import cv2
import numpy as np

from backend.checks import check


def scene(fg=150, bg=90, w=1200, h=1200, seed=0):
    """A spoon-shaped object (bowl + handle, shaded, fine surface texture) on a textured background.
    fg/bg are mean grey levels, so we can build white-on-white, dark-on-slate and so on."""
    rng = np.random.default_rng(seed)

    def tex(mean, amp):  # blocky texture so Laplacian energy is high on a sharp image
        t = rng.integers(-amp, amp + 1, (h // 12, w // 12)).astype(np.float32)
        return mean + cv2.resize(t, (w, h), interpolation=cv2.INTER_NEAREST)

    img = tex(bg, 14)
    shape = np.zeros((h, w), np.uint8)
    cv2.ellipse(shape, (600, 420), (190, 270), 0, 0, 360, 255, -1)       # bowl
    cv2.rectangle(shape, (570, 650), (630, 1080), 255, -1)                # handle
    yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    shade = 1 + 0.12 * yy                                                 # gentle vertical shading
    obj = tex(fg, 12) * shade
    img = np.where(shape > 0, obj, img)
    out = np.clip(img, 0, 255).astype(np.uint8)
    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def test_clean_passes():
    r = check(scene())
    assert r["passed"], r


def test_blur_fails():
    r = check(cv2.GaussianBlur(scene(), (0, 0), 14))
    assert "blur" in r["failed"], r["scores"]


def test_dark_fails():
    r = check((scene(fg=150, bg=90) * 0.2).astype(np.uint8))
    assert "dark" in r["failed"], r["scores"]


def test_overexposed_fails():
    r = check(np.clip(scene(fg=200, bg=120).astype(int) + 90, 0, 255).astype(np.uint8))
    assert "bright" in r["failed"], r["scores"]


def test_low_contrast_fails():
    img = (scene().astype(np.float32) * 0.08 + 118).astype(np.uint8)
    assert "contrast" in check(img)["failed"], check(img)["scores"]


def test_glare_fails():
    img = scene()
    cv2.circle(img, (600, 400), 110, (255, 255, 255), -1)  # saturated hotspot on the bowl
    assert "glare" in check(img)["failed"], check(img)["scores"]


def test_small_fails():
    assert "resolution" in check(cv2.resize(scene(), (400, 400)))["failed"]


def test_no_object_in_empty_frame():
    import time
    t = time.time()
    r = check(np.full((1000, 1000, 3), 128, np.uint8))
    assert "no_object" in r["failed"] and not r["object_found"]
    assert time.time() - t < 3, "a flat frame once made GrabCut take 30+ s (found by driving the UI)"


# The reason this revamp exists: whole-frame checks give wrong advice when the BACKGROUND (or a pale object) is extreme.
def test_dark_background_is_not_a_dark_spoon():
    r = check(scene(fg=170, bg=18))  # well-lit spoon on a slate-black backdrop
    assert "dark" in r["legacy"]["failed"], "legacy should wrongly flag this (the bug we are fixing)"
    assert "dark" not in r["failed"], r["scores"]


def test_pale_spoon_on_pale_background_is_not_overexposed():
    r = check(scene(fg=225, bg=200))  # white ceramic-ish spoon on a light table, detail intact
    assert "bright" not in r["failed"], r["scores"]
