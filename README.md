# Guided image capture PoC: grade the object, not the photo

Sprint 1 question: can a system guide someone to capture a complete set of usable images of an everyday object?
The spoon is the reference object. The system reports **image-quality problems**; it does not verify the object or the view.
It is built as a swappable **pack** so the same pipeline (data collection, labeling, tuning, evaluation, checks, app) can be pointed at
another object, with rod bearings as the intended next one, by writing one config file.

## What changed in v2, and why
v1 scored 98-100% on its own test set and still gave wrong advice on real photos. Two causes:
1. **It measured the wrong pixels.** Every check used the whole frame, so a white spoon read "too bright" and a slate backdrop "too dark".
   v2 finds the object inside a guide box (classical GrabCut, no model at runtime) and scores only the object's pixels
   (`backend/roi.py`, `backend/checks.py`). The response still carries the old whole-frame verdict as `legacy`, and the app shows it when the two disagree.
2. **It was tuned on its own fakes.** 100 near-identical AI photos plus whole-frame cv2 faults (gaussian blur, a 1-D motion kernel,
   additive brightness, a white circle). v2 uses real photos in real environments and physically-motivated faults, and scores old vs new on the same held-out real photos.

## Data collection (all per pack, under `data/<pack>/`)
| step | script | what it does |
|---|---|---|
| scrape | `scripts/scrape.py` | CC-licensed photos from Wikimedia Commons; licence, creator and page URL per image in `sources.csv`; dHash dedup. Images are gitignored; `--from-sources` re-downloads. |
| label | `scripts/label.py` | Claude Haiku 5.5 vision pass (structured output): is a metallic target present, bounding box, view, and per-fault severity and *where* (object vs background). Cached in `labels.jsonl`. |
| audit | `scripts/label.py --model claude-opus-5-5 --sample 150 --gate --out labels_audit.jsonl` | Opus re-labels a sample; **Opus overrides Haiku where present** (Haiku misses faults, see `results.md`). |
| ingest | `scripts/ingest_phone.py` | Your own phone photos: manifest, flatten, HEIC to JPEG (see below). |
| generate | `scripts/generate_images.py` | gpt-image-2, metallic only: finishes x scenes x views x fault styles. Adds environment diversity; used for tuning only, never in the test set. |
| degrade | `scripts/degrade.py` | defocus (disc PSF), hand shake (curved PSF), low light (shot and read noise, JPEG), overexposure (linear-light clipping), haze, glare on the object mask, downscale. In memory, deterministic per image. |
| evaluate | `scripts/evaluate.py` | Splits real photos by photographer, tunes on dev, freezes, reports old vs new on held-out test -> `data/<pack>/results.md`, `thresholds.json`. |

`scripts/contact_sheet.py <dir>` makes an eyeball sheet of any image folder.

## Port to a new object (the rod-bearing checklist)
1. `cp packs/spoon.toml packs/rod_bearing.toml`; edit `object`, `noun`, `views` (with `prompt` wording for generation), `scrape.queries/environments`,
   `generate.finishes/scenes`, and the `faults.*.definition` text the labeler is given (say what is NOT a fault, e.g. normal shine on polished metal).
2. `PACK=rod_bearing uv run scripts/scrape.py --n 300 && uv run scripts/label.py && uv run scripts/evaluate.py`. No code edits.
3. `PACK=fork` is a canary pack that proves this; `tests/test_label.py` checks the schema rebuilds from a different pack.
Note: if the new object's pictures come from a shop floor rather than Wikimedia, swap the scraper source (one function) or drop images into `data/<pack>/raw/`.

## Acceptable image = passes all checks (`backend/checks.py`, thresholds in `data/<pack>/thresholds.json`)
Features were chosen per check by leave-photographers-out cross-validation, not by taste (candidates in `data/spoon/results.md` history):
- **resolution**: short side in pixels.
- **blur**: 95th percentile of |Laplacian| around the object (its sharpest edges).
- **too dark**: the *frame's* 95th-percentile luminance, i.e. nothing bright anywhere. A dark-patina spoon on a bright backdrop is not underexposed; the object median said it was.
- **too bright**: share of the object blown out, counting blown pixels that the backdrop-stripper removed (object and white backdrop clip to the same colour).
- **low contrast**: object tonal spread (5th to 95th percentile) plus how far it stands out from its surroundings.
- **glare**: largest saturated blob that does not touch the frame border (border-connected white is backdrop), over frame area.
- **no object**: only when the guide box is flat (blank wall, lens cap). If GrabCut finds nothing plausible in a textured box, the box minus its backdrop is used.
Metrics use percentiles on purpose: the mask sometimes leaks into shadow, and means would then measure the leak.

## Run
```
uv sync
# add ANTHROPIC_API_KEY (labeling) and OPENAI_API_KEY (generation) to .env
uv run scripts/scrape.py --n 30                 # pilot; then --n 500
uv run scripts/label.py --limit 30              # pilot; then without --limit
uv run scripts/label.py --model claude-opus-5-5 --sample 150 --gate --out labels_audit.jsonl
uv run scripts/generate_images.py --n 6         # pilot; costs money
uv run scripts/evaluate.py
uv run python -m pytest
uv run python -m uvicorn backend.main:app --port 8000     # API
cd frontend && npm install && npm run dev       # https://<your-mac-ip>:5173 on the iPhone (accept the self-signed cert)
```
Use `uv run python -m pytest`, not `uv run pytest` (a different pytest on PATH can shadow the venv).

## Results (default GrabCut seed; regenerate with `uv run scripts/evaluate.py`, tables in `data/spoon/results.md`)
Two domains now: 1,366 scraped Wikimedia photos and your own iPhone photos. One global set of thresholds is tuned on both.

**iPhone photos (your 298, all Opus-labeled, 238 fault-free), measured in order:**

| step | wrongly rejects clean phone photos | notes |
|---|---|---|
| 0. v1 whole-frame | 16% (39/238) | scene s1 7%, s2 43% |
| 1. v2 with thresholds frozen BEFORE any phone data (`phone_step3.md`, first 198 labeled) | **20%** (32/160, CI 15 to 27) | blur flagged 31 of the 32: no better than v1 on real phone photos |
| 2. phone photos added to tuning, each scene held out in turn | s1 11% (20/178), s2 5% (3/60): **about 10%** (23/238) | honest estimate, final thresholds |
| 3. fresh photos never used in tuning (`phone_fresh.md`, 100 photos, 78 fault-free) | **8%** (6/78, CI 4 to 16); v1 13% | measured with the step-6 thresholds before these joined tuning |

**Wikimedia photos (426 sealed clean, never tuned on):** v1 34.7%; v2 **9.9% before phone data, 14.3% after**. That regression is the price of one global threshold set for two cameras/domains. Per-domain thresholds would likely recover it (not done: ruled out in advance for this round; decide before the next).

What the evidence says:
- **The phone gap was real.** iPhone photos are smooth and heavily processed: clean phone photos have about half the sharpness energy of clean Wikimedia photos at our scale (median 44 vs 87), so a threshold learned on web JPEGs flagged them (blur AUC 0.70 on phone vs 0.84 on Wikimedia).
- **Negative controls found a wrong-advice case my own numbers had hidden.** A sharp, lit spoon on a black backdrop was rejected about two-thirds of the time because "too dark" read the frame, not the object. Exposure is now the brighter of the object and the frame: that control passes 94% on phone photos (32% before). On Wikimedia it passes 70%, partly because many Wikimedia spoons are dark antiques, which makes the control invalid for them.
- **Real blur is largely not caught, and the lower false-alarm rate is partly bought with that.** Opus flagged 50 phone photos as blurry; v2 catches 6 (v1 catches 14), and on the 19 blurry fresh photos it catches 2 (v1 6). An obvious hand-shake photo (ph_0262) passed in the browser as "Good image". Natural glare (10 photos) and contrast (3) are caught 0 times by either system. Dark is caught 3 of 3. Blur is the next thing to fix: measure sharpness on the object's edges relative to the background's own texture instead of an absolute Laplacian threshold.
- **Controls are evaluation-only unless they fail.** Rule fixed before the run: a control passing under 90% joins the tuning negatives (background blur and normal shine did; white backdrop at 90% did not).

**Honest limits of the phone numbers:** all 298 photos come from 2 scenes (the four people shot the same place within minutes; only one person's second session is elsewhere). Counts are photos, not independent samples, and 112 of them are one spoon on one desk. All 298 are Opus-labeled (Opus is claude-opus-5-5, verified live against the Models API as the newest Opus; `scripts/label.py` now checks the model at startup and warns if a newer one in the same family exists).
Camera-app stills are not what the app sees: the app captures browser video frames. The truest next test is about 30 photos taken through the app itself.

**Wikimedia sealed detail (before phone data):** pool CV 9.3% (CI 6.1 to 13.9), sealed 9.9% (42/426, CI 7.4 to 13.1) at seeds 0, 1, 2: 9.9, 10.1, 9.6. Recall of injected faults 0.63: dark 100%, blur 80%, contrast 78%, bright 43%, glare 16%. The resolution check is switched off in evaluation (scraped thumbnails say nothing about how the photo was taken); it still runs in the app.
v1 artifacts (`data/base`, `data/dev`, `data/test`, `data/manifest.csv`, `data/results.md`, `data/thresholds.json`) are kept as the frozen "before". Snapshots: `results_prephone.md`, `results_step4.md`, `results_step5.md`, `thresholds_prephone.json`.

## Your own phone photos (`scripts/ingest_phone.py`)
```
uv run scripts/ingest_phone.py manifest ~/Downloads/spoon              # records folder, device, time, ISO, shutter, scene id, sha256; no GPS; moves nothing
uv run scripts/ingest_phone.py flatten  ~/Downloads/spoon --rename spoons   # moves files up, verifies sha256, removes emptied folders
uv run scripts/ingest_phone.py convert  ~/Downloads/spoons             # HEIC -> JPEG (long edge 2048) into data/spoon/phone/, registers rows in sources.csv
uv run scripts/label.py                                                # Haiku on everything new
uv run scripts/label.py --model claude-opus-5-5 --ids-file <ids> --sample 1000 --gate --out labels_audit.jsonl
uv run scripts/phone_baseline.py                                       # score with the frozen pre-phone thresholds (run BEFORE tuning on new phone photos)
```
Folders are flattened with original filenames; who shot what, and which scene, lives in `data/spoon/phone_manifest.csv`, so the evaluation can hold out a whole scene instead of splitting near-twin photos across tuning and testing.
Simulated faults on phone photos now include object-only defocus (focus missed the spoon) and four evaluation-only negative controls (blurred background, black backdrop, white backdrop, normal specular shine).

## Speed
Scoring is the only slow part of the pipeline (everything else in `evaluate.py` takes about 0.2 s), and 82% of scoring is `cv2.grabCut`. Measured full rescore of 811 photos on this machine:

| | wall clock | CPU time |
|---|---|---|
| before (full-resolution fault generation, every variant scored) | 425 s | 5,381 s |
| after | **248 s (1.7x)** | 3,182 s |

What changed: faults are fabricated and scored at a 1280px working size (`backend/checks.py: shrink`); the clean photo is segmented once and shared by the glare and overexposure generators; severity-1 and resolution-only variants, which tuning and recall never read, are no longer generated or scored (`variants(min_sev=2, downscale=False)`); the score cache is keyed on score code only, so editing `verdict()` no longer forces a rescore; load balancing uses `chunksize=1`.
Not changed on purpose: cheaper GrabCut settings. 320px / 2 iterations is only 1.3x faster, and anything smaller moves masks (median overlap 0.90 to 0.94 with a bad tail), which would move the results.
`GRABCUT_SEED` (default 0) seeds OpenCV's RNG: GrabCut is otherwise randomised, so repeated runs gave slightly different masks. Live app latency is about 0.4 s per photo, almost all GrabCut.
Scraping speed is set by Wikimedia (about 6 to 10 photos a minute after a short burst) and Openverse's anonymous search quota, not by this code.

## Known limitations (be upfront in the demo)
- **Labels are model-made and no human has audited them.** Opus overrides Haiku, but Opus is also a model. A small human-checked set is the missing piece.
- Degraded variants are simulations. Recall on them is not recall on real faults. Natural-fault counts are small because CC photos rarely contain obvious faults.
- Wikimedia photos skew toward museum and catalogue shots. They are not phone photos. A real phone test set does not exist yet; clean phone photos would at least give a real false-alarm rate.
- GrabCut leaks into shadows on cluttered or dark-bordered scenes, fails on thin objects touching the frame edge, and keeps the backdrop when a scale bar or ruler hides the border colour (e.g. img_00324).
- Highlights on polished metal are normal, so "bright" and "glare" separate faulty from fine less cleanly than blur, dark and contrast.
- Rules, features and simulator severities were changed several times after seeing data; the history is in `results.md`. Treat the numbers as development estimates, not a preregistered test.
- View correctness, object identity and framing quality beyond "object found in the box" are not checked.
- Wikimedia is the only source that runs unattended. It rate-limits to roughly 6 to 10 photos a minute after a short burst. `scripts/scrape.py --source openverse` exists (Flickr and other CC photos, fast downloads) but anonymous Openverse search is throttled almost to zero; a free API key would fix that.

## Slide deck
The 5-slide presentation lives outside the repo (it embeds other people's phone photos). It was built from this repo's results and real screenshots of the app.

## Licensing
Scraped images are CC-licensed or public domain, with licence and creator stored per image. Many CC licences require attribution if you republish them; images are not committed to the repo.
