"""Find the object's pixels inside a box. Classical CV only: no model at runtime, no object-specific code.
GrabCut seeded by a rectangle (the on-screen guide box at runtime; the labeler's bbox in evaluation)."""
import os

import cv2
import numpy as np

GC_WIDTH = 320          # GrabCut runs on a downscaled copy; the mask is scaled back up
MIN_FRAC, MAX_FRAC = 0.02, 0.95  # plausible object area as a fraction of the rect; outside this we say "not found"
MIN_STD = 1.0                     # a box with less pixel variation than this contains no object
FULL_FRAME = 0.85                 # rect covering this much of the frame = "object fills the frame"


def _tighten(small, m, min_keep=0.3):
    """GrabCut on a pale-on-pale or soft-shadow scene can swallow the backdrop. If the photo has a plain border,
    keep only mask pixels whose colour differs from it (Otsu on the distance). Skipped when that would eat the object."""
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    b = max(2, small.shape[0] // 20)
    ring = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3), lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(ring, axis=0)
    if (np.linalg.norm(ring - bg, axis=1) < 15).mean() < 0.6:  # no dominant border colour (scale bars/rulers are tolerated): distance cue is meaningless
        return m
    d = np.linalg.norm(lab - bg, axis=2)
    d8 = np.clip(d * 4, 0, 255).astype(np.uint8)
    thr, _ = cv2.threshold(d8[m].reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    keep = m & (d8 > thr * 0.5)
    keep = cv2.morphologyEx(keep.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(bool)
    return keep if keep.sum() >= min_keep * m.sum() else m


def object_mask(img_bgr: np.ndarray, rect: tuple[float, float, float, float]):
    """rect = normalized (x0, y0, x1, y1). Returns a bool mask the size of img_bgr, or None if no plausible object."""
    h, w = img_bgr.shape[:2]
    if (rect[2] - rect[0]) * (rect[3] - rect[1]) >= FULL_FRAME:
        # The box is (nearly) the whole frame, i.e. the labeler says the object fills it: there is nothing for GrabCut to
        # separate, and the right answer is the box itself. (The runtime guide box is smaller, so it never takes this path.)
        s = GC_WIDTH / w
        small = cv2.resize(img_bgr, (GC_WIDTH, round(h * s)), interpolation=cv2.INTER_AREA)
        sh, sw = small.shape[:2]
        m = np.zeros((sh, sw), bool)
        m[int(rect[1] * sh):round(rect[3] * sh), int(rect[0] * sw):round(rect[2] * sw)] = True
        # ...but a big box often surrounds several objects on a plain backdrop (catalogue shots): drop the backdrop pixels.
        m = _tighten(small, m, min_keep=0.02)  # thin objects in a big frame are a small share of it
        return cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
    s = GC_WIDTH / w
    small = cv2.resize(img_bgr, (GC_WIDTH, round(h * s)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    x0, y0, x1, y1 = (int(rect[0] * sw), int(rect[1] * sh), int(rect[2] * sw), int(rect[3] * sh))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    if small[y0:y1, x0:x1].std(axis=(0, 1)).max() < MIN_STD:
        return None  # blank wall / lens cap: nothing to segment, and GrabCut's colour models degenerate (it took 30+ s on a flat frame)
    # GrabCut needs background pixels outside the rect, and asserts when the rect spans the whole frame (close-ups).
    # Pad with the border colour so there are always background seeds, then crop back.
    pad = max(6, int(0.08 * max(sh, sw)))
    ring = np.concatenate([small[:3].reshape(-1, 3), small[-3:].reshape(-1, 3), small[:, :3].reshape(-1, 3), small[:, -3:].reshape(-1, 3)])
    padded = cv2.copyMakeBorder(small, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=[int(v) for v in np.median(ring, axis=0)])
    gc = np.zeros(padded.shape[:2], np.uint8)
    cv2.setRNGSeed(int(os.environ.get("GRABCUT_SEED", 0)))  # GrabCut's colour-model init is randomised; seeding makes the same photo give the same mask every time
    try:
        cv2.grabCut(padded, gc, (x0 + pad, y0 + pad, x1 - x0, y1 - y0), np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 3, cv2.GC_INIT_WITH_RECT)
    except cv2.error:
        return None
    gc = gc[pad:-pad, pad:-pad]
    fg = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    if n < 2:
        m = np.zeros((sh, sw), bool)
    else:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))  # one object: keep the largest blob
        m = _tighten(small, lab == big)
    frac = m.sum() / max(1, (x1 - x0) * (y1 - y0))
    if not (MIN_FRAC <= frac <= MAX_FRAC):
        # GrabCut gave nothing plausible (thin object, tiny object, busy scene) but the box has structure: treat the box content,
        # minus a plain backdrop, as the object. Only a flat box (MIN_STD above) means "nothing there".
        m = np.zeros((sh, sw), bool)
        m[y0:y1, x0:x1] = True
        m = _tighten(small, m, min_keep=0.02)
    return cv2.resize(m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(bool)
