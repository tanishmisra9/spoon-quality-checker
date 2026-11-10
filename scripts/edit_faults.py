"""Ask gpt-image-2 to ADD a realistic camera fault to a real, fault-free web photo -> data/<pack>/edited/ed_NNNN.png + edited.csv.
An independent fault source for blur/glare: the fault comes from a image model, not from scripts/degrade.py, so tuning on the simulator
cannot inflate the score on these. Bases are CC Wikimedia photos only (re-encoded, no metadata); the user's phone photos are never uploaded.
Usage: uv run scripts/edit_faults.py --n 6   (pilot)    uv run scripts/edit_faults.py --n 60"""
import argparse, base64, csv, io, random, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import cv2
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA, ROOT  # noqa: E402
from scripts.evaluate import load  # noqa: E402

OUT = DATA / "edited"
PROMPTS = {
    "glare": "Edit this photo so a strong camera flash or window reflection blows out a large part of the spoon: a big, soft, saturated white "
             "hotspot with a glowing halo that hides the metal's detail. Keep everything else, the framing and the sharpness identical. It must still look like a real photo.",
    "defocus": "Edit this photo so the whole image is strongly out of focus, as if shot with a missed autofocus: apply a heavy lens blur so the spoon's outline is a soft smudge "
               "and no engraving, texture or crisp edge survives anywhere. Keep framing, lighting and colours identical. It must still look like a real photo.",
    "shake": "Edit this photo so it shows strong camera shake: the whole picture, spoon included, is smeared along one direction as in a slow-shutter handheld shot. "
             "Keep framing, lighting and colours identical. It must still look like a real photo.",
    "glare_mid": "Edit this photo so a camera-flash reflection makes one moderate bright hotspot on the spoon: washed out and glowing, covering roughly a tenth of the spoon, with a soft halo. "
                 "Keep everything else identical. It must still look like a real photo.",
    "glare_bloom": "Edit this photo as if it was taken with a phone flash: a hot white spot with a bloom halo on the spoon's surface, details washed out around it, the rest of the scene a little darker. "
                   "Keep framing identical. It must still look like a real photo.",
    "glare_window": "Edit this photo so a big soft window reflection spreads across the spoon's bowl, a smooth near-white patch hiding the metal's detail. Keep framing identical. It must still look like a real photo.",
    "shine": "Edit this photo to add a few small, sharp, natural specular sparkles on the polished spoon, as normal shiny metal has. Do not wash out any area. Keep everything else identical. It must still look like a real photo.",
}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--kinds", default="glare,defocus,shake")
    a = ap.parse_args()
    n = a.n
    load_dotenv(ROOT / ".env")
    client = OpenAI()
    items, *_ = load()
    bases = sorted([it for it in items if it["split"] in ("pool", "sealed") and it["clean"] and it["tier"] == "opus"], key=lambda i: i["id"])
    rng = random.Random(7); rng.shuffle(bases)
    OUT.mkdir(exist_ok=True)
    meta = DATA / "edited.csv"; new = not meta.exists()
    start = len(list(OUT.glob("ed_*.png")))
    kinds = a.kinds.split(",")
    jobs = [(f"ed_{start + i:04d}", bases[(start + 7 * i + 3) % len(bases)], kinds[(start + i) % len(kinds)]) for i in range(n)]

    def run(j):
        iid, base, kind = j
        img = cv2.imread(base["path"]); h, w = img.shape[:2]; s = 1024 / max(h, w)
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
        buf = ("base.jpg", io.BytesIO(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()), "image/jpeg")
        r = client.images.edit(model="gpt-image-2", image=buf, prompt=PROMPTS[kind], quality="medium", size="auto")
        (OUT / f"{iid}.png").write_bytes(base64.b64decode(r.data[0].b64_json))
        return iid, base["id"], kind

    with meta.open("a", newline="") as f, ThreadPoolExecutor(6) as ex:
        w = csv.writer(f)
        if new: w.writerow(["image_id", "base_id", "intended_fault", "model"])
        for iid, bid, kind in ex.map(run, jobs):
            w.writerow([iid, bid, kind, "gpt-image-2"]); f.flush(); print(iid, bid, kind, flush=True)

if __name__ == "__main__":
    main()
