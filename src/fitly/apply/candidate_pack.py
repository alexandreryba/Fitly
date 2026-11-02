"""Create a compact human-facing candidate pack for one job (data/candidate_pack_<id>.md)."""

import sys
from pathlib import Path

from fitly import config, paths
from fitly.review.review_queue import explain
from fitly.scoring.pipeline import _required_years
from fitly.storage import connect


def _cv_file(cv_id):
    try:
        cvs = config.load("cv_profiles.json").get("cvs", [])
    except (OSError, ValueError):
        return None, False
    for c in cvs:
        if c.get("id") == cv_id:
            p = paths.project_path(c.get("path", ""))
            return c.get("path"), p.is_file()
    return None, False


def render(d, ad, events) -> str:
    cv = ad.get("cv_type") or d.get("recommended_cv")
    cv_path, cv_ok = _cv_file(cv)
    years = _required_years(d.get("description"))
    excerpt = " ".join((d.get("description") or "").split())[:1200]
    history = (
        "\n".join(
            f"- {e['event_at'][:10]} {e['event_type'].replace('status:', '')}"
            + (f" — {e['details']}" if e["details"] else "")
            for e in events
        )
        or "- none"
    )
    cv_line = f"{cv} → `{cv_path}`" + ("" if cv_ok else "  ⚠ FILE MISSING: add it before applying")
    return f"""# Candidate Pack — {d["company"]} — {d["title"]}

## Job
- Score: {d.get("score")} ({d.get("score_band")})
- Why: {explain(d.get("score_details")) or "n/a"}
- Experience required: {f"{years}+ years" if years else "not stated"}
- Location: {d.get("location")}
- Route: {d.get("application_route")}
- URL: {d.get("url")}

## CV to send
{cv_line}

## Application status
{ad.get("status", "not_started")} · applied {str(ad.get("applied_at") or "-")[:10]} · next follow-up {str(ad.get("next_follow_up_at") or "-")[:10]}

### History
{history}

## Offer excerpt
{excerpt or "No description stored."}

## Human checks
- [ ] Role matches target
- [ ] CV is appropriate
- [ ] Experience claims are truthful
- [ ] Mandatory questions reviewed
- [ ] Free-text answers reviewed
- [ ] CAPTCHA / anti-bot absent
- [ ] Final Submit clicked manually
- [ ] Then record it: `fitly applied {d["id"]} --cv {cv}`
"""


def main(argv=None, db=None, out_dir=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("Usage: fitly pack JOB_ID")
        return 2
    job_id = argv[0]
    con = connect(paths.db_path(db))
    j = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    a = con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
    events = [
        dict(e)
        for e in con.execute("SELECT event_type,event_at,details FROM events WHERE job_id=? ORDER BY id", (job_id,))
    ]
    con.close()
    if not j:
        print("Unknown job id")
        return 1
    p = Path(out_dir or paths.work_dir()) / f"candidate_pack_{job_id}.md"
    p.write_text(render(dict(j), dict(a) if a else {}, events), encoding="utf-8")
    print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
