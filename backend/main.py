import csv
import json
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from backend.checks import check
from config import GUIDE_RECT, OBJECT, TITLE, VIEWS

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "data" / "sessions"
MAX_BYTES = 15 * 1024 * 1024

app = FastAPI()


def decode(data: bytes) -> np.ndarray:
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Image too large")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "Not a readable image")
    return img


@app.get("/api/views")
def views():
    return VIEWS


@app.get("/api/info")
def info():
    return {"object": OBJECT, "title": TITLE, "guide": GUIDE_RECT}


@app.post("/api/check")
async def check_image(image: UploadFile = File(...)):
    return check(decode(await image.read()))


@app.post("/api/session")
async def save_session(
    images: list[UploadFile] = File(...),
    view_ids: str = Form(...),  # JSON list, same order as images
    client: str = Form(""),  # JSON: user agent, camera resolution reported by the browser
):
    ids = json.loads(view_ids)
    if len(ids) != len(images) or not set(ids) <= {v["id"] for v in VIEWS}:
        raise HTTPException(400, "view_ids must match images and known views")
    sid = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    d = SESSIONS / sid
    d.mkdir(parents=True)
    rows = []
    for view, up in zip(ids, images):
        raw = await up.read()
        res = check(decode(raw))  # re-score server side; never trust the client's verdict
        (d / f"{view}.jpg").write_bytes(raw)
        rows.append({"view_id": view, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **res})
    meta = {"session_id": sid, "client": client, "images": rows}
    (d / "session.json").write_text(json.dumps(meta, indent=2))
    return {"session_id": sid}
