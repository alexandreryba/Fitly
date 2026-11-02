"""Generate a local-only HTML review report from SQLite."""

import html
from datetime import UTC, datetime
from pathlib import Path

from fitly import paths
from fitly.review.review_queue import build
from fitly.tracking.tracker import due_followups

OUT = paths.DATA_DIR / "morning_review.html"


def _e(v):
    return html.escape(str(v if v is not None else ""))


def main(db=None, out=OUT, limit=100):
    db = paths.db_path(db)
    rows = [r for r in build(db) if r.get("quota") != "reserve"][:limit]
    due = due_followups(db_path=db)
    trs = "".join(
        "<tr>"
        + "".join(
            [
                f"<td>{_e(r['id'])}</td>",
                f"<td><b>{_e(int(r['score'] or 0))}</b></td>",
                f"<td>{_e(r['score_band'])}</td>",
                f"<td>{_e(r['freshness'])}</td>",
                f"<td>{_e(r['company'])}</td>",
                f"<td>{_e(r['title'])}</td>",
                f"<td>{_e(r['location'])}</td>",
                f"<td>{_e(r['recommended_cv'])}</td>",
                f"<td class=why>{_e(r['why'])}</td>",
                f"<td>{_e(r['application_status'])}</td>",
                f"<td>{_e(r['human_action'])}</td>",
                f"<td><a href='{_e(r['url'])}'>open</a></td>",
            ]
        )
        + "</tr>"
        for r in rows
    )
    fus = (
        "".join(
            f"<li>{_e(x['company'])} — {_e(x['title'])} · envoyée {_e(str(x['applied_at'])[:10])} · "
            f"{_e(x['suggested_action'])} (id {_e(x['job_id'])})</li>"
            for x in due
        )
        or "<li>Aucune</li>"
    )
    doc = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Fitly morning review</title>
<style>body{{font-family:Arial,sans-serif;margin:30px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ddd;padding:6px;text-align:left;font-size:13px}}th{{position:sticky;top:0;background:#eee}}
td.why{{color:#555;font-size:12px}}</style></head><body><h1>Fitly — Morning Review</h1>
<p>Generated {datetime.now(UTC).isoformat(timespec="seconds")} · top {len(rows)} open offers</p>
<h2>Relances dues</h2><ul>{fus}</ul>
<h2>Offres</h2>
<table><tr><th>id</th><th>Score</th><th>Band</th><th>New</th><th>Company</th><th>Title</th><th>Location</th><th>CV</th>
<th>Why</th><th>Status</th><th>Action</th><th>URL</th></tr>{trs}</table>
<p>Go/no-go : <code>fitly decide ID go|no</code> ou colonne <code>decision</code> du CSV.
La soumission finale reste manuelle.</p></body></html>"""
    Path(out).write_text(doc, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
