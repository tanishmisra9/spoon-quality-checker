# Views, messages and thresholds come from packs/$PACK.toml (default: spoon). Thresholds are DRAFTS until
# scripts/evaluate.py tunes them and writes data/<pack>/thresholds.json.
import json as _json
import os as _os
import tomllib as _tomllib
from pathlib import Path as _P

ROOT = _P(__file__).parent
PACK = _os.environ.get("PACK", "spoon")
PACK_CFG = _tomllib.loads((ROOT / "packs" / f"{PACK}.toml").read_text())
DATA = ROOT / "data" / PACK  # per-pack artifacts: raw/, labels.jsonl, results.md, thresholds.json

VIEWS = PACK_CFG["views"]
OBJECT = PACK_CFG["object"]
NOUN = PACK_CFG.get("noun", OBJECT)
TITLE = PACK_CFG.get("title", f"Capture the {OBJECT}")
MESSAGES = {k: v["message"] for k, v in PACK_CFG["faults"].items()}
# On-screen guide box (normalized x0,y0,x1,y1). Runtime object search is seeded by it; the frontend draws the same box.
GUIDE_RECT = tuple(PACK_CFG.get("guide_rect", [0.1, 0.1, 0.9, 0.9]))

NORM_WIDTH = 640  # all scoring happens at this width so blur scores are size-independent

THRESHOLDS = {
    "min_side_px": 800,        # fail if min(h, w) below
    "blur_min": 12.0,          # fail if p95 |Laplacian| on the object below (draft until evaluate.py tunes it)
    "edge_min": 0.0,           # fail if the object's edge-sharpness ratio is below (0 = off until evaluate.py tunes it)
    "dark_max_mean": 100.0,    # fail if the frame's 95th-percentile luminance is below (underexposed: nothing bright anywhere)
    "bright_max_frac": 0.15,   # fail if fraction of pixels >= 250 above
    "contrast_min_std": 25.0,  # fail if (object p95-p5 spread + its separation from its surroundings) is below
    "hot_max_frac": 1.0,       # fail glare if the largest on-object saturated blob (discounted when speckled) / frame area is above (1.0 = off until tuned)
    "glare_max_frac": 0.01,    # fail if the largest interior saturated blob / frame area is above
}

DRAFT_THRESHOLDS = dict(THRESHOLDS)  # untuned defaults; tests pin these so they don't depend on the last evaluation run

# Frozen v1 thresholds, tuned on synthetic whole-image data. Kept only as the "before" in the old-vs-new comparison.
_legacy = ROOT / "data" / "thresholds.json"
LEGACY_THRESHOLDS = {**THRESHOLDS, **(_json.loads(_legacy.read_text()) if _legacy.exists() else {})}

# scripts/evaluate.py writes tuned values here; they override the drafts above.
_tuned = DATA / "thresholds.json"
if _tuned.exists():
    THRESHOLDS.update(_json.loads(_tuned.read_text()))
