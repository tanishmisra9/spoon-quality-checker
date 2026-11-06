import cv2
import numpy as np

from backend.roi import object_mask
from config import GUIDE_RECT, LEGACY_THRESHOLDS, MESSAGES, NORM_WIDTH, THRESHOLDS


WORK_EDGE = 1280  # nothing below needs more: metrics run at NORM_WIDTH (640) and segmentation at 320, so big phone photos only cost time


def shrink(img_bgr: np.ndarray) -> np.ndarray:
    """Downscale so the long edge is at most WORK_EDGE (INTER_AREA). Used by score() and by the evaluator before it fabricates faults."""
    h, w = img_bgr.shape[:2]
    if max(h, w) <= WORK_EDGE:
        return img_bgr
    k = WORK_EDGE / max(h, w)
    return cv2.resize(img_bgr, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)


def _largest_blob(binary):
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    return int(stats[1:, cv2.CC_STAT_AREA].max()) if n > 1 else 0


def score(img_bgr: np.ndarray, rect=GUIDE_RECT) -> dict:
    """Metrics for one BGR image. Plain keys are whole-frame (the legacy view); roi_* keys are measured only on the
    object's pixels, found by GrabCut inside `rect` (roi_found=False when no plausible object is there)."""
    h, w = img_bgr.shape[:2]
    min_side = min(h, w)  # resolution is judged on the photo as delivered, before any downscaling for speed
    img_bgr = shrink(img_bgr)
    h, w = img_bgr.shape[:2]
    small = cv2.resize(img_bgr, (NORM_WIDTH, round(h * NORM_WIDTH / w)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sat = gray >= 250
    s = {
        "min_side_px": min_side,
        "blur": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "mean_lum": float(gray.mean()),
        "bright_frac": float(sat.mean()),
        "contrast_std": float(gray.std()),
        "glare_frac": float(_largest_blob(sat) / gray.size),
        "roi_found": False,
    }
    # Mask-independent exposure cues: the mask-based ones can erase a blown-out object, since object and backdrop then share a colour.
    ncc, cc = cv2.connectedComponents(sat.astype(np.uint8), connectivity=8)
    edge_labels = np.unique(np.concatenate([cc[0], cc[-1], cc[:, 0], cc[:, -1]]))
    interior_sat = sat & ~np.isin(cc, edge_labels[edge_labels > 0])  # saturated areas touching the frame border are backdrop, not highlights
    s["frame_glare_int"] = float(_largest_blob(interior_sat) / gray.size)
    s["frame_p05"] = float(np.percentile(gray, 5))     # overexposed = even the darkest content is washed out
    s["frame_p95"] = float(np.percentile(gray, 95))  # exposure from the brightest content: a dark-coloured object on a bright backdrop is not underexposed
    mask = object_mask(img_bgr, rect)
    if mask is None:
        return s
    m = cv2.resize(mask.astype(np.uint8), (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
    px = gray[m]
    if px.size < 200:
        return s
    # Robust statistics on purpose: GrabCut sometimes leaks into shadow or background, and a mean/variance over the
    # whole mask would then measure the leak. Percentiles of what is on the object survive that.
    bh, bw = gray.shape
    box = np.zeros((bh, bw), bool)
    box[int(rect[1] * bh):round(rect[3] * bh), int(rect[0] * bw):round(rect[2] * bw)] = True
    bu_sat = interior_sat & box
    bu_area = max(1, int((m | bu_sat).sum()))
    ring_mask = cv2.dilate(m.astype(np.uint8), np.ones((25, 25), np.uint8)).astype(bool) & ~cv2.dilate(m.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_64F))
    near = cv2.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)  # include the silhouette edge
    s.update({
        "roi_found": True,
        "roi_area_frac": float(m.mean()),
        "roi_blur": float(np.percentile(lap[near], 95)),       # sharpest edges of the object
        "roi_mean_lum": float(np.median(px)),                  # median ignores a small shadow/background leak
        "roi_bright_frac": float((px >= 250).mean()),
        "roi_contrast_std": float(np.percentile(px, 95) - np.percentile(px, 5)),
        "roi_contrast": float(np.percentile(px, 95) - np.percentile(px, 5) + abs(np.median(px) - np.median(gray[ring_mask])) if ring_mask.any() else np.percentile(px, 95) - np.percentile(px, 5)),  # tonal range of the object + how far it stands out from its surroundings
        "roi_p5": float(np.percentile(px, 5)),
        "roi_p95": float(np.percentile(px, 95)),
        "roi_bright_u": float(bu_sat.sum() / bu_area),   # blown-out share of the object, counting blown pixels the backdrop-stripper removed
        "roi_glare_u": float(_largest_blob(bu_sat) / bu_area),
        "roi_bright_int": float((interior_sat & m).sum() / m.sum()),
        "roi_glare_int": float(_largest_blob(interior_sat & m) / m.sum()),
        "roi_ring_diff": float(abs(np.median(px) - np.median(gray[ring_mask]))) if ring_mask.any() else 0.0,
        "roi_glare_frac": float(_largest_blob(m & sat) / m.sum()),
    })
    return s


def verdict(s: dict, t: dict | None = None, mode: str = "roi") -> dict:
    """mode='roi': object-scoped checks ('no_object' when the object can't be found).
    mode='legacy': the original whole-frame checks, kept as the before/after comparison."""
    failed = []
    k = None
    if mode == "legacy":
        t = t or LEGACY_THRESHOLDS
        k = dict(blur=s["blur"], lum=s["mean_lum"], bright=s["bright_frac"], contrast=s["contrast_std"], glare=s["glare_frac"])
    else:
        t = t or THRESHOLDS
        if s["roi_found"]:
            # Chosen by grouped cross-validation (scripts/evaluate.py CHECKS): exposure is the brighter of the object and the frame (a lit spoon on a black backdrop is not underexposed; a dark spoon on a bright backdrop is not either) and glare is frame-level because
            # object masks can erase a blown-out object or include a dark-coloured object's backdrop.
            k = dict(blur=s["roi_blur"], lum=max(s["roi_p95"], s["frame_p95"]), bright=s["roi_bright_u"],
                     contrast=s["roi_contrast"], glare=s["frame_glare_int"])
        else:
            failed.append("no_object")
    if s["min_side_px"] < t["min_side_px"]:
        failed.append("resolution")
    if k:
        if k["blur"] < t["blur_min"]:
            failed.append("blur")
        if k["lum"] < t["dark_max_mean"]:
            failed.append("dark")
        if k["bright"] > t["bright_max_frac"]:
            failed.append("bright")
        if k["contrast"] < t["contrast_min_std"]:
            failed.append("contrast")
        if k["glare"] > t["glare_max_frac"]:
            failed.append("glare")
    return {"passed": not failed, "failed": failed, "reasons": [MESSAGES[f] for f in failed]}


def check(img_bgr: np.ndarray) -> dict:
    s = score(img_bgr)
    return {"scores": s, **verdict(s), "legacy": verdict(s, mode="legacy"), "object_found": s["roi_found"]}
