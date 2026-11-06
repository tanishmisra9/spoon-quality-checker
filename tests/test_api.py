import cv2
from fastapi.testclient import TestClient

from backend.main import app
from tests.test_checks import scene

c = TestClient(app)


def jpg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_check_pass_and_fail():
    ok = c.post("/api/check", files={"image": ("a.jpg", jpg(scene()), "image/jpeg")}).json()
    assert ok["passed"] and ok["object_found"], ok
    bad = c.post("/api/check", files={"image": ("a.jpg", jpg(cv2.GaussianBlur(scene(), (0, 0), 14)), "image/jpeg")}).json()
    assert not bad["passed"] and "Blurry, hold steady" in bad["reasons"]
    assert "legacy" in bad  # old whole-frame verdict rides along for the before/after line


def test_info_matches_pack():
    info = c.get("/api/info").json()
    assert info["object"] and len(info["guide"]) == 4


def test_rejects_garbage():
    assert c.post("/api/check", files={"image": ("a.jpg", b"not an image", "image/jpeg")}).status_code == 400
