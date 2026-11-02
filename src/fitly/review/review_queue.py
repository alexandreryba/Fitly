"""Create a deterministic human-review queue from SQLite (JSON with go/no-go context)."""

import json
from datetime import UTC
from pathlib import Path

from fitly import config, paths
from fitly.storage import connect

OUT = paths.DATA_DIR / "review_queue.json"

# Closed applications (no-go, rejected, offer...) leave the queue.
OPEN_STATUSES = ("not_started", "new", "review", "ready", "go", "prepared", "submitted", "followed_up", "interview")
LABELS = {
    "role": "role",
    "skills": "skills",
    "location": "loc",
    "internship": "stage",
    "seniority": "niveau",
    "age": "ancienneté",
    "unrelated_role": "off-target",
    "freelance": "freelance",
    "salary": "salary",
    "avoid_company_cap": "avoid-cap",
}


def explain(details):
    """'Data Engineer | role +47, skills +12, loc +15, seniority -10' from the stored JSON."""
    try:
        d = json.loads(details or "")
    except ValueError:
        return ""
    parts = [
        f"{LABELS.get(k, k)} {v:+d}"
        for k, v in (d.get("breakdown") or {}).items()
        if k != "base" and isinstance(v, int)
    ]
    return f"{d.get('matched_role') or '-'} | {', '.join(parts)}"


def human_action(d):
    status = d["application_status"]
    if status == "go":
        return "prepare_application"
    if status == "prepared":
        return "submit_manually"
    if status in ("submitted", "followed_up"):
        return "await_reply"
    if status == "interview":
        return "interview"
    band = d.get("score_band")
    if band in ("apply_now", "strong_match"):
        return "review_and_prepare"
    if band == "interesting":
        return "review"
    return "low_priority"


# Sources re-collected by every morning run; manual imports are never marked stale.
COLLECTED_SOURCES = ("lever", "greenhouse", "ashby", "smartrecruiters", "workable", "recruitee", "teamtailor")

# Keyword-search sources only list live offers: an offer they stopped returning is probably expired.
SEARCH_SOURCES = ("google_jobs", "jooble", "adzuna", "france_travail")
SEARCH_STALE_DAYS = 14


def freshness(d, last_run, seen_companies):
    """'new' (first seen in the latest run), 'closed?' (company re-collected without it) or ''.

    upsert_jobs stamps every offer of one run with the same timestamp, so the
    latest run is exactly MAX(last_seen_at) of the collected sources.
    """
    if not last_run or not d.get("last_seen_at"):
        return ""
    if d.get("source") in COLLECTED_SOURCES and d["last_seen_at"] < last_run and d.get("company") in seen_companies:
        return "closed?"
    if d.get("first_seen_at") == last_run:
        return "new"
    return ""


def _company_key(name):
    import re
    import unicodedata

    t = "".join(c for c in unicodedata.normalize("NFKD", str(name or "")) if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def excluded_companies(path=None):
    try:
        return [
            c
            for c in json.loads(Path(path or config.path("preferences.json")).read_text(encoding="utf-8")).get(
                "excluded_companies", []
            )
            if str(c).strip()
        ]
    except (OSError, ValueError):
        return []


def is_excluded(company, names):
    """'Acme Partners France' matches 'Acme Partners'; 'BluePoint' matches 'Blue Point'.
    'Globex' does NOT match 'Globex Consulting' (list each entity to exclude)."""
    c = _company_key(company)
    if not c:
        return False
    for name in names:
        n = _company_key(name)
        if not n:
            continue
        words = c.split()
        # the company's first words, spaces ignored, spell the excluded name ('BluePoint France' / 'Blue Point')
        if any("".join(words[:k]) == n.replace(" ", "") for k in range(1, len(words) + 1)):
            return True
    return False


def build(db=None, include_closed=False):
    db = paths.db_path(db)
    con = connect(str(db))
    last_run = con.execute(
        f"SELECT MAX(last_seen_at) FROM jobs WHERE source IN ({','.join('?' * len(COLLECTED_SOURCES))})",
        COLLECTED_SOURCES,
    ).fetchone()[0]
    seen_companies = {r[0] for r in con.execute("SELECT DISTINCT company FROM jobs WHERE last_seen_at=?", (last_run,))}
    marks = ",".join("?" * len(OPEN_STATUSES))
    rows = con.execute(
        f"""
      SELECT j.id,j.company,j.title,j.location,COALESCE(j.direct_url,j.url) AS url,j.score,j.score_band,
             j.availability,j.direct_url IS NOT NULL AS direct,
             j.recommended_cv,j.application_route,j.score_details,
             j.source,j.first_seen_at,j.last_seen_at,j.posted_at,j.employment_type,j.via,
             COALESCE(a.status,'not_started') AS application_status,
             a.next_follow_up_at
      FROM jobs j LEFT JOIN applications a ON a.job_id=j.id
      WHERE COALESCE(a.status,'not_started') IN ({marks})
      ORDER BY j.score DESC, j.last_seen_at DESC
    """,
        OPEN_STATUSES,
    ).fetchall()
    con.close()
    queue = []
    excluded = excluded_companies()
    from datetime import datetime, timedelta

    stale_before = (datetime.now(UTC) - timedelta(days=SEARCH_STALE_DAYS)).isoformat(timespec="seconds")
    for r in rows:
        d = dict(r)
        if d["application_status"] == "not_started" and is_excluded(d["company"], excluded):
            continue  # companies the user never applies to (config/preferences.json)
        if (
            d["application_status"] == "not_started"
            and not include_closed
            and d.get("source") in SEARCH_SOURCES
            and not d.get("direct")
            and (d.get("last_seen_at") or "") < stale_before
        ):
            continue  # search result not seen again for 14 days: the listing has most likely expired
        if d["application_status"] == "not_started" and d.get("availability") == "closed" and not include_closed:
            continue  # expired (employer page gone, or seen 'no longer available' while pre-filling)
        details = d.pop("score_details")
        try:
            d["matched_role"] = json.loads(details).get("matched_role") if details else None
        except ValueError:
            d["matched_role"] = None
        d["why"] = explain(details)
        d["freshness"] = freshness(d, last_run, seen_companies)
        if d["freshness"] == "closed?" and d["application_status"] == "not_started" and not include_closed:
            continue
        d["human_action"] = human_action(d)
        queue.append(d)
    from fitly.scoring import company_quota

    return company_quota.apply(queue, str(db))


def main(db=None, out=OUT):
    queue = build(db)
    Path(out).write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(queue)} jobs -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
