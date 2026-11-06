# Results (metal spoon, pack `spoon`)

Labels: Claude Haiku 5.5 for every image, overridden by Claude Opus 5.5 where an audit label exists. **No human has audited these labels.** 'Truth' for degraded variants is by construction; 'clean' and 'natural fault' are model-judged.

## Data
- pool: 258 real labeled metallic-spoon photos from 131 photographers + 60 generated (generated images only ever help tune)
- sealed: 493 labeled metallic photos scraped after img_00454 (never used for tuning or choosing budgets/features) from 378 photographers, 359 of whom never appear in the pool
- fault-free in pool: 233 (Opus-confirmed 126); in sealed: 426

## Tuned thresholds (whole pool; per-check false-alarm budgets blur 2%, dark 2%, contrast 2%, glare 2%, bright 3%)
```
{
  "blur_min": 24.5,
  "dark_max_mean": 139.5,
  "bright_max_frac": 0.13123824711028712,
  "contrast_min_std": 93.5,
  "glare_max_frac": 0.018744461532441706
}
```

## Headline: share of CLEAN photos wrongly rejected
| system | pool, each photographer held out in turn (n=216) | sealed, scraped after freeze (n=426) | recall of injected faults (pool CV, macro of 5) |
|---|---|---|---|
| v1 whole-frame (synthetic-tuned) | 38.9% [32.6%, 45.5%] | 34.7% (148/426) | n/a (not re-tuned) |
| v2 object-scoped | 9.3% [6.1%, 13.9%] | 9.9% (42/426) [7.4%, 13.1%] | 0.63 |

Brackets are 95% Wilson intervals. The pool number is a cross-validated estimate but the per-check budgets were chosen on this same pool, so it is mildly optimistic; the sealed row is the check on that. It came out higher (see below), which is the expected size of that optimism.
Sealed subset from photographers who never appear in the pool: n=360 clean, v1 wrongly rejects 129, v2 35.

## Goal criteria
- PASS: G1 pool, leave-photographers-out CV: new wrongly rejects <= 10% of clean photos
- PASS: G2 sealed set (n=426): new wrongly rejects <= 10% (point estimate; interval shown)
- FAIL: G3 guard against going blind: pool CV macro recall of injected faults >= 0.65
- PASS: G4 mimics (clean photos v1 rejects): v2 rejects fewer than v1

## Pool CV: recall of injected faults by check and severity (2 = clear, 3 = severe)
| check | sev 2 | sev 3 | both |
|---|---|---|---|
| blur | 416/432 (96%) | 450/648 (69%) | 866/1080 (80%) |
| dark | 215/216 (100%) | 430/432 (100%) | 645/648 (100%) |
| bright | 103/216 (48%) | 82/216 (38%) | 185/432 (43%) |
| contrast | 124/216 (57%) | 215/216 (100%) | 339/432 (78%) |
| glare | 19/215 (9%) | 51/215 (24%) | 70/430 (16%) |

What wrongly rejected the held-out clean photos (a photo can trip several): bright 6, contrast 5, glare 5, blur 4, no_object 1

Largest photographers among the clean pool photos (n clean, v2 wrongly rejects, v1 wrongly rejects):
- Smirkybec: 33, 0, 0
- Dominique Provost: 30, 3, 28
- Ji-Elle: 9, 1, 3
- unknown: 8, 0, 2

## Mimics: clean pool photos that v1 rejects (84 of 216); v2 (final thresholds, in-sample) still rejects 9
- img_00010: v1 said ['Low contrast']; v2 said pass
- img_00020: v1 said ['Low contrast']; v2 said pass
- img_00025: v1 said ['Blurry, hold steady', 'Low contrast']; v2 said pass
- img_00026: v1 said ['Low contrast']; v2 said pass
- img_00027: v1 said ['Low contrast']; v2 said pass
- img_00034: v1 said ['Low contrast']; v2 said pass
- img_00036: v1 said ['Too dark']; v2 said ['Too bright / washed out']
- img_00037: v1 said ['Too bright / washed out', 'Glare, change the angle']; v2 said pass
- img_00046: v1 said ['Too bright / washed out', 'Glare, change the angle']; v2 said pass
- img_00050: v1 said ['Low contrast']; v2 said pass
- img_00055: v1 said ['Low contrast']; v2 said pass
- img_00057: v1 said ['Low contrast']; v2 said pass

## Naturally faulty photos (model-labeled, object-located, severity >= 2; pool + sealed, in-sample)
| fault | n | v1 catches | v2 catches |
|---|---|---|---|
| blur | 9 | 1 | 5 |
| dark | 3 | 2 | 1 |
| bright | 0 | 0 | 0 |
| contrast | 2 | 0 | 0 |
| glare | 2 | 0 | 1 |

## Label agreement: Haiku vs Opus (audited sample)
- n audited 780; object-present agreement 698/780; metallic 753/780
| fault | Opus positives | Haiku caught | Haiku extra positives |
|---|---|---|---|
| blur | 61 | 23 | 0 |
| dark | 24 | 8 | 2 |
| bright | 6 | 1 | 3 |
| contrast | 38 | 4 | 9 |
| glare | 42 | 29 | 87 |

## History (everything below was decided after looking at data; nothing is pre-registered except the goal itself)
- Run 1: tuned by balanced accuracy on a creator-hash dev/test split: v2 rejected 59% of clean test photos (v1 24%). - Fixed ROI bugs (GrabCut asserting on full-frame boxes; flat frames hanging; my full-frame shortcut returning white backdrops), changed the rule to a per-check false-alarm budget, recalibrated overexposure/low-light severities. Run 2: 49% (v1 63%) on that test split, dominated by one catalogue photographer and the contrast check. - Goal set: <= 10%. Switched to leave-photographers-out CV on all photos (the old test split had put the hardest photographer entirely outside tuning). Features picked per check by CV: dark = frame 95th-percentile luminance (was object median), contrast = object spread + separation from surroundings (was spread alone), glare = largest interior saturated blob over frame area (was over object area). Rejected after testing: object 25th-percentile luminance and frame 5th-percentile for bright, michelson contrast. ROI fallback: an implausible GrabCut mask now falls back to the box minus its backdrop instead of 'no object'. - Performance pass: faults fabricated and scored at <=1280px, GrabCut seeded (it was randomised), unused severity-1/downscale variants no longer scored; this moved blur features on large originals and the headline by about a point, and made runs reproducible.

## Caveats
- Labels are model-made and unaudited by a human; Haiku-only 'clean' labels miss faults, which makes the false-alarm number conservative.
- Degraded variants are simulations (scripts/degrade.py); recall there is not recall on real faults, and glare/bright recall is the weak spot.
- Wikimedia photos skew toward museum/catalogue shots; not phone photos. The sealed set is small. There is no real phone test set yet.
- ROI is GrabCut-based; it leaks into shadows, and keeps the backdrop when a scale bar hides the border colour.
