# Results

Tuned on dev (69 bases), frozen, reported on test (31 bases, counts not percentages: small sample).

Thresholds (max balanced accuracy on dev; first attempt used max F1, which rejected ~1/3 of clean images, so the rule was changed after seeing dev results):
```
{
  "blur_min": 50.55109547292888,
  "dark_max_mean": 75.51949096679687,
  "bright_max_frac": 0.023909912109375,
  "contrast_min_std": 18.54926623099475,
  "glare_max_frac": 0.033001708984375
}
```

# Dev (tuning set, optimistic)

## Per-check (severity 2-3 positives vs clean negatives)
| check | TP | FN | FP(clean flagged) | precision | recall |
|---|---|---|---|---|---|
| blur | 298 | 47 | 0/69 | 1.00 | 0.86 |
| dark | 207 | 0 | 0/69 | 1.00 | 1.00 |
| bright | 133 | 5 | 1/69 | 0.99 | 0.96 |
| contrast | 138 | 0 | 0/69 | 1.00 | 1.00 |
| glare | 138 | 0 | 0/69 | 1.00 | 1.00 |
| resolution (fixed, not tuned) | 138 | 0 | - | - | 1.00 |

## Overall accept/reject (clean=accept, severity 2-3=reject)
- clean accepted: 68/69
- degraded rejected: 988/1035

## Rejected by degradation type (severity 2-3)
- blur_dark: 69/69
- dark: 138/138
- downscale: 138/138
- gaussian_blur: 138/138
- glare: 138/138
- low_contrast: 138/138
- motion_blur: 96/138
- overexposed: 133/138

## Borderline (severity 1, not scored as right/wrong): rejected 267/483

## Combined blur+dark: both reported 69/69

# Test (held out)

## Per-check (severity 2-3 positives vs clean negatives)
| check | TP | FN | FP(clean flagged) | precision | recall |
|---|---|---|---|---|---|
| blur | 124 | 31 | 2/31 | 0.98 | 0.80 |
| dark | 93 | 0 | 1/31 | 0.99 | 1.00 |
| bright | 55 | 7 | 0/31 | 1.00 | 0.89 |
| contrast | 62 | 0 | 1/31 | 0.98 | 1.00 |
| glare | 62 | 0 | 0/31 | 1.00 | 1.00 |
| resolution (fixed, not tuned) | 62 | 0 | - | - | 1.00 |

## Overall accept/reject (clean=accept, severity 2-3=reject)
- clean accepted: 27/31
- degraded rejected: 428/465

## Rejected by degradation type (severity 2-3)
- blur_dark: 31/31
- dark: 62/62
- downscale: 62/62
- gaussian_blur: 62/62
- glare: 62/62
- low_contrast: 62/62
- motion_blur: 32/62
- overexposed: 55/62

## Borderline (severity 1, not scored as right/wrong): rejected 127/217

## Combined blur+dark: both reported 31/31

## Caveats
- Synthetic degradation: each check is scored against the degradation it was built for, so these numbers partly measure themselves.
- No real iPhone photos tested; thresholds may not transfer (compression, noise, real lighting).
- Resolution threshold is a fixed placeholder until the delivered iPhone Safari camera resolution is measured.
- Not checked: view correctness, object presence, framing.