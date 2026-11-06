"""Generate varied METALLIC object photos with gpt-image-2 -> data/<pack>/generated/gen_NNNN.png + generated.csv.
Unlike the first version (6 spoons x 4 plain surfaces x 3 lights, always centred and clean) this crosses finishes x scenes x views
x a fault_style axis (asks the generator for blur, flash glare, under-exposure directly instead of faking it with cv2 afterwards).
Everything object-specific comes from packs/<name>.toml. The intended fault is metadata only; the vision label is the label.
Generated images are for tuning/stress only, never the test set.
Usage: uv run scripts/generate_images.py --n 6      (pilot: check cost and realism first)
       uv run scripts/generate_images.py --n 120"""
import argparse
import base64
import csv
import itertools
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA, NOUN, PACK_CFG, ROOT, VIEWS  # noqa: E402

OUT = DATA / "generated"
MODEL = "gpt-image-2"
WORKERS = 8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    n = ap.parse_args().n
    load_dotenv(ROOT / ".env")
    client = OpenAI()
    OUT.mkdir(parents=True, exist_ok=True)
    meta = DATA / "generated.csv"
    new = not meta.exists()
    start = len(list(OUT.glob("gen_*.png")))
    rng = random.Random(start)
    g = PACK_CFG["generate"]
    styles = list(g["fault_styles"])
    views = itertools.cycle(VIEWS)  # even spread across views
    style_cycle = itertools.cycle(rng.sample(styles, len(styles)))  # and across fault styles
    jobs = []
    for i in range(start, start + n):
        view, style = next(views), next(style_cycle)
        finish, scene = rng.choice(g["finishes"]), rng.choice(g["scenes"])
        prompt = f"A {g['fault_styles'][style]} phone photo of a {finish} {NOUN} {scene}, {view['prompt']}."
        jobs.append((f"gen_{i:04d}", view["id"], style, finish, scene, prompt))

    def gen(job):
        r = client.images.generate(model=MODEL, prompt=job[-1], size="1024x1024", quality="medium")
        (OUT / f"{job[0]}.png").write_bytes(base64.b64decode(r.data[0].b64_json))
        return job

    with meta.open("a", newline="") as f, ThreadPoolExecutor(WORKERS) as ex:
        w = csv.writer(f)
        if new:
            w.writerow(["image_id", "view_id", "intended_fault", "finish", "scene", "model", "prompt"])
        for job in ex.map(gen, jobs):  # map yields in order; a failure raises here
            w.writerow([*job[:5], MODEL, job[5]])
            f.flush()
            print(*job[:3], flush=True)


if __name__ == "__main__":
    main()
