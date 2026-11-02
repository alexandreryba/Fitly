"""Export the current human-review queue to CSV for easy triage.

Fill the 'decision' column with go/oui or no/non (optional 'decision_note'),
save the file, then run: fitly decisions
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from fitly.paths import DATA_DIR

INP = DATA_DIR / "review_queue.json"
OUT = DATA_DIR / "review_queue.csv"

FIELDS = [
    "id",
    "decision",
    "decision_note",
    "score",
    "score_band",
    "freshness",
    "quota",
    "quota_reason",
    "company",
    "title",
    "location",
    "recommended_cv",
    "why",
    "application_status",
    "human_action",
    "next_follow_up_at",
    "application_route",
    "url",
]


# ';' because Excel on a French-locale Windows splits columns on ';' (list separator).
def main(inp=INP, out=OUT, delimiter=";") -> int:
    inp, out = Path(inp), Path(out)
    if not inp.exists():
        print("Missing review_queue.json; run `fitly review` first.")
        return 1
    rows = json.loads(inp.read_text(encoding="utf-8"))
    try:
        f = out.open("w", newline="", encoding="utf-8-sig")
    except PermissionError:
        # Typically the CSV is open in Excel: never fail the morning run for that.
        out = out.with_name(f"{out.stem}_{datetime.now():%Y%m%d_%H%M%S}{out.suffix}")
        print(f"WARNING: the CSV is open/locked (Excel?). Wrote {out.name} instead.")
        f = out.open("w", newline="", encoding="utf-8-sig")
    with f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", delimiter=delimiter)
        w.writeheader()
        w.writerows(rows)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(delimiter="," if "--comma" in sys.argv else ";"))
