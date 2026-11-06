# Results (metal spoon, pack `spoon`)

Labels: Claude Haiku 5.5 for every image, overridden by Claude Opus 5.5 where an audit label exists. **No human has audited these labels.** 'Truth' for degraded variants is by construction; 'clean' and 'natural fault' are model-judged.

## Data
- pool: 258 real labeled metallic-spoon photos from 131 photographers + 60 generated (generated images only ever help tune)
- sealed: 493 labeled metallic photos scraped after img_00454 (never used for tuning or choosing budgets/features) from 378 photographers, 359 of whom never appear in the pool
- fault-free in pool: 471 (Opus-confirmed 364); in sealed: 426

## Tuned thresholds (whole pool; per-check false-alarm budgets blur 2%, dark 2%, contrast 2%, glare 2%, bright 3%)
```
{
  "blur_min": 14.5,
  "dark_max_mean": 170.5,
  "bright_max_frac": 0.0796376138714586,
  "contrast_min_std": 95.5,
  "glare_max_frac": 0.010306852409638554
}
```

## Headline: share of CLEAN photos wrongly rejected
| system | pool, each photographer held out in turn (n=216) | sealed, scraped after freeze (n=426) | recall of injected faults (pool CV, macro of 5) |
|---|---|---|---|
| v1 whole-frame (synthetic-tuned) | 38.9% [32.6%, 45.5%] | 34.7% (148/426) | n/a (not re-tuned) |
| v2 object-scoped | 14.8% [10.7%, 20.2%] | 14.3% (61/426) [11.3%, 18.0%] | 0.67 |

Brackets are 95% Wilson intervals. The pool number is a cross-validated estimate but the per-check budgets were chosen on this same pool, so it is mildly optimistic; the sealed row is the check on that. It came out higher (see below), which is the expected size of that optimism.
Sealed subset from photographers who never appear in the pool: n=360 clean, v1 wrongly rejects 129, v2 49.

# Your iPhone photos

298 Opus-labeled phone photos (238 fault-free) from 4 people in 2 scenes; at most 40 per person and scene are used for tuning. The four people shot scene s1 within minutes of each other, so scenes, not people, are the independent groups. Numbers are photos, not independent samples.

### Measured first: thresholds frozen before any phone data was used
Frozen-threshold result (Opus-labeled: 198 photos, 160 fault-free)

| group | clean photos | v1 wrongly rejects | v2 wrongly rejects [95% CI] | v2 'no object' |
|---|---|---|---|---|
| all phone photos | 160 | 29 (18%) | 32 (20%) [15%, 27%] | 0 |
| scene s1 | 116 | 10 (9%) | 24 (21%) [14%, 29%] | 0 |
| scene s2 | 44 | 19 (43%) | 8 (18%) [10%, 32%] | 0 |

What wrongly rejected the clean phone photos: blur 31, dark 1

Naturally faulty phone photos (Opus-flagged; real, not simulated): how often each system flags that fault

| fault | photos | v1 catches | v2 catches |
|---|---|---|---|
| blur | 31 | 8 | 12 |
| dark | 1 | 0 | 0 |
| bright | 1 | 0 | 0 |
| contrast | 2 | 0 | 0 |
| glare | 8 | 0 | 0 |

Through the fixed on-screen guide box instead of the labeler's box: v2 wrongly rejects 49 of 160 clean phone photos.

### Honest estimate after adding phone photos to tuning: each scene held out in turn
| held-out scene | clean photos | v1 wrongly rejects | v2 wrongly rejects [95% CI] | what rejected them |
|---|---|---|---|---|
| s1 | 178 | 13 (7%) | 20 (11%) [7%, 17%] | blur 19, bright 1 |
| s2 | 60 | 26 (43%) | 3 (5%) [2%, 14%] | blur 3 |

Recall of simulated faults on held-out phone scenes (faults applied to the phone photos): s1: blur 938/1246, bright 172/356, contrast 286/356, dark 534/534, glare 69/356; s2: blur 289/420, bright 77/120, contrast 98/120, dark 180/180, glare 38/120

## Final thresholds on phone photos (in-sample: they were part of tuning) (Opus-labeled: 298 photos, 238 fault-free)

| group | clean photos | v1 wrongly rejects | v2 wrongly rejects [95% CI] | v2 'no object' |
|---|---|---|---|---|
| all phone photos | 238 | 39 (16%) | 16 (7%) [4%, 11%] | 0 |
| scene s1 | 178 | 13 (7%) | 13 (7%) [4%, 12%] | 0 |
| scene s2 | 60 | 26 (43%) | 3 (5%) [2%, 14%] | 0 |

What wrongly rejected the clean phone photos: blur 15, bright 1

Naturally faulty phone photos (Opus-flagged; real, not simulated): how often each system flags that fault

| fault | photos | v1 catches | v2 catches |
|---|---|---|---|
| blur | 50 | 14 | 6 |
| dark | 3 | 0 | 3 |
| bright | 2 | 0 | 0 |
| contrast | 3 | 0 | 0 |
| glare | 10 | 0 | 0 |

Through the fixed on-screen guide box instead of the labeler's box: v2 wrongly rejects 48 of 238 clean phone photos.

## Negative controls: a sharp, well-lit spoon on an extreme backdrop (expected verdict: PASS)
Built from fault-free photos, so truth is by construction. Evaluation only; rule fixed in advance: a control passing under 90% would be added to the tuning negatives.

| control | photos | v1 passes | v2 passes |
|---|---|---|---|
| ctrl_bg_blur (phone) | 238 | 100 (42%) | 222 (93%) |
| ctrl_bg_blur (wikimedia) | 641 | 325 (51%) | 554 (86%) |
| ctrl_dark_bg (phone) | 238 | 8 (3%) | 217 (91%) |
| ctrl_dark_bg (wikimedia) | 641 | 48 (7%) | 446 (70%) |
| ctrl_shine (phone) | 238 | 198 (83%) | 223 (94%) |
| ctrl_shine (wikimedia) | 641 | 404 (63%) | 552 (86%) |
| ctrl_white_bg (phone) | 238 | 123 (52%) | 218 (92%) |
| ctrl_white_bg (wikimedia) | 641 | 358 (56%) | 573 (89%) |

## Goal criteria
- FAIL: G1 pool, leave-photographers-out CV: new wrongly rejects <= 10% of clean photos
- FAIL: G2 sealed set (n=426): new wrongly rejects <= 10% (point estimate; interval shown)
- PASS: G3 guard against going blind: pool CV macro recall of injected faults >= 0.65
- PASS: G4 mimics (clean photos v1 rejects): v2 rejects fewer than v1

## Pool CV: recall of injected faults by check and severity (2 = clear, 3 = severe)
| check | sev 2 | sev 3 | both |
|---|---|---|---|
| blur | 460/647 (71%) | 505/863 (59%) | 965/1510 (64%) |
| dark | 215/216 (100%) | 430/432 (100%) | 645/648 (100%) |
| bright | 143/216 (66%) | 113/216 (52%) | 256/432 (59%) |
| contrast | 137/216 (63%) | 215/216 (100%) | 352/432 (81%) |
| glare | 46/215 (21%) | 85/215 (40%) | 131/430 (30%) |

What wrongly rejected the held-out clean photos (a photo can trip several): contrast 12, glare 10, bright 9, dark 2, no_object 1

Largest photographers among the clean pool photos (n clean, v2 wrongly rejects, v1 wrongly rejects):
- Smirkybec: 33, 0, 0
- Dominique Provost: 30, 9, 28
- Ji-Elle: 9, 0, 3
- unknown: 8, 0, 2

## Mimics: clean pool photos that v1 rejects (84 of 216); v2 (final thresholds, in-sample) still rejects 13
- img_00010: v1 said ['Low contrast']; v2 said pass
- img_00020: v1 said ['Low contrast']; v2 said pass
- img_00025: v1 said ['Blurry, hold steady', 'Low contrast']; v2 said ['Low contrast']
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
| blur | 27 | 11 | 4 |
| dark | 3 | 2 | 0 |
| bright | 0 | 0 | 0 |
| contrast | 4 | 0 | 0 |
| glare | 4 | 0 | 1 |

## Label agreement: Haiku vs Opus (audited sample)
- n audited 1078; object-present agreement 996/1078; metallic 1051/1078
| fault | Opus positives | Haiku caught | Haiku extra positives |
|---|---|---|---|
| blur | 111 | 47 | 6 |
| dark | 27 | 8 | 2 |
| bright | 8 | 2 | 3 |
| contrast | 41 | 5 | 11 |
| glare | 52 | 37 | 187 |

## History (everything below was decided after looking at data; nothing is pre-registered except the goal itself)
- Run 1: tuned by balanced accuracy on a creator-hash dev/test split: v2 rejected 59% of clean test photos (v1 24%). - Fixed ROI bugs (GrabCut asserting on full-frame boxes; flat frames hanging; my full-frame shortcut returning white backdrops), changed the rule to a per-check false-alarm budget, recalibrated overexposure/low-light severities. Run 2: 49% (v1 63%) on that test split, dominated by one catalogue photographer and the contrast check. - Goal set: <= 10%. Switched to leave-photographers-out CV on all photos (the old test split had put the hardest photographer entirely outside tuning). Features picked per check by CV: dark = frame 95th-percentile luminance (was object median), contrast = object spread + separation from surroundings (was spread alone), glare = largest interior saturated blob over frame area (was over object area). Rejected after testing: object 25th-percentile luminance and frame 5th-percentile for bright, michelson contrast. ROI fallback: an implausible GrabCut mask now falls back to the box minus its backdrop instead of 'no object'. - Performance pass: faults fabricated and scored at <=1280px, GrabCut seeded (it was randomised), unused severity-1/downscale variants no longer scored; this moved blur features on large originals and the headline by about a point, and made runs reproducible.

## Caveats
- Labels are model-made and unaudited by a human; Haiku-only 'clean' labels miss faults, which makes the false-alarm number conservative.
- Degraded variants are simulations (scripts/degrade.py); recall there is not recall on real faults, and glare/bright recall is the weak spot.
- Wikimedia photos skew toward museum/catalogue shots; not phone photos. The sealed set is small. There is no real phone test set yet.
- ROI is GrabCut-based; it leaks into shadows, and keeps the backdrop when a scale bar hides the border colour.
