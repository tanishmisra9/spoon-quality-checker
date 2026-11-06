"""Grid of images with ids, for eye-review. Writes <out>/sheet_N.jpg (20 per sheet).
Usage: uv run scripts/contact_sheet.py [image_dir] [--out DIR]   (default: data/<pack>/raw)"""
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("image_dir", nargs="?", default=str(DATA / "raw"))
ap.add_argument("--out", default=str(DATA / "contact_sheets"))
a = ap.parse_args()
files = sorted(p for p in Path(a.image_dir).iterdir() if p.suffix.lower() in (".jpg", ".png"))
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
T, COLS = 256, 5
for s in range(0, len(files), 20):
    tiles = []
    for f in files[s:s + 20]:
        img = cv2.resize(cv2.imread(str(f)), (T, T))
        cv2.putText(img, f.stem[-5:], (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        tiles.append(img)
    while len(tiles) % COLS:
        tiles.append(np.zeros((T, T, 3), np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + COLS]) for i in range(0, len(tiles), COLS)])
    cv2.imwrite(str(out / f"sheet_{s // 20}.jpg"), grid)
print("sheets in", out)
