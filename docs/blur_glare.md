# Improving blur and glare detection

Goal set by the owner: measure blur and glare quantitatively, then improve them by 25 to 40% on a deterministic measurement, without raising the false-alarm budget.
Everything here is reproducible: `uv run scripts/bench_blur_glare.py` (quantile rule) and `... --prod before=<json>,after=<json>` (the thresholds the app really uses).

## 1. How performance is measured

Metric: recall of clearly faulty photos (severity 2 or 3) at a fixed false-alarm budget on clean photos. Four independent sources of faulty photos, because no single one is trustworthy:

| set | what it is | size (blur / glare) | role |
|---|---|---|---|
| real | natural faults found by Claude Opus in web and iPhone photos | 27 / 4 | the honest number, but small. 4 glare photos means one photo is 25 points |
| sim | `scripts/degrade.py` faults applied to clean real photos | 5,278 / 1,758 | used to design |
| edev, edit | gpt-image-2 asked to add a fault to a clean Wikimedia photo (`scripts/edit_faults.py`) | 18+19 / 14+16 | edev used to design, edit used to compare rules (so only a validation set) |
| final | 60 more edited photos generated after the rule was fixed, scored once | 17 / 17 | the cleanest number |

Clean photos (1,759 including sharp negative controls) set the threshold. Edited photos use Wikimedia (CC) bases only: the phone photos were never uploaded to OpenAI.
Headline = average recall over real, sim, final, each set weighted equally.
Cost: about $4 OpenAI image edits, about $3 Claude labeling (Haiku + Opus).

## 2. Baseline (before)

Thresholds the app used before this work (`blur_min 14.5`, `glare_max_frac 0.0103`), same clean-photo false-alarm rate:

| check | real | sim | final | headline | clean photos flagged |
|---|---|---|---|---|---|
| blur | 4/27 (15%) | 77% | 88% | 60% | 2.5% |
| glare | 1/4 (25%) | 30% | 41% | 32% | 3.7% |

Real blur was the weak spot: 2 of 18 clearly blurry iPhone photos and 2 of 9 web photos caught.

## 3. What was tried

Blur (features tested on real positives against clean photos; "AUC" is how well one number separates them):
1. Laplacian energy at the 95th percentile of the object (the existing check): real AUC 0.91 but only 15% recall at 2% false alarms, because soft-but-fine iPhone photos sit in the same range.
2. **Edge-sharpness ratio** (gradient at a fine scale divided by gradient at a coarse scale, median over the object's strongest edge ridges): contrast-free, so it travels between web and phone photos. Alone it catches 41 to 52% of real blur but fails on very blurry photos, which have no strong edges left to measure.
3. Edge width (local range divided by peak gradient): useless on real photos (AUC 0.45 to 0.62).
4. Silhouette-ring and band-energy ratios: worse than the baseline.
5. Scales and ridge percentiles were swept (0.8/2.0, 1.0/2.5, 1.2/3.0; 75/85/90). 0.8/2.0 at 85 was kept; the spread between settings (26 to 52% real recall) says the real-photo number is noisy at n=27.
6. **Kept: fail if EITHER Laplacian energy OR the edge ratio is low**, each given half of the false-alarm budget. Very blurry photos trip the old check, soft-but-edged ones trip the new one.

Glare:
1. Saturated blob variants (threshold 250, 240, 230 or 225; area over frame, over object, centroid-in-box, smoothed, flat-region versions): none beat the current frame-level blob. The common false alarms are white cups, scale paper and flat engraved metal, not anything about the threshold.
2. Blobs restricted to those on the object (mask with holes filled, because the backdrop stripper erases blown-out glare): small gain on simulated glare only.
3. Two-feature logistic regression looked strong on held-out simulated glare (19% to 52%) and led to the useful insight below, but learned weights in the runtime were judged too heavy for the gain.
4. **Insight:** real glare is ONE hotspot, whereas false alarms are scattered speckle or many small blobs. Feature `roi_hot` = largest on-object saturated blob share of the frame, multiplied by (largest blob / all saturated pixels). It lifted simulated glare 20% to 37% and edited glare 50% to 69%.
5. **Kept: glare fails if the old frame-level blob OR the hotspot feature is over its threshold.**

## 4. Result (production thresholds, through `verdict()`)

| check | set | before | after |
|---|---|---|---|
| blur | real (27) | 4 (15%) | 13 (48%) |
| blur | sim | 77% | 81% |
| blur | final (17) | 15 (88%) | 16 (94%) |
| blur | **headline** | **60%** | **74%** (+24% relative) |
| blur | clean photos flagged | 2.5% | 3.0% |
| glare | real (4) | 1 (25%) | 1 (25%) |
| glare | sim | 30% | 47% |
| glare | final (17) | 7 (41%) | 9 (53%) |
| glare | **headline** | **32%** | **42%** (+31% relative) |
| glare | clean photos flagged | 3.7% | 3.6% |

By source for blur on real photos: iPhone 2/18 to 8/18, web 2/9 to 5/9. Blur and glare together: headline 46% to 58%, +26% relative.

Held-out false-alarm rate of the whole system (all checks, `results.md`): pool 14.8% unchanged, sealed 14.3% to 15.7% (61 to 67 of 426 photos), recall of injected faults 0.67 to 0.72. Tuned thresholds: `blur_min 10.5`, `edge_min 1.58`, `glare_max_frac 0.0187`, `hot_max_frac 0.0049`.

## 5. Caveats, plainly

- The target of 25 to 40% is met on glare (+31%) and the combined headline (+26%); blur alone is +24% on the headline, which is a point short, even though real-photo recall tripled. The headline is capped by easy sets: edited blur is extreme and already at 88%.
- Real glare is 4 photos; its recall did not improve (1 of 4). The glare gain is on simulated and AI-edited glare, so treat it as unproven on real glare. Two real flash photos (tiny saturated core, big halo) are still missed. More real glare photos are the thing that would settle this.
- Edited and simulated faults are not real faults. The final set was not looked at while choosing the rule, but the rule was chosen between a handful of candidates after seeing the edit and edev sets.
- The glare check now flags 4 of 30 edited "natural sparkle" clean photos (13%), up from 2 of 30. Fewer of the same hard cases would need a better hotspot definition.
- Blur false alarms on web photos rose from 12 to 35 of 1,283 (0.9% to 2.7%) while phone fell from 31 to 16 of 476: the budget is pooled, so the edge ratio, which is better on phones, moved errors between domains.
- Blur_min fell from 14.5 to 10.5 because half the blur budget now belongs to the new check.
- Labels for real positives are model-made (Opus); no human has checked them.
