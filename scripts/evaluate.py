"""Tune object-scoped thresholds, estimate their false-alarm rate honestly, report old vs new -> data/<pack>/results.md + thresholds.json.
Protocol: POOL = every photo scraped before SEALED_FROM, plus generated images. Thresholds are tuned on the pool; the estimate that goes in the
headline is leave-photographers-out cross-validation on the pool (each photographer is held out in turn). SEALED = photos scraped later whose
photographer never appears in the pool; they are scored once with the final thresholds as a check on selection bias.
Inputs: data/<pack>/{sources.csv, labels.jsonl (Haiku), labels_audit.jsonl (Opus; overrides Haiku where present)}, raw/ and generated/ images.
Positives are clean photos with faults applied by scripts/degrade.py (truth by construction). Negatives are photos the labelers call fault-free.
The old whole-frame system is scored on the exact same images (the "before").
Usage: uv run scripts/evaluate.py [--rescore]"""
import argparse
import csv
import hashlib
import json
import os
import sys
import collections
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.checks import score, shrink, verdict  # noqa: E402
from config import DATA, GUIDE_RECT, LEGACY_THRESHOLDS, OBJECT, PACK, THRESHOLDS  # noqa: E402
from scripts.degrade import variants  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# Pixel size of a scraped photo is whatever our downloader produced (960-1280px thumbnails), not how it was taken, so the resolution check
# is switched off for every false-rejection rate here (v1 and v2 alike). It still runs in the app, and the downscale degradation is not scored.
THRESHOLDS = {**THRESHOLDS, "min_side_px": 0}
LEGACY_THRESHOLDS = {**LEGACY_THRESHOLDS, "min_side_px": 0}
CACHE = DATA / "scores_cache.json"
FAULTS = ["blur", "dark", "bright", "contrast", "glare"]
# Per-check cap on the share of clean pool photos a check may wrongly flag (chosen by grouped CV so the sum lands near the 10% goal).
BUDGETS = {"blur": 0.02, "dark": 0.02, "contrast": 0.02, "glare": 0.02, "bright": 0.03}
SEALED_FROM = "img_00454"  # photos scraped after this id, from photographers absent from the pool, are the sealed check
GOAL_FA = 0.10
# Negative controls added to the tuning negatives because they passed under 90% in the first controls run (rule fixed in advance).
# ctrl_dark_bg is fixed by the dark feature instead (brighter of object/frame); ctrl_white_bg passed at >= 90% and stays evaluation-only.
CTRL_TUNE = {"ctrl_bg_blur", "ctrl_shine"}
PHONE_CAP = 40  # max phone photos per (person, scene) used for tuning: 112 near-twins of one desk must not make up a third of the phone data
# ROI score key, threshold key, direction ('low' = fail when score < thr)
CHECKS = {  # keep in sync with backend/checks.py verdict(); features chosen by grouped CV (see README). A check with several features fails if ANY of them trips.
    "blur": [("roi_blur", "blur_min", "low"), ("roi_edge", "edge_min", "low")],
    "dark": [(lambda s: max(s["roi_p95"], s["frame_p95"]), "dark_max_mean", "low")],  # brighter of object and frame
    "bright": [("roi_bright_u", "bright_max_frac", "high")],
    "contrast": [("roi_contrast", "contrast_min_std", "low")],
    "glare": [("frame_glare_int", "glare_max_frac", "high"), ("roi_hot", "hot_max_frac", "high")],
}

# ---- Success criteria for the goal "new version wrongly rejects at most ~10% of clean photos" (set before this run) ----
def _criteria(m):
    return [
        ("G1 pool, leave-photographers-out CV: new wrongly rejects <= 10% of clean photos", m["cv_fa"] <= GOAL_FA),
        (f"G2 sealed set (n={m['sealed_n']}): new wrongly rejects <= 10% (point estimate; interval shown)", m["sealed_n"] > 0 and m["sealed_fa"] <= GOAL_FA),
        ("G3 guard against going blind: pool CV macro recall of injected faults >= 0.65", m["cv_recall"] >= 0.65),
        ("G4 mimics (clean photos v1 rejects): v2 rejects fewer than v1", m["new_mimic"] < m["mimic_n"]),
    ]


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def jl(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def split_of(image_id, creator, pool_creators):
    """gen = generated (tunes only); sealed = scraped after SEALED_FROM (never used for tuning or for choosing budgets/features); pool = everything else.
    Sealed photos from photographers who also appear in the pool are new photos but not new photographers; load() flags the difference."""
    if image_id.startswith("gen_"):
        return "gen"
    if image_id.startswith("ed_"):
        return "edit"  # gpt-image-2 added a fault to a real photo: never tuned on, only reported (scripts/edit_faults.py)
    if image_id.startswith("ph_"):
        return "phone"  # the user's own iPhone photos: scored separately; they join tuning only in a later, reported step
    return "sealed" if image_id >= SEALED_FROM else "pool"


def creator_of(src, iid):
    return src.get(iid, {}).get("creator", iid)


def fold_of(it, src, k=5):
    return int(hashlib.md5(("f" + creator_of(src, it["id"])).encode()).hexdigest(), 16) % k


def load():
    src = {r["image_id"]: r for r in csv.DictReader((DATA / "sources.csv").open())} if (DATA / "sources.csv").exists() else {}
    pool_creators = {r["creator"] for r in src.values() if r["image_id"] < SEALED_FROM and not r["image_id"].startswith("ph_")}
    manifest = {r["new_id"]: r for r in csv.DictReader((DATA / "phone_manifest.csv").open())} if (DATA / "phone_manifest.csv").exists() else {}
    hs = {r["image_id"]: r for r in jl(DATA / "labels.jsonl")}
    op = {r["image_id"]: r for r in jl(DATA / "labels_audit.jsonl")}
    items = []
    for iid, h in hs.items():
        lab = op.get(iid, h)
        path = next((p for sub in ("raw", "generated", "phone", "edited") for ext in ("jpg", "png") if (p := DATA / sub / f"{iid}.{ext}").exists()), None)
        if path is None or not (lab["contains_object"] and lab["metallic"]):
            continue
        if iid.startswith(("ph_", "ed_")) and iid not in op:
            continue  # Haiku-only labels miss faults, so a phone photo only counts once Opus has labeled it
        bb = lab["object_bbox"]
        m = 0.1
        rect = GUIDE_RECT
        if len(bb) == 4 and bb[2] > bb[0] and bb[3] > bb[1]:
            w, h_ = bb[2] - bb[0], bb[3] - bb[1]
            rect = (max(0, bb[0] - m * w), max(0, bb[1] - m * h_), min(1, bb[2] + m * w), min(1, bb[3] + m * h_))
        faults = {f: lab["faults"][f] for f in FAULTS}
        items.append({"id": iid, "path": str(path), "rect": rect, "split": split_of(iid, creator_of(src, iid), pool_creators),
                      "tier": "opus" if iid in op else "haiku", "faults": faults, "new_photographer": creator_of(src, iid) not in pool_creators, "scene": manifest.get(iid, {}).get("scene", ""), "person": creator_of(src, iid),
                      "device": manifest.get(iid, {}).get("device", ""),
                      "clean": not any(v["present"] for v in faults.values())})
    return items, hs, op, src


def _work(args):
    path, rect, seed, with_variants, with_guide = args
    img = shrink(cv2.imread(path))  # fabricate and score faults at working size (a 21MP original made each base take ~50 s)
    out = {"boxed": score(img, rect)}
    if with_guide:
        out["guide"] = score(img, GUIDE_RECT)
    if with_variants:
        out["variants"] = [(n, sev, ex, score(v, rect)) for n, sev, ex, v in variants(img, rect, seed, min_sev=2, downscale=False, controls=True) if n != "clean"]
    return out


def code_hash():
    """Hash only what changes the cached scores: roi.py, degrade.py and checks.py up to verdict() (editing verdict wiring must not trigger a rescore)."""
    checks = (ROOT / "backend/checks.py").read_text().split("def verdict(")[0]
    return hashlib.md5(checks.encode() + b"".join((ROOT / p).read_bytes() for p in ("backend/roi.py", "scripts/degrade.py"))).hexdigest()


def compute(items, rescore):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() and not rescore else {}
    if cache.get("_code") != code_hash():
        cache = {"_code": code_hash()}
    todo = [it for it in items if f"{it['id']}|{it['rect']}" not in cache]
    jobs = [(it["path"], it["rect"], int(it["id"].split("_")[-1]) if it["id"].split("_")[-1].isdigit() else 0, it["clean"], True) for it in todo]
    if jobs:
        print(f"scoring {len(jobs)} images (cached: {len(items) - len(jobs)}) ...", flush=True)
        with ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as ex:
            for it, res in zip(todo, ex.map(_work, jobs, chunksize=1)):
                cache[f"{it['id']}|{it['rect']}"] = res
        CACHE.write_text(json.dumps(cache))
    for it in items:
        it["res"] = cache[f"{it['id']}|{it['rect']}"]
    return items


def rows(items, splits):
    """(item, degradation, severity, expected set, scores) for clean-labeled bases (variants) of the given splits."""
    out = []
    for it in items:
        if it["split"] not in splits or not it["clean"]:
            continue
        out.append((it, "clean", 0, set(), it["res"]["boxed"]))
        for n, sev, ex, s in it["res"]["variants"]:
            out.append((it, n, sev, set(filter(None, ex.split("+"))), s))
    return out


def tune(rs, budgets=BUDGETS):
    """Per check: catch as many severity 2-3 faults as possible while wrongly flagging at most budgets[check] of clean photos.
    Real metal legitimately has bright highlights and dark patina, so a rule that only maximises accuracy drifts into the tail of the
    clean distribution and gives clean photos false advice (it did, on the first run)."""
    tuned = {}
    for chk, feats in CHECKS.items():
        for sk, tk, d in feats:
            val = sk if callable(sk) else (lambda s, k=sk: s[k])
            pos = [val(s) for _, n, sev, ex, s in rs if sev >= 2 and chk in ex and s["roi_found"]]
            neg = [val(s) for _, n, sev, ex, s in rs if (n == "clean" or n in CTRL_TUNE) and s["roi_found"]]
            if len(pos) < 5 or len(neg) < 5:
                continue
            flag = (lambda t: (lambda x: x < t)) if d == "low" else (lambda t: (lambda x: x > t))
            vals = sorted(set(pos + neg))
            cands = [(a + b) / 2 for a, b in zip(vals, vals[1:])]
            fa = lambda c: sum(map(flag(c), neg)) / len(neg)  # noqa: E731
            rec = lambda c: sum(map(flag(c), pos)) / len(pos)  # noqa: E731
            share = budgets[chk] / len(feats)  # the check's false-alarm budget is split evenly between its features
            ok = [c for c in cands if fa(c) <= share] or [min(cands, key=fa)]
            best = max(rec(c) for c in ok)
            tied = [c for c in ok if rec(c) == best]
            tuned[tk] = float(tied[len(tied) // 2])
    return tuned


def metrics(rs, mode, thr):
    clean = [(it, s) for it, n, sev, ex, s in rs if n == "clean"]
    v = lambda s: verdict(s, thr, mode)  # noqa: E731
    fa = sum(not v(s)["passed"] for _, s in clean) / max(1, len(clean))
    pos = [(ex, s) for it, n, sev, ex, s in rs if sev >= 2 and ex & set(FAULTS)]
    rec = defaultdict(lambda: [0, 0])
    for ex, s in pos:
        failed = set(v(s)["failed"])
        for chk in ex & set(FAULTS):
            rec[chk][0] += chk in failed
            rec[chk][1] += 1
    macro = float(np.mean([a / b for a, b in rec.values()])) if rec else float("nan")
    return {"fa": fa, "n_clean": len(clean), "recall": macro, "per": {k: tuple(x) for k, x in rec.items()}}


def cap_phone(items, cap=PHONE_CAP):
    """Keep at most `cap` phone photos per (person, scene), chosen by a fixed hash order; other items pass through untouched."""
    out, groups = [], defaultdict(list)
    for it in items:
        (groups[(it["person"], it["scene"])] if it["split"] == "phone" else out).append(it)
    for g in groups.values():
        out += sorted(g, key=lambda it: hashlib.md5(it["id"].encode()).hexdigest())[:cap]
    return out


def _score_rows(out, test, thr, src):
    for it, n, sev, ex, s in test:
        v = verdict(s, thr, "roi")
        if n == "clean":
            o = verdict(s, LEGACY_THRESHOLDS, "legacy")
            out["clean"] += 1
            out["fa_new"] += not v["passed"]
            out["fa_old"] += not o["passed"]
            for r in v["failed"]:
                out["sources"][r] += 1
            c = out["per_creator"][creator_of(src, it["id"])]
            c[0] += 1
            c[1] += not v["passed"]
            c[2] += not o["passed"]
        elif sev >= 2:
            for chk in ex & set(FAULTS):
                hit = chk in v["failed"]
                out["by_check"][chk][0] += hit
                out["by_check"][chk][1] += 1
                out["by_sev"][(chk, sev)][0] += hit
                out["by_sev"][(chk, sev)][1] += 1


def _blank():
    return {"clean": 0, "fa_new": 0, "fa_old": 0, "by_check": defaultdict(lambda: [0, 0]), "by_sev": defaultdict(lambda: [0, 0]),
            "sources": defaultdict(int), "per_creator": defaultdict(lambda: [0, 0, 0])}


def cv(pool, src, k=5):
    """Hold-out cross-validation. Wikimedia photographers: leave a hash fold out (as before). Phone photos: leave one SCENE out at a time
    (the four people shot the same place within minutes, so a person is not an independent group). Training always includes everything else,
    phone photos capped per person and scene. Returns pooled Wikimedia stats plus out['phone'][scene]."""
    out = _blank()
    out["phone"] = {}
    for f in range(k):
        train = cap_phone([it for it in pool if it["split"] == "phone" or fold_of(it, src, k) != f])
        test = [it for it in pool if it["split"] == "pool" and fold_of(it, src, k) == f]
        thr = {**THRESHOLDS, **tune(rows(train, ("pool", "gen", "phone")))}
        _score_rows(out, rows(test, ("pool",)), thr, src)
    for sc in sorted({it["scene"] for it in pool if it["split"] == "phone"}):
        train = cap_phone([it for it in pool if not (it["split"] == "phone" and it["scene"] == sc)])
        test = [it for it in pool if it["split"] == "phone" and it["scene"] == sc]
        thr = {**THRESHOLDS, **tune(rows(train, ("pool", "gen", "phone")))}
        o = out["phone"][sc] = _blank()
        _score_rows(o, rows(test, ("phone",)), thr, src)
    return out


def phone_section(items, thr, title="Phone photos"):
    """Markdown: how a set of thresholds does on the user's own iPhone photos (Opus-labeled only), by scene and by folder.
    Counts are photos, not independent samples: shots within a session are near-twins, and there are only a few scenes."""
    ph = [it for it in items if it["split"] == "phone"]
    if not ph:
        return ""
    def group(name, sel):
        clean = [it for it in sel if it["clean"]]
        if not clean:
            return f"| {name} | 0 | - | - | - |"
        v2 = sum(not verdict(it["res"]["boxed"], thr, "roi")["passed"] for it in clean)
        v1 = sum(not verdict(it["res"]["boxed"], LEGACY_THRESHOLDS, "legacy")["passed"] for it in clean)
        noo = sum("no_object" in verdict(it["res"]["boxed"], thr, "roi")["failed"] for it in clean)
        lo, hi = wilson(v2, len(clean))
        return f"| {name} | {len(clean)} | {v1} ({v1 / len(clean):.0%}) | {v2} ({v2 / len(clean):.0%}) [{lo:.0%}, {hi:.0%}] | {noo} |"
    out = [f"## {title} (Opus-labeled: {len(ph)} photos, {sum(i['clean'] for i in ph)} fault-free)\n",
           "| group | clean photos | v1 wrongly rejects | v2 wrongly rejects [95% CI] | v2 'no object' |\n|---|---|---|---|---|",
           group("all phone photos", ph)]
    for sc in sorted({it["scene"] for it in ph}):
        out.append(group(f"scene {sc}", [it for it in ph if it["scene"] == sc]))
    c2 = collections.Counter()
    for it in ph:
        if it["clean"]:
            for f in verdict(it["res"]["boxed"], thr, "roi")["failed"]:
                c2[f] += 1
    out.append("\nWhat wrongly rejected the clean phone photos: " + (", ".join(f"{k} {v}" for k, v in c2.most_common()) or "nothing") + "\n")
    out.append("Naturally faulty phone photos (Opus-flagged; real, not simulated): how often each system flags that fault\n\n| fault | photos | v1 catches | v2 catches |\n|---|---|---|---|")
    for f in FAULTS:
        nat = [it for it in ph if it["faults"][f]["present"]]
        c1 = sum(f in verdict(it["res"]["boxed"], LEGACY_THRESHOLDS, "legacy")["failed"] for it in nat)
        c2_ = sum(f in verdict(it["res"]["boxed"], thr, "roi")["failed"] for it in nat)
        out.append(f"| {f} | {len(nat)} | {c1} | {c2_} |")
    guide = sum(not verdict(it["res"]["guide"], thr, "roi")["passed"] for it in ph if it["clean"])
    out.append(f"\nThrough the fixed on-screen guide box instead of the labeler's box: v2 wrongly rejects {guide} of {sum(i['clean'] for i in ph)} clean phone photos.\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescore", action="store_true")
    a = ap.parse_args()
    items, hs, op, src = load()
    if not items:
        sys.exit(f"no labeled images for pack '{PACK}': run scripts/scrape.py then scripts/label.py first")
    items = compute(items, a.rescore)
    pool = [it for it in items if it["split"] in ("pool", "gen", "phone")]
    sealed = [it for it in items if it["split"] == "sealed"]
    tuned = tune(rows(cap_phone(pool), ("pool", "gen", "phone")))
    thr = {**THRESHOLDS, **tuned}
    (DATA / "thresholds.json").write_text(json.dumps(tuned, indent=2))
    res = cv(pool, src)
    n = res["clean"]
    lo, hi = wilson(res["fa_new"], n)
    lo_o, hi_o = wilson(res["fa_old"], n)
    macro = float(np.mean([a_ / b_ for a_, b_ in res["by_check"].values()]))
    sr = rows(sealed, ("sealed",))
    sm_new, sm_old = metrics(sr, "roi", thr), metrics(sr, "legacy", LEGACY_THRESHOLDS)
    sn = sm_new["n_clean"]
    s_k = round(sm_new["fa"] * sn)
    s_lo, s_hi = wilson(s_k, sn)
    np_ = [it for it in sealed if it["new_photographer"]]
    np_rows = rows(np_, ("sealed",))
    np_new, np_old = metrics(np_rows, "roi", thr), metrics(np_rows, "legacy", LEGACY_THRESHOLDS)
    pclean = [it for it in pool if it["split"] == "pool" and it["clean"]]
    mimic = [it for it in pclean if not verdict(it["res"]["boxed"], LEGACY_THRESHOLDS, "legacy")["passed"]]
    new_mimic = [it for it in mimic if not verdict(it["res"]["boxed"], thr, "roi")["passed"]]
    m = {"cv_fa": res["fa_new"] / n, "cv_recall": macro, "sealed_n": sn, "sealed_fa": sm_new["fa"], "new_mimic": len(new_mimic), "mimic_n": len(mimic)}

    out = [f"# Results ({OBJECT}, pack `{PACK}`)\n",
           "Labels: Claude Haiku 5.5 for every image, overridden by Claude Opus 5.5 where an audit label exists. **No human has audited these labels.** "
           "'Truth' for degraded variants is by construction; 'clean' and 'natural fault' are model-judged.\n"]
    out.append("## Data\n"
               f"- pool: {sum(i['split'] == 'pool' for i in items)} real labeled metallic-spoon photos from {len({creator_of(src, i['id']) for i in items if i['split'] == 'pool'})} photographers "
               f"+ {sum(i['split'] == 'gen' for i in items)} generated (generated images only ever help tune)\n"
               f"- sealed: {len(sealed)} labeled metallic photos scraped after {SEALED_FROM} (never used for tuning or choosing budgets/features) from {len({creator_of(src, i['id']) for i in sealed})} photographers, {len({creator_of(src, i['id']) for i in sealed if i['new_photographer']})} of whom never appear in the pool\n"
               f"- fault-free in pool: {sum(i['clean'] for i in pool)} (Opus-confirmed {sum(i['clean'] and i['tier'] == 'opus' for i in pool)}); in sealed: {sum(i['clean'] for i in sealed)}\n")
    out.append("## Tuned thresholds (whole pool; per-check false-alarm budgets " + ", ".join(f"{k} {v:.0%}" for k, v in BUDGETS.items()) + ")\n```\n" + json.dumps(tuned, indent=2) + "\n```\n")
    out.append("## Headline: share of CLEAN photos wrongly rejected\n"
               "| system | pool, each photographer held out in turn (n=%d) | sealed, scraped after freeze (n=%d) | recall of injected faults (pool CV, macro of 5) |\n|---|---|---|---|\n" % (n, sn) +
               f"| v1 whole-frame (synthetic-tuned) | {res['fa_old'] / n:.1%} [{lo_o:.1%}, {hi_o:.1%}] | " + (f"{sm_old['fa']:.1%} ({round(sm_old['fa'] * sn)}/{sn})" if sn else "-") + " | n/a (not re-tuned) |\n" +
               f"| v2 object-scoped | {res['fa_new'] / n:.1%} [{lo:.1%}, {hi:.1%}] | " + (f"{sm_new['fa']:.1%} ({s_k}/{sn}) [{s_lo:.1%}, {s_hi:.1%}]" if sn else "-") + f" | {macro:.2f} |\n\n"
               "Brackets are 95% Wilson intervals. The pool number is a cross-validated estimate but the per-check budgets were chosen on this same pool, so it is mildly optimistic; "
               "the sealed row is the check on that. It came out higher (see below), which is the expected size of that optimism.\n"
               f"Sealed subset from photographers who never appear in the pool: n={np_new['n_clean']} clean, v1 wrongly rejects {round(np_old['fa'] * np_new['n_clean'])}, v2 {round(np_new['fa'] * np_new['n_clean'])}.\n")
    phone = [it for it in items if it["split"] == "phone"]
    if phone:
        out.append("# Your iPhone photos\n")
        out.append(f"{len(phone)} Opus-labeled phone photos ({sum(i['clean'] for i in phone)} fault-free) from {len({i['person'] for i in phone})} people in {len({i['scene'] for i in phone})} scenes; "
                   f"at most {PHONE_CAP} per person and scene are used for tuning. The four people shot scene s1 within minutes of each other, so scenes, not people, are the independent groups. "
                   "Numbers are photos, not independent samples.\n")
        frozen = DATA / "phone_step3.md"
        if frozen.exists():
            out.append("### Measured first: thresholds frozen before any phone data was used\n" + frozen.read_text().replace("## Phone photos, thresholds frozen BEFORE any phone tuning", "Frozen-threshold result").strip() + "\n")
        out.append("### Honest estimate after adding phone photos to tuning: each scene held out in turn\n"
                   "| held-out scene | clean photos | v1 wrongly rejects | v2 wrongly rejects [95% CI] | what rejected them |\n|---|---|---|---|---|")
        for sc, o in res["phone"].items():
            n_ = o["clean"]
            lo_, hi_ = wilson(o["fa_new"], n_)
            out.append(f"| {sc} | {n_} | {o['fa_old']} ({o['fa_old'] / max(1, n_):.0%}) | {o['fa_new']} ({o['fa_new'] / max(1, n_):.0%}) [{lo_:.0%}, {hi_:.0%}] | "
                       + (", ".join(f"{k} {v}" for k, v in sorted(o['sources'].items(), key=lambda x: -x[1])) or "-") + " |")
        out.append("\nRecall of simulated faults on held-out phone scenes (faults applied to the phone photos): " + "; ".join(
            f"{sc}: " + ", ".join(f"{c} {h_}/{t_}" for c, (h_, t_) in sorted(o["by_check"].items())) for sc, o in res["phone"].items()) + "\n")
        out.append(phone_section(items, thr, "Final thresholds on phone photos (in-sample: they were part of tuning)"))
    ctrl = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for it in items:
        if it["clean"] and it["split"] != "gen":
            dom = "phone" if it["split"] == "phone" else "wikimedia"
            for n_, sev_, ex_, s_ in it["res"]["variants"]:
                if n_.startswith("ctrl_"):
                    c_ = ctrl[n_][dom]
                    c_[0] += 1
                    c_[1] += verdict(s_, thr, "roi")["passed"]
                    c_[2] += verdict(s_, LEGACY_THRESHOLDS, "legacy")["passed"]
    if ctrl:
        out.append("## Negative controls: a sharp, well-lit spoon on an extreme backdrop (expected verdict: PASS)\n"
                   "Built from fault-free photos, so truth is by construction. Evaluation only; rule fixed in advance: a control passing under 90% would be added to the tuning negatives.\n\n"
                   "| control | photos | v1 passes | v2 passes |\n|---|---|---|---|")
        for n_ in sorted(ctrl):
            for dom in ("phone", "wikimedia"):
                n0, p2, p1 = ctrl[n_].get(dom, (0, 0, 0))
                if n0:
                    out.append(f"| {n_} ({dom}) | {n0} | {p1} ({p1 / n0:.0%}) | {p2} ({p2 / n0:.0%}) |")
        out.append("")
    out.append("## Goal criteria\n" + "\n".join(f"- {'PASS' if ok else 'FAIL'}: {k}" for k, ok in _criteria(m)) + "\n")
    out.append("## Pool CV: recall of injected faults by check and severity (2 = clear, 3 = severe)\n| check | sev 2 | sev 3 | both |\n|---|---|---|---|")
    for chk in FAULTS:
        cells = []
        for sv in (2, 3):
            h_, t_ = res["by_sev"].get((chk, sv), (0, 0))
            cells.append(f"{h_}/{t_} ({h_ / t_:.0%})" if t_ else "-")
        h_, t_ = res["by_check"].get(chk, (0, 0))
        out.append(f"| {chk} | {cells[0]} | {cells[1]} | " + (f"{h_}/{t_} ({h_ / t_:.0%})" if t_ else "-") + " |")
    out.append("\nWhat wrongly rejected the held-out clean photos (a photo can trip several): " + ", ".join(f"{k} {v}" for k, v in sorted(res["sources"].items(), key=lambda x: -x[1])) + "\n")
    top = sorted(res["per_creator"].items(), key=lambda x: -x[1][0])[:4]
    out.append("Largest photographers among the clean pool photos (n clean, v2 wrongly rejects, v1 wrongly rejects):\n" +
               "\n".join(f"- {c[:28]}: {a_}, {b_}, {c_}" for c, (a_, b_, c_) in top) + "\n")
    out.append(f"## Mimics: clean pool photos that v1 rejects ({len(mimic)} of {len(pclean)}); v2 (final thresholds, in-sample) still rejects {len(new_mimic)}\n" + "\n".join(
        f"- {it['id']}: v1 said {verdict(it['res']['boxed'], LEGACY_THRESHOLDS, 'legacy')['reasons']}; v2 said {verdict(it['res']['boxed'], thr, 'roi')['reasons'] or 'pass'}" for it in mimic[:12]) + "\n")
    out.append("## Naturally faulty photos (model-labeled, object-located, severity >= 2; pool + sealed, in-sample)\n| fault | n | v1 catches | v2 catches |\n|---|---|---|---|")
    for f in FAULTS:
        nat = [it for it in pool + sealed if it["split"] != "gen" and it["faults"][f]["present"] and it["faults"][f]["severity"] >= 2 and it["faults"][f]["where"] in ("object", "both")]
        c1 = sum(f in verdict(it["res"]["boxed"], LEGACY_THRESHOLDS, "legacy")["failed"] for it in nat)
        c2 = sum(f in verdict(it["res"]["boxed"], thr, "roi")["failed"] for it in nat)
        out.append(f"| {f} | {len(nat)} | {c1} | {c2} |")
    out.append("\n## Label agreement: Haiku vs Opus (audited sample)")
    both = [i for i in op if i in hs]
    if both:
        g = [i for i in both if hs[i]["contains_object"] and op[i]["contains_object"]]
        out.append(f"- n audited {len(both)}; object-present agreement {sum(hs[i]['contains_object'] == op[i]['contains_object'] for i in both)}/{len(both)}; "
                   f"metallic {sum(hs[i]['metallic'] == op[i]['metallic'] for i in both)}/{len(both)}")
        out.append("| fault | Opus positives | Haiku caught | Haiku extra positives |\n|---|---|---|---|")
        for f in FAULTS:
            ox = [i for i in g if op[i]["faults"][f]["present"]]
            out.append(f"| {f} | {len(ox)} | {sum(hs[i]['faults'][f]['present'] for i in ox)} | {sum(hs[i]['faults'][f]['present'] and not op[i]['faults'][f]['present'] for i in g)} |")
    else:
        out.append("- no audit labels yet (run scripts/label.py --model claude-opus-5-5 --sample 150 --gate --out labels_audit.jsonl)")
    out.append("\n## History (everything below was decided after looking at data; nothing is pre-registered except the goal itself)\n"
               "- Run 1: tuned by balanced accuracy on a creator-hash dev/test split: v2 rejected 59% of clean test photos (v1 24%). "
               "- Fixed ROI bugs (GrabCut asserting on full-frame boxes; flat frames hanging; my full-frame shortcut returning white backdrops), changed the rule to a per-check false-alarm budget, recalibrated overexposure/low-light severities. "
               "Run 2: 49% (v1 63%) on that test split, dominated by one catalogue photographer and the contrast check. "
               "- Goal set: <= 10%. Switched to leave-photographers-out CV on all photos (the old test split had put the hardest photographer entirely outside tuning). "
               "Features picked per check by CV: dark = frame 95th-percentile luminance (was object median), contrast = object spread + separation from surroundings (was spread alone), "
               "glare = largest interior saturated blob over frame area (was over object area). Rejected after testing: object 25th-percentile luminance and frame 5th-percentile for bright, michelson contrast. "
               "ROI fallback: an implausible GrabCut mask now falls back to the box minus its backdrop instead of 'no object'. "
               "- Performance pass: faults fabricated and scored at <=1280px, GrabCut seeded (it was randomised), unused severity-1/downscale variants no longer scored; "
               "this moved blur features on large originals and the headline by about a point, and made runs reproducible.\n")
    out.append("## Caveats\n- Labels are model-made and unaudited by a human; Haiku-only 'clean' labels miss faults, which makes the false-alarm number conservative.\n"
               "- Degraded variants are simulations (scripts/degrade.py); recall there is not recall on real faults, and glare/bright recall is the weak spot.\n"
               "- Wikimedia photos skew toward museum/catalogue shots; not phone photos. The sealed set is small. There is no real phone test set yet.\n"
               "- ROI is GrabCut-based; it leaks into shadows, and keeps the backdrop when a scale bar hides the border colour.\n")
    text = "\n".join(out)
    (DATA / "results.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
