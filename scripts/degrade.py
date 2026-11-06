"""Phone-camera-style faults for stress-testing and tuning: variants(img, rect, seed) yields
(degradation, severity, expected_failing, image). Severity 1 is borderline (reported, not scored as right/wrong); 2-3 are clear.
Replaces the v1 primitives (gaussian blur, 1-D motion kernel, additive brightness, white-circle glare):
- defocus uses a disc PSF, shake a random curved-path PSF
- low light works in linear light with shot + read noise, then JPEG-recompresses
- overexposure is gain in linear light with hard clipping (what a sensor does), severity = share of the object's pixels blown out
- glare is a specular hotspot placed on the OBJECT (needs the object mask), not a circle anywhere
- haze is veiling light, the physical cause of washed-out / low-contrast phone photos
These are still simulations. They are used for tuning and stress tests and reported as synthetic; they are never the headline.
Run `uv run scripts/degrade.py <image>` to dump a contact sheet of every fault on one image for eyeballing."""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.roi import object_mask  # noqa: E402


def _long(img):
    return max(img.shape[:2])


def _conv(img, ker):
    return cv2.filter2D(img, -1, ker / ker.sum(), borderType=cv2.BORDER_REFLECT)


def defocus(img, frac, rng):
    r = max(1, round(frac * _long(img)))
    ker = np.zeros((2 * r + 1, 2 * r + 1), np.float32)
    cv2.circle(ker, (r, r), r, 1.0, -1)
    return _conv(img, ker)


def shake(img, frac, rng):
    L = max(5, round(frac * _long(img)))
    ker = np.zeros((2 * L + 1, 2 * L + 1), np.float32)
    pts = np.cumsum(rng.normal(0, 1, (6, 2)), axis=0)  # a hand-drawn wobble, not a straight line
    pts = (pts - pts[0]) / (np.abs(pts - pts[0]).max() + 1e-6) * L * rng.choice([-1, 1], 2)
    cv2.polylines(ker, [np.round(pts + L).astype(np.int32)], False, 1.0, 1, cv2.LINE_AA)
    return _conv(img, ker)


def _lin(img):
    return (img.astype(np.float32) / 255.0) ** 2.2


def _unlin(x):
    return (np.clip(x, 0, 1) ** (1 / 2.2) * 255.0).astype(np.uint8)


def lowlight(img, gain, rng):
    x = _lin(img) * gain
    x = x + rng.normal(0, 1, x.shape).astype(np.float32) * np.sqrt(x * 4e-3 + 4e-5)  # shot + read noise
    return jpeg(_unlin(x), 75)


def overexpose(img, lost, rng, m):
    """Gain in linear light with hard clipping (what a sensor does), set per image so that `lost` of the object's pixels end up
    blown out. Severity is "how much of the object is gone", not a fixed gain: a fixed gain barely clips a mid-tone spoon but
    wipes out a pale one, so the same severity label meant different faults on different photos."""
    lin = _lin(img)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    ref = g[m] if m is not None else g.ravel()
    gain = 1.0 / max(float(np.percentile(ref, 100 * (1 - lost))) ** 2.2, 1e-3)
    return _unlin(lin * max(gain, 1.0))


def haze(img, a, rng):
    return (img.astype(np.float32) * (1 - a) + 225 * a).astype(np.uint8)


def jpeg(img, q):
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], cv2.IMREAD_COLOR)


def glare(img, area_frac, rng, m):
    """Specular hotspot centred on the object mask `m`. Returns None if no object was found (nothing realistic to place)."""
    if m is None:
        return None
    core = cv2.erode(m.astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)  # stay off the silhouette edge
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ok = core & (gray >= np.median(gray[m]))  # hotspots form on the brighter, reflective parts, not in shadow
    ys, xs = np.nonzero(ok if ok.any() else m)
    i = rng.integers(len(ys))
    cy, cx = int(ys[i]), int(xs[i])
    area = area_frac * m.sum()
    ry = max(3.0, np.sqrt(area / np.pi) * rng.uniform(0.6, 1.6))
    rx = max(3.0, area / (np.pi * ry))
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2
    a = np.clip(1.6 * np.exp(-d * 1.2), 0, 1) * m.astype(np.float32)  # saturated core, soft skirt, only on the object
    a = a[..., None]
    return (img.astype(np.float32) * (1 - a) + 255 * a).astype(np.uint8)


def _feather(mask, long_edge):
    return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), max(2.0, 0.004 * long_edge))[..., None]


def defocus_object(img, frac, rng, mask):
    """Focus missed the spoon but the background stayed sharp (a real phone failure): blur only inside the object mask."""
    fm = _feather(mask, _long(img))
    return (defocus(img, frac, rng) * fm + img * (1 - fm)).astype(np.uint8)


def control(img, name, mask, rng):
    """Negative controls: photos that look 'extreme' to a whole-frame check but have a good, sharp, well-lit spoon. Expected verdict: PASS."""
    fm = _feather(mask, _long(img))
    f = img.astype(np.float32)
    if name == "ctrl_bg_blur":      # shallow depth of field: sharp spoon, blurred background
        bg = cv2.GaussianBlur(img, (0, 0), 0.012 * _long(img)).astype(np.float32)
    elif name == "ctrl_dark_bg":    # black backdrop, lit spoon
        bg = f * 0.12
    elif name == "ctrl_white_bg":   # high-key white backdrop
        bg = f * 0.15 + 250 * 0.85
    else:
        raise ValueError(name)
    return (f * fm + bg * (1 - fm)).astype(np.uint8)


def downscale_img(img, side):
    h, w = img.shape[:2]
    s = side / min(h, w)
    return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


def variants(img, rect, seed, min_sev=1, downscale=True, controls=False):
    """Yield (degradation, severity, expected_failing, image) for one clean base image.
    controls=True also yields the severity-0 negative controls. min_sev=2 skips the borderline severity-1 variants and downscale=False skips the resolution-only ones: tuning and recall
    never read them, and each one costs a GrabCut when scored. The clean photo is segmented once and shared by glare/overexposure."""
    rng = np.random.default_rng(seed)
    yield "clean", 0, "", img
    mask = object_mask(img, rect)
    spec = [
        ("defocus", defocus, [0.004, 0.010, 0.022], "blur"),
        ("shake", shake, [0.008, 0.022, 0.05], "blur"),
        ("lowlight", lowlight, [0.25, 0.08, 0.025], "dark"),
        ("overexposed", lambda im, p, r: overexpose(im, p, r, mask), [0.10, 0.30, 0.60], "bright"),
        ("haze", haze, [0.35, 0.6, 0.8], "contrast"),
    ]
    for name, fn, params, expect in spec:
        for sev, p in enumerate(params, 1):
            if sev >= min_sev:
                yield name, sev, expect, fn(img, p, rng)
    for sev, p in enumerate([0.03, 0.10, 0.25], 1):
        if sev >= min_sev:
            g = glare(img, p, rng, mask)
            if g is not None:
                yield "glare", sev, "glare", g
    if downscale:
        for sev, p in enumerate([760, 500, 250], 1):
            if sev >= min_sev:
                yield "downscale", sev, "resolution", downscale_img(img, p)
    yield "defocus_lowlight", 3, "blur+dark", lowlight(defocus(img, 0.010, rng), 0.12, rng)
    if mask is not None:
        for sev, p in enumerate([0.004, 0.010, 0.022], 1):
            if sev >= min_sev:
                yield "defocus_object", sev, "blur", defocus_object(img, p, rng, mask)
        if controls:  # severity 0, expected to pass; reported separately, never part of tuning unless a control fails its pass-rate rule
            for name in ("ctrl_bg_blur", "ctrl_dark_bg", "ctrl_white_bg"):
                yield name, 0, "", control(img, name, mask, rng)
            shine = img
            for _ in range(4):  # normal specular shine on polished metal: four small spots, ~2.4% of the object in total
                g = glare(shine, 0.006, rng, mask)
                shine = shine if g is None else g
            yield "ctrl_shine", 0, "", shine


if __name__ == "__main__":
    img = cv2.imread(sys.argv[1])
    rect = (0.05, 0.05, 0.95, 0.95)
    tiles = []
    for name, sev, _, v in variants(img, rect, 0):
        v = cv2.resize(v, (256, 256))
        cv2.putText(v, f"{name} {sev}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        tiles.append(v)
    while len(tiles) % 6:
        tiles.append(np.zeros((256, 256, 3), np.uint8))
    out = sys.argv[2] if len(sys.argv) > 2 else "degrade_sheet.jpg"
    cv2.imwrite(out, np.vstack([np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)]))
    print("wrote", out)
