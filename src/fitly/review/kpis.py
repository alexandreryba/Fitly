"""Key indicators from the project brief: coverage, automation, efficiency (interviews per hour)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fitly import paths
from fitly.storage import connect

RELEVANT = 55  # score from which an offer is worth a human look ("interesting" band)
AUTOMATABLE_ROUTES = ("external_ats",)
MANUAL_ROUTES = ("linkedin_easy_apply", "linkedin_check")
REPLIED = ("interview", "offer", "rejected")


def last_run_summary(path=None) -> dict[str, Any]:
    p = Path(path) if path else paths.work_dir() / "morning_run.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("summary", {})
    except (OSError, ValueError):
        return {}


def compute(db_path=None, today=None, run_summary=None, queue=None) -> dict[str, Any]:
    from fitly.review.review_queue import build
    from fitly.tracking.tracker import due_followups

    con = connect(db_path)
    try:

        def q(sql, *params):
            return con.execute(sql, params).fetchone()[0]

        jobs = q("SELECT COUNT(*) FROM jobs")
        relevant = q("SELECT COUNT(*) FROM jobs WHERE score>=?", RELEVANT)
        by_source = dict(
            con.execute(
                """SELECT s.source, COUNT(DISTINCT s.job_id) FROM job_sources s
                                        JOIN jobs j ON j.id=s.job_id WHERE j.score>=? GROUP BY s.source""",
                (RELEVANT,),
            ).fetchall()
        )
        multi_source = q("SELECT COUNT(*) FROM (SELECT job_id FROM job_sources GROUP BY job_id HAVING COUNT(*)>1)")
        submitted = q("SELECT COUNT(*) FROM applications WHERE applied_at IS NOT NULL")

        def ever(status):
            return q("SELECT COUNT(DISTINCT job_id) FROM events WHERE event_type=?", f"status:{status}")

        interviews, offers, rejected = ever("interview"), ever("offer"), ever("rejected")
        replied = q(
            f"""SELECT COUNT(DISTINCT job_id) FROM events WHERE event_type IN ({",".join("?" * len(REPLIED))})""",
            *[f"status:{s}" for s in REPLIED],
        )
        minutes = q("SELECT COALESCE(SUM(CAST(details AS REAL)),0) FROM events WHERE event_type='effort'")
        statuses = dict(con.execute("SELECT status, COUNT(*) FROM applications GROUP BY status").fetchall())
    finally:
        con.close()
    queue = build(db_path) if queue is None else queue  # reuse the caller's queue: building it is the costly part
    triage = [r for r in queue if (r["score"] or 0) >= RELEVANT and r["application_status"] == "not_started"]
    open_relevant = [r for r in triage if r.get("quota") != "reserve"]
    reserve_relevant = len(triage) - len(open_relevant)
    automatable = sum(1 for r in open_relevant if r["application_route"] in AUTOMATABLE_ROUTES)
    manual = sum(1 for r in open_relevant if r["application_route"] in MANUAL_ROUTES)
    hours = minutes / 60
    run = run_summary if run_summary is not None else last_run_summary()
    return {
        "jobs_known": jobs,
        "new_offers": sum(1 for r in triage if r.get("freshness") == "new"),  # relevant ones only (score >= 55)
        "new_all": sum(1 for r in queue if r.get("freshness") == "new"),
        "relevant_offers": relevant,
        "to_review": len(open_relevant),
        "reserve_relevant": reserve_relevant,
        "duplicates_last_run": (run.get("collected", 0) - run.get("processed", 0)) + run.get("duplicates", 0),
        "multi_source_offers": multi_source,
        "automatable_to_review": automatable,
        "manual_easy_apply_to_review": manual,
        "other_route_to_review": len(open_relevant) - automatable - manual,
        "automation_rate": round(automatable / len(open_relevant), 3) if open_relevant else None,
        "followups_due": len(due_followups(today=today, db_path=db_path)),
        "coverage_by_source": by_source,
        "submitted": submitted,
        "replied": replied,
        "interviews": interviews,
        "offers": offers,
        "rejected": rejected,
        "response_rate": round(replied / submitted, 3) if submitted else None,
        "interview_rate": round(interviews / submitted, 3) if submitted else None,
        "hours_logged": round(hours, 1),
        "interviews_per_hour": round(interviews / hours, 3) if hours else None,
        "statuses": statuses,
    }


def log_effort(job_id, minutes, db_path=None) -> None:
    """Record time spent on an application (feeds 'interviews per hour of work')."""
    if minutes is None or float(minutes) <= 0:
        return
    con = connect(db_path)
    try:
        con.execute(
            "INSERT INTO events(job_id, event_type, event_at, details) VALUES(?, 'effort', datetime('now'), ?)",
            (job_id, str(float(minutes))),
        )
        con.commit()
    finally:
        con.close()
