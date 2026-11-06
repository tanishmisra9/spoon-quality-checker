"""Ingest a folder of the user's own phone photos (HEIC/JPEG, possibly in sub-folders per person).
Stages (run in this order; each is safe to re-run):
  manifest  record every file (folder, device, time, size, ISO, shutter, flash, scene, sha256) in data/<pack>/phone_manifest.csv. Moves nothing. No GPS is stored.
  flatten   move every file up into SRC, verify sha256 against the manifest, rmdir the emptied sub-folders, optionally rename SRC (--rename spoons)
  convert   sips -> data/<pack>/phone/ph_NNNN.jpg (long edge <= 2048, JPEG q92) and append rows to sources.csv
Usage: uv run scripts/ingest_phone.py manifest ~/Downloads/spoon
       uv run scripts/ingest_phone.py flatten  ~/Downloads/spoon --rename spoons
       uv run scripts/ingest_phone.py convert  ~/Downloads/spoons [--limit 1]
Scenes: photos are clustered by capture date and ~1 km GPS cell; only the scene id (s1, s2, ...) is stored, never coordinates.
Reversal: the manifest keeps each file's original sub-folder, so `flatten` can be undone by moving files back."""
import argparse
import csv
import hashlib
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA  # noqa: E402

MANIFEST = DATA / "phone_manifest.csv"
OUT = DATA / "phone"
EXTS = {".heic", ".jpeg", ".jpg", ".png"}
FIELDS = ["new_id", "orig_folder", "filename", "device", "captured", "width", "height", "iso", "shutter_s", "flash", "scene", "sha256"]


def mdls(path):
    """Spotlight metadata by attribute name. (mdls -raw returns values in alphabetical attribute order, not request order,
    so parse the 'key = value' form; '(null)' becomes ''.)"""
    out = subprocess.run(["mdls", str(path)], capture_output=True, text=True).stdout
    meta = {}
    for line in out.splitlines():
        m = re.match(r"(kMDItem\w+)\s*=\s*(.*)", line)
        if m:
            v = m.group(2).strip().strip('"')
            meta[m.group(1)] = "" if v == "(null)" else v
    return {k: meta.get(k, "") for k in ("kMDItemAcquisitionModel", "kMDItemContentCreationDate", "kMDItemPixelWidth", "kMDItemPixelHeight", "kMDItemISOSpeed",
                                         "kMDItemExposureTimeSeconds", "kMDItemFlashOnOff", "kMDItemLatitude", "kMDItemLongitude")}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files_under(src):
    return sorted(p for p in Path(src).expanduser().rglob("*") if p.is_file() and p.suffix.lower() in EXTS)


def build_manifest(src):
    src = Path(src).expanduser()
    files = files_under(src)
    names = [p.name.lower() for p in files]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        sys.exit(f"filename collisions across folders, refusing to flatten: {sorted(dup)[:5]}")
    with ThreadPoolExecutor(8) as ex:
        meta = list(ex.map(mdls, files))
        hashes = list(ex.map(sha256, files))
    # scene = capture date + ~1 km GPS cell, numbered by first appearance. Coordinates are used to cluster and then discarded.
    cells, rows = {}, []
    for i, (p, m, h) in enumerate(zip(files, meta, hashes)):
        try:
            cell = (m["kMDItemContentCreationDate"][:10], round(float(m["kMDItemLatitude"]), 2), round(float(m["kMDItemLongitude"]), 2))
        except ValueError:  # no GPS: the date alone defines the scene
            cell = (m["kMDItemContentCreationDate"][:10], None, None)
        scene = cells.setdefault(cell, f"s{len(cells) + 1}")
        rows.append({"new_id": f"ph_{i:04d}", "orig_folder": p.parent.name if p.parent != src else "", "filename": p.name, "device": m["kMDItemAcquisitionModel"],
                     "captured": m["kMDItemContentCreationDate"], "width": m["kMDItemPixelWidth"], "height": m["kMDItemPixelHeight"], "iso": m["kMDItemISOSpeed"],
                     "shutter_s": m["kMDItemExposureTimeSeconds"], "flash": m["kMDItemFlashOnOff"], "scene": scene, "sha256": h})
    DATA.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"manifest: {len(rows)} files, {len(cells)} scenes -> {MANIFEST}")
    return rows


def load_manifest():
    return list(csv.DictReader(MANIFEST.open()))


def flatten(src, rename):
    src = Path(src).expanduser()
    rows = load_manifest()
    before = len(files_under(src))
    for r in rows:
        cur = src / r["orig_folder"] / r["filename"] if r["orig_folder"] else src / r["filename"]
        dest = src / r["filename"]
        if cur != dest:
            if dest.exists():
                sys.exit(f"refusing to overwrite {dest}")
            shutil.move(str(cur), str(dest))
    after = files_under(src)
    assert len(after) == before == len(rows), f"count mismatch: before {before}, after {len(after)}, manifest {len(rows)}"
    bad = [r["filename"] for r in rows if sha256(src / r["filename"]) != r["sha256"]]
    assert not bad, f"sha256 changed for {bad[:3]}"
    print(f"flattened {len(rows)} files into {src}; sha256 verified")
    for d in sorted({r["orig_folder"] for r in rows if r["orig_folder"]}):
        sub = src / d
        for junk in sub.glob(".DS_Store"):
            junk.unlink()
        sub.rmdir()  # raises if anything is left: it cannot delete data
        print("removed empty folder", sub.name)
    if rename and src.name != rename:
        target = src.parent / rename
        if target.exists():
            sys.exit(f"{target} already exists; not renaming")
        src.rename(target)
        print(f"renamed {src.name} -> {rename}")


def convert(src, limit=None):
    src = Path(src).expanduser()
    rows = load_manifest()[:limit]
    OUT.mkdir(parents=True, exist_ok=True)

    def one(r):
        out = OUT / f"{r['new_id']}.jpg"
        if not out.exists():
            subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", "-Z", "2048", str(src / r["filename"]), "--out", str(out)],
                           capture_output=True, check=True)
        return out

    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(one, rows))
    print(f"converted {len(outs)} -> {OUT}")
    sources = DATA / "sources.csv"
    have = {r["image_id"] for r in csv.DictReader(sources.open())} if sources.exists() else set()
    with sources.open("a", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            if r["new_id"] not in have:
                w.writerow([r["new_id"], "phone", r["filename"], f"phone/{r['new_id']}.jpg", r["orig_folder"] or "owner", "own (contributed)", "phone", ""])
    return outs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["manifest", "flatten", "convert"])
    ap.add_argument("src")
    ap.add_argument("--rename")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    {"manifest": lambda: build_manifest(a.src), "flatten": lambda: flatten(a.src, a.rename), "convert": lambda: convert(a.src, a.limit)}[a.stage]()
