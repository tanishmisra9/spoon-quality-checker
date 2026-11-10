"""Blur / glare benchmark: recall of clear faults at a fixed false-alarm budget, on four fault sources, for any feature in the score cache.
Threshold for a feature = the budget quantile of the CLEAN photos' feature values (real clean photos + sharp negative controls), so positives never
influence it. Positive sets (severity >= 2 only):
  real  - Opus-labeled natural faults in web + phone photos (small: the honest but noisy number)
  sim   - scripts/degrade.py faults on clean real photos (used to design features)
  edev  - gpt-image-2 added the fault to a real photo (scripts/edit_faults.py), first half: allowed while designing features
  edit  - same, second half (see sealed_edit): sealed, never used to pick a feature or threshold
  gen   - gpt-image-2 text-to-image photos Opus judged faulty
Usage: uv run scripts/bench_blur_glare.py [--budget 0.02] [--feat blur=roi_blur,glare=frame_glare_int] [--json out.json]"""
import argparse, json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.evaluate import CTRL_TUNE, compute, load  # noqa: E402

CHK = {"blur": ("low", {"defocus", "shake", "defocus_object"}, None), "glare": ("high", {"glare"}, {"ctrl_shine"})}
CTRL = {"blur": {"ctrl_bg_blur"}, "glare": {"ctrl_shine"}}
REAL = ("pool", "sealed", "phone")
def sealed_edit(iid):
    """Edited photos ed_0030-0059 and ed_0075-0099 ("edit") were looked at while comparing candidate rules, so they are only a validation set; ed_0000-0029 and ed_0060-0074
    ("edev") were used for designing. ed_0100 and up ("final") were generated after the rule was fixed and are looked at once, at the end."""
    n = int(iid.split("_")[1])
    return 30 <= n < 60 or 75 <= n < 100


def auc(pos, neg, d):
    pos, neg = np.array(pos), np.array(neg)
    if not len(pos) or not len(neg):
        return float("nan")
    a = (pos[:, None] < neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()
    return float(a if d == "low" else 1 - a)


def thresh(neg, d, budget):
    s = np.sort(neg)
    k = int(np.floor(budget * len(s)))
    return s[k] if d == "low" else s[len(s) - 1 - k]  # flagged = strictly beyond this value, so at most k negatives are flagged


def collect(items, chk):
    d, sim_names, _ = CHK[chk]
    neg, pos = [], {"real": [], "sim": [], "edev": [], "edit": [], "gen": [], "final": [], "edclean": []}
    for it in items:
        f = it["faults"][chk]
        if it["split"] in REAL:
            if it["clean"]:
                neg.append(("clean", it["res"]["boxed"], it))
                neg += [("ctrl", s, it) for n, sev, ex, s in it["res"]["variants"] if n in CTRL[chk]]
                pos["sim"] += [(s, it) for n, sev, ex, s in it["res"]["variants"] if n in sim_names]
            elif f["present"] and f["severity"] >= 2 and f["where"] in ("object", "both"):
                pos["real"].append((it["res"]["boxed"], it))
        elif it["split"] == "edit" and it["clean"]:
            pos["edclean"].append((it["res"]["boxed"], it))  # not positives: a gpt-image-2 edit (e.g. "natural sparkle") that Opus found fault-free; reported as a false-alarm rate
        elif it["split"] in ("edit", "gen") and f["present"] and f["severity"] >= 2:
            pos["final" if it["split"] == "edit" and int(it["id"].split("_")[1]) >= 100 else "edit" if it["split"] == "edit" and sealed_edit(it["id"]) else "edev" if it["split"] == "edit" else it["split"]].append((it["res"]["boxed"], it))
    return neg, pos


def run(items, chk, feat, budgets=(0.02, 0.05)):
    """feat: a score key, or 'a+b' meaning the check fails if EITHER trips; each gets an equal share of the false-alarm budget (what scripts/evaluate.py tune() does)."""
    d = CHK[chk][0]
    keys = feat.split("+")
    neg, pos = collect(items, chk)
    nv = {k: np.array([s[k] for _, s, _ in neg if s["roi_found"] and k in s]) for k in keys}
    out = {"n_neg": len(nv[keys[0]])}
    for b in budgets:
        t = {k: thresh(nv[k], d, b / len(keys)) for k in keys}
        out[f"thr@{b}"] = {k: float(v) for k, v in t.items()}
        trip = lambda s: any((s[k] < t[k] if d == "low" else s[k] > t[k]) for k in keys if k in s)  # noqa: E731
        for name, ps in pos.items():
            out[f"{name}@{b}"] = (sum(bool(s["roi_found"]) and trip(s) for s, _ in ps), len(ps))  # no object found = a miss
    for name, ps in pos.items():
        out[f"auc_{name}"] = auc([s[keys[0]] for s, _ in ps if s["roi_found"] and keys[0] in s], nv[keys[0]], d) if len(keys) == 1 else float("nan")
    return out


def summary(res, b):
    """Headline = mean recall over the sets that exist (real, sim, final), each set weighted equally."""
    r = [res[f"{n}@{b}"] for n in ("real", "sim", "final") if res[f"{n}@{b}"][1]]
    return float(np.mean([k / n for k, n in r]))


def show(name, chk, res, b=0.02):
    cells = [f"{n} {res[f'{n}@{b}'][0]}/{res[f'{n}@{b}'][1]} ({res[f'{n}@{b}'][0] / max(1, res[f'{n}@{b}'][1]):.0%}, auc {res[f'auc_{n}']:.2f})" for n in ("real", "sim", "edev", "edit", "final", "gen", "edclean")]
    print(f"{chk:5s} {name:18s} FA<={b:.0%}  " + " | ".join(cells) + f"  || headline {summary(res, b):.0%}")


def prod(items, chk, thr_file):
    """Recall with the thresholds the app really uses (a thresholds.json), through verdict(). thr_file=None -> the old rule (no edge check, no hotspot check)."""
    import config
    from backend.checks import verdict
    t = {**config.DRAFT_THRESHOLDS, "min_side_px": 0}  # drafts have the new checks switched off, so an old thresholds.json reproduces the old rule exactly
    t.update(json.loads(Path(thr_file).read_text()))
    neg, pos = collect(items, chk)
    trip = lambda s: (not s["roi_found"]) or chk in verdict(s, t, "roi")["failed"]  # noqa: E731
    out = {"fa": (sum(trip(s) for _, s, _ in neg), len(neg))}
    for n, ps in pos.items():
        out[n] = (sum(trip(s) for s, _ in ps), len(ps))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0.02)
    ap.add_argument("--feat", default="blur=roi_blur,glare=frame_glare_int")
    ap.add_argument("--json")
    ap.add_argument("--prod", help="thresholds.json to evaluate through verdict() instead of the quantile rule, e.g. before=a.json,after=b.json")
    a = ap.parse_args()
    items, *_ = load()
    items = compute(items, False)
    if a.prod:
        for lab, f in (x.split("=") for x in a.prod.split(",")):
            for chk in CHK:
                r = prod(items, chk, f)
                print(f"{lab:7s}{chk:6s} FA {r['fa'][0]}/{r['fa'][1]} ({r['fa'][0] / r['fa'][1]:.1%}) | " + " | ".join(f"{n} {k}/{t} ({k / max(1, t):.0%})" for n, (k, t) in r.items() if n != "fa"))
        return
    feats = dict(x.split("=") for x in a.feat.split(","))
    allres = {}
    for chk, f in feats.items():
        res = allres[chk] = run(items, chk, f, (a.budget,))
        show(f, chk, res, a.budget)
    if a.json:
        Path(a.json).write_text(json.dumps(allres, indent=1))


if __name__ == "__main__":
    main()
