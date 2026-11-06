"""Score the user's phone photos with a FROZEN threshold file and write a markdown report. Run it BEFORE tuning on new phone photos.
  uv run scripts/phone_baseline.py                                    # thresholds_prephone.json on all phone photos -> phone_step3.md
  uv run scripts/phone_baseline.py --thresholds thresholds_step6.json --only-ids ids.txt --out phone_fresh.md --title "..."
Do not tune against what this prints."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.evaluate import DATA, THRESHOLDS, compute, load, phone_section  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default="thresholds_prephone.json")
    ap.add_argument("--only-ids", help="file with one phone image id per line; default: all phone photos")
    ap.add_argument("--out", default="phone_step3.md")
    ap.add_argument("--title", default="Phone photos, thresholds frozen BEFORE any phone tuning")
    a = ap.parse_args()
    items, *_ = load()
    if a.only_ids:
        want = set(Path(a.only_ids).read_text().split())
        items = [it for it in items if it["split"] != "phone" or it["id"] in want]
    items = compute([it for it in items if it["split"] == "phone"], False) if a.only_ids else compute(items, False)
    frozen = {**THRESHOLDS, **json.loads((DATA / a.thresholds).read_text())}
    text = phone_section(items, frozen, a.title)
    (DATA / a.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
