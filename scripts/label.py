"""Vision-label scraped/generated images with Claude -> data/<pack>/labels*.jsonl (cached per image+model).
Usage: uv run scripts/label.py --limit 30                          (Haiku pilot; needs ANTHROPIC_API_KEY in .env)
       uv run scripts/label.py                                     (all unlabeled images in raw/ and generated/)
       uv run scripts/label.py --model claude-opus-5-5 --sample 100 --out labels_audit.jsonl   (audit a sample)
The schema is built from the pack's faults, so a new object only needs a new packs/<name>.toml.
Labels exist for offline tuning/evaluation only; the runtime app never calls a model."""
import argparse
import base64
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

import cv2
from dotenv import load_dotenv
from pydantic import BaseModel, create_model

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA, OBJECT, PACK_CFG, ROOT, VIEWS  # noqa: E402

DEFAULT_MODEL = "claude-haiku-5-5"
PRICE = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.0, 20.0)}  # $/MTok in, out (cached skill table, 2026-10-06)
MAX_EDGE = 1568  # larger images are downscaled server-side anyway
WORKERS = 6
FAULTS = [f for f, v in PACK_CFG["faults"].items() if v.get("labeled", True)]  # resolution / no_object are computed, never labeled


class FaultLabel(BaseModel):
    present: bool
    severity: int  # 0 none, 1 mild, 2 clear, 3 severe (range checked in code: structured outputs don't enforce min/max)
    where: Literal["object", "background", "both", "none"]


Faults = create_model("Faults", **{f: (FaultLabel, ...) for f in FAULTS})
View = Literal[tuple([v["id"] for v in VIEWS] + ["none"])]  # type: ignore[valid-type]


class Label(BaseModel):
    contains_object: bool
    metallic: bool
    object_bbox: list[float]  # [x0, y0, x1, y1] normalized 0-1 of the main object; [] if absent
    view: View  # type: ignore[valid-type]
    faults: Faults  # type: ignore[valid-type]
    retake: bool


def prompt():
    fdefs = "\n".join(f"- {f}: {PACK_CFG['faults'][f]['definition']}" for f in FAULTS)
    views = "\n".join(f"- {v['id']}: {v['label']}" for v in VIEWS)
    return (
        f"You are labeling a photo for an image-quality checker. The target object is a {OBJECT}.\n"
        "1. contains_object: is a real, physical target object clearly the subject? (drawings, paintings and unrelated scenes: false)\n"
        "2. metallic: is the object made of metal?\n"
        "3. object_bbox: normalized [x0,y0,x1,y1] box around the target object (x right, y down, 0-1). [] if absent.\n"
        f"4. view: which requested view does the photo show, or 'none'?\n{views}\n"
        "5. For each fault below judge the OBJECT's pixels, not the background. severity 0 none, 1 mild, 2 clear, 3 severe; "
        "where = object, background, both, or none. If the object is absent, mark every fault absent with severity 0.\n"
        f"{fdefs}\n"
        "6. retake: would a careful person retake this photo because of a fault on the object?\n"
        "Be literal and consistent; do not invent faults."
    )


def check_model(client, model):
    """Fail fast if the model is retired/unknown, and warn if a newer model of the same family (claude-opus, claude-haiku, ...) exists."""
    client.models.retrieve(model)  # raises NotFoundError for a retired or misspelled id
    family = "-".join(model.split("-")[:2])
    newer = [m.id for m in client.models.list(limit=100) if m.id.startswith(family + "-") and m.created_at > client.models.retrieve(model).created_at]
    if newer:
        print(f"WARNING: newer {family} model(s) available: {newer}; {model} may be superseded", flush=True)


def encode(path):
    img = cv2.imread(str(path))
    h, w = img.shape[:2]
    s = min(1.0, MAX_EDGE / max(h, w))
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    return base64.standard_b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()).decode()


def label_one(client, model, path):
    r = client.messages.parse(
        model=model, max_tokens=1500, output_format=Label, output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encode(path)}},
            {"type": "text", "text": prompt()}]}],
    )
    lab = r.parsed_output
    if lab is None:
        raise ValueError(f"no parsed output (stop_reason={r.stop_reason})")
    d = lab.model_dump()
    for f in FAULTS:  # structured outputs don't enforce numeric ranges
        d["faults"][f]["severity"] = max(0, min(3, d["faults"][f]["severity"]))
    return d, r.usage.input_tokens, r.usage.output_tokens


def images():
    return sorted(p for sub in ("raw", "generated", "phone", "edited") if (DATA / sub).exists() for p in (DATA / sub).glob("*.[jp][pn]g"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", type=int, help="random sample of already-labeled images (for auditing with a stronger model)")
    ap.add_argument("--ids-file", help="only label the image ids listed in this file (one per line)")
    ap.add_argument("--gate", action="store_true", help="with --sample: only images the main labels say contain a metallic target object")
    ap.add_argument("--out", default="labels.jsonl")
    ap.add_argument("--max-cost", type=float, default=5.0, help="stop when estimated spend exceeds this many dollars")
    a = ap.parse_args()
    load_dotenv(ROOT / ".env")
    import anthropic
    client = anthropic.Anthropic(timeout=120.0, max_retries=2)  # a stalled request must fail, not hang the run
    check_model(client, a.model)
    out = DATA / a.out
    done = {json.loads(l)["image_id"] for l in out.read_text().splitlines()} if out.exists() else set()
    todo = images()
    if a.sample:  # audit mode: draw from images the main label file already covers
        rows = [json.loads(l) for l in (DATA / "labels.jsonl").read_text().splitlines()]
        base = {r["image_id"] for r in rows if not a.gate or (r["contains_object"] and r["metallic"])}
        todo = [p for p in todo if p.stem in base and p.stem not in done]  # exclude already-audited before sampling
        random.Random(0).shuffle(todo)
        todo = todo[:a.sample]
    todo = [p for p in todo if p.stem not in done]
    if a.ids_file:
        want = set(Path(a.ids_file).read_text().split())
        todo = [p for p in todo if p.stem in want]
    if a.limit:
        todo = todo[:a.limit]
    pin, pout = PRICE.get(a.model, (0, 0))
    spent = 0.0
    n = 0
    with out.open("a") as f, ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(lambda p: (p, *_safe(client, a.model, p)), p) for p in todo]
        for fut in as_completed(futs):  # write as they finish: one slow request must not block the rest
            p, d, tin, tout = fut.result()
            if d is None:
                print("FAILED", p.name, tin, flush=True)
                continue
            spent += (tin * pin + tout * pout) / 1e6
            f.write(json.dumps({"image_id": p.stem, "model": a.model, **d}) + "\n")
            f.flush()
            n += 1
            print(f"{p.stem} obj={d['contains_object']} metal={d['metallic']} view={d['view']} "
                  f"faults={[k for k, v in d['faults'].items() if v['present']]}  ${spent:.3f}", flush=True)
            if spent > a.max_cost:
                print(f"stopping: estimated spend ${spent:.2f} > --max-cost")
                for f_ in futs:
                    f_.cancel()
                break
    print(f"labeled {n}/{len(todo)} with {a.model}, est. ${spent:.3f}")


def _safe(client, model, path):
    try:
        return label_one(client, model, path)
    except Exception as e:  # one bad image must not stop a long run
        return None, repr(e)[:200], 0


if __name__ == "__main__":
    main()
