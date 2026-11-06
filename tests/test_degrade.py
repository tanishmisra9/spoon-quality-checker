import cv2
import numpy as np

from backend.checks import check
from scripts.degrade import variants
from tests.test_checks import scene

RECT = (0.05, 0.05, 0.95, 0.95)


def by_name(img):
    return {(n, sev): v for n, sev, _, v in variants(img, RECT, 0, min_sev=2, downscale=False, controls=True)}


def test_object_only_defocus_is_caught_as_blur_but_background_stays_sharp():
    base = scene()
    v = by_name(base)[("defocus_object", 3)]
    assert "blur" in check(v)["failed"], check(v)["scores"]
    assert not np.array_equal(v, base)


def test_dark_backdrop_control_is_not_called_dark():
    """The original wrong-advice case: a well-lit spoon on a black backdrop. Exposure is the brighter of the object and the frame."""
    v = by_name(scene(fg=190, bg=70))[("ctrl_dark_bg", 0)]
    r = check(v)
    assert "dark" not in r["failed"], r["scores"]


def test_truly_underexposed_photo_is_still_dark():
    assert "dark" in check((scene() * 0.2).astype(np.uint8))["failed"]


def test_controls_are_severity_zero_and_expected_to_pass_dark_and_bright():
    for (name, sev), v in by_name(scene(fg=170, bg=100)).items():
        if name.startswith("ctrl_"):
            assert sev == 0
            f = check(v)["failed"]
            assert "dark" not in f, (name, f)
