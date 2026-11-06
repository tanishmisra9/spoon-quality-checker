"""Pull CC-licensed photos from Wikimedia Commons -> data/<pack>/raw/*.jpg + data/<pack>/sources.csv.
Usage: uv run scripts/scrape.py --n 30            (pilot)
       uv run scripts/scrape.py --n 1500
       uv run scripts/scrape.py --from-sources    (re-download every row already in sources.csv)
Only CC / public-domain files are kept; licence, creator and page URL are stored per image.
Resumable: rows already in sources.csv are skipped. Add another source = another function returning the same dicts."""
import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
import json
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA, PACK_CFG  # noqa: E402

RAW = DATA / "raw"
SOURCES = DATA / "sources.csv"
API = "https://commons.wikimedia.org/w/api.php"
UA = "image-identifier-poc/0.1 (research; contact via repo owner)"
MAX_PAGES = 8  # per query: the head of a huge result list is full of photographers we already have
# 960 is a standard Wikimedia thumbnail step that is usually already cached: 1280 triggered on-demand thumbnail generation and frequent 429s.
# Earlier photos (img_00000-00859) were fetched at 1280; metrics are measured at 640px wide so this does not change scores.
MIN_SIDE, MAX_EDGE, DELAY, DL_DELAY, WORKERS, BATCH = 800, 960, 1.0, 0.4, 4, 16
DL_MIN_SIDE = 600  # shortest side of the downloaded 960px thumbnail (MIN_SIDE still applies to the original)
FIELDS = ["image_id", "source", "page", "url", "creator", "licence", "query", "dhash"]
OK_LICENCE = re.compile(r"^(cc|public domain|pd|attribution)", re.I)


def get(url, binary=False, delay=None, timeout=30):
    for attempt in range(4):
        time.sleep(DELAY if delay is None else delay)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            wait = min(60, int(e.headers.get("Retry-After") or 10) * (attempt + 1))  # capped: an uncapped Retry-After once stalled a run for minutes
            print(f"429, waiting {wait}s", flush=True)
            time.sleep(wait)


def dhash(img):
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA)
    return int("".join("1" if b else "0" for b in (g[:, 1:] > g[:, :-1]).flatten()), 2)


def strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def wikimedia(query, limit=50):
    """Yield candidate dicts for one search query (bitmap files only, thumbnail URL capped at MAX_EDGE)."""
    offset = 0
    for _ in range(MAX_PAGES):
        p = {"action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
             "gsrlimit": limit, "gsroffset": offset, "prop": "imageinfo", "iiprop": "url|size|extmetadata",
             "iiurlwidth": MAX_EDGE, "format": "json"}
        d = get(API + "?" + urllib.parse.urlencode(p))
        for pg in (d.get("query", {}).get("pages", {})).values():
            ii = (pg.get("imageinfo") or [None])[0]
            if not ii:
                continue
            m = ii.get("extmetadata", {})
            lic = (m.get("LicenseShortName", {}).get("value") or "").strip()
            if not OK_LICENCE.match(lic):
                continue
            if min(ii["width"], ii["height"]) < MIN_SIDE:
                continue
            yield {"source": "wikimedia", "page": ii["descriptionurl"], "url": ii.get("thumburl") or ii["url"],
                   "creator": strip_html(m.get("Artist", {}).get("value")) or "unknown", "licence": lic, "query": query}
        if "continue" not in d:
            return
        offset = d["continue"]["gsroffset"]


def openverse_stream(qs, limit=20, workers=3):
    """Openverse (Flickr and other CC sources): direct image URLs on other hosts, so its rate limit is independent of Wikimedia's.
    Each search call takes 10-30 s, so (query, page) calls run concurrently; page-major order puts the first page of every query first."""
    pairs = [(q, pg) for pg in range(1, MAX_PAGES + 1) for q in qs]

    def call(qp):
        q, pg = qp
        url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode({"q": q, "page": pg, "page_size": limit, "license_type": "all-cc", "mature": "false"})
        for _ in range(2):
            try:
                d = get(url, delay=0.2, timeout=90)
                break
            except urllib.error.HTTPError as e:
                if e.code in (400, 404):  # past the last page
                    return []
                raise
            except Exception:  # timeout: the API is slow, not down
                d = None
        if d is None:
            return []
        out = []
        for r in d.get("results", []):
            if (r.get("width") and r.get("height")) and min(r["width"], r["height"]) < MIN_SIDE:
                continue
            out.append({"source": "openverse", "page": r.get("foreign_landing_url") or r["url"], "url": r["url"],
                        "creator": (r.get("creator") or "unknown").strip(),
                        "licence": f"CC {r['license'].upper()} {r.get('license_version') or ''}".strip(), "query": q})
        return out

    ex = ThreadPoolExecutor(workers)
    try:
        for res in ex.map(call, pairs):
            yield from res
    finally:
        ex.shutdown(wait=False, cancel_futures=True)  # stop searching once we have enough photos




def queries():
    cfg = PACK_CFG["scrape"]
    return [f"{q} {e}".strip() for e in cfg["environments"] for q in cfg["queries"]]


def batched(it, n):
    buf = []
    for x in it:
        buf.append(x)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def load_rows():
    return list(csv.DictReader(SOURCES.open())) if SOURCES.exists() else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--source", choices=["wikimedia", "openverse"], default="wikimedia")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--from-sources", action="store_true")
    ap.add_argument("--skip-seen-queries", action="store_true", help="skip search queries that already produced photos in sources.csv (go straight to new ones)")
    ap.add_argument("--max-per-creator", type=int, default=0, help="skip candidates whose photographer already has this many images (0 = no cap); favours new photographers")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    seen_urls = {r["url"] for r in rows}
    per_creator = {}
    for r in rows:
        per_creator[r["creator"]] = per_creator.get(r["creator"], 0) + 1
    hashes = [int(r["dhash"]) for r in rows if r["dhash"]]
    if a.from_sources:
        for r in rows:
            f = RAW / f"{r['image_id']}.jpg"
            if not f.exists():
                f.write_bytes(get(r["url"], binary=True))
        print("restored", len(rows))
        return
    new = not SOURCES.exists()
    nxt = len(rows)
    added = 0
    with SOURCES.open("a", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        if new:
            w.writeheader()
        done_q = {r["query"] for r in rows} if a.skip_seen_queries else set()
        if a.source == "openverse":
            streams = iter([("openverse", openverse_stream(PACK_CFG["scrape"]["queries"]))])
        else:
            streams = ((q, wikimedia(q)) for q in queries() if q not in done_q)
        for q, stream in streams:
            if added >= a.n:
                break
            try:
                for batch in batched(stream, BATCH):
                    if added >= a.n:
                        break
                    batch = [c for c in batch if c["url"] not in seen_urls
                             and not (a.max_per_creator and per_creator.get(c["creator"], 0) >= a.max_per_creator)]
                    seen_urls.update(c["url"] for c in batch)

                    def fetch(c):
                        try:
                            return c, get(c["url"], binary=True, delay=DL_DELAY)
                        except Exception as e:  # one bad download must not kill a long run
                            print("skip", c["url"][:80], e)
                            return c, None

                    with ThreadPoolExecutor(a.workers) as ex:
                        fetched = list(ex.map(fetch, batch))
                    for c, data in fetched:
                        if added >= a.n or data is None:
                            continue
                        if a.max_per_creator and per_creator.get(c["creator"], 0) >= a.max_per_creator:
                            continue  # the batch may have held several from one photographer
                        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                        if img is None or min(img.shape[:2]) < DL_MIN_SIDE:
                            continue
                        h = dhash(img)
                        if any(bin(h ^ o).count("1") <= 4 for o in hashes):  # near-duplicate
                            continue
                        hashes.append(h)
                        per_creator[c["creator"]] = per_creator.get(c["creator"], 0) + 1
                        iid = f"img_{nxt:05d}"
                        nxt += 1
                        cv2.imwrite(str(RAW / f"{iid}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
                        w.writerow({"image_id": iid, **{k: c[k] for k in ("source", "page", "url", "creator", "licence", "query")}, "dhash": h})
                        fh.flush()
                        added += 1
                        print(iid, c["licence"], "|", c["query"], flush=True)
            except Exception as e:
                print("query failed", q, e)
    print(f"added {added}, total {nxt}")


if __name__ == "__main__":
    main()
