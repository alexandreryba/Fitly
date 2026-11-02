"""Plain-text dashboard for the terminal (`fitly dashboard`): best offers and follow-ups due."""

from datetime import UTC, datetime
from typing import Any

from fitly.storage import connect


def get_dashboard(limit=25, db_path=None) -> dict[str, Any]:
    c = connect(db_path)
    jobs = c.execute(
        """
        SELECT j.*, a.status AS application_status, a.next_follow_up_at
        FROM jobs j
        LEFT JOIN applications a ON a.job_id = j.id
        ORDER BY j.score DESC, j.last_seen_at DESC
        LIMIT ?
    """,
        (limit,),
    ).fetchall()
    due = c.execute(
        """
        SELECT a.*, j.title, j.company, j.url
        FROM applications a JOIN jobs j ON j.id=a.job_id
        WHERE a.next_follow_up_at IS NOT NULL
          AND a.next_follow_up_at <= ?
          AND a.status IN ('submitted','followed_up')
        ORDER BY a.next_follow_up_at
    """,
        (datetime.now(UTC).isoformat(),),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in c.execute("SELECT status, COUNT(*) n FROM applications GROUP BY status"):
        counts[row["status"]] = row["n"]
    c.close()
    return {"jobs": [dict(x) for x in jobs], "followups": [dict(x) for x in due], "counts": counts}


def render_text(limit=25, db_path=None) -> str:
    d = get_dashboard(limit, db_path)
    lines = ["FITLY DASHBOARD", "=" * 72, ""]
    lines.append("APPLICATIONS: " + (", ".join(f"{k}={v}" for k, v in sorted(d["counts"].items())) or "none"))
    lines.append("")
    lines.append("TOP JOBS")
    for j in d["jobs"]:
        route = j.get("application_route") or "?"
        band = j.get("score_band") or "?"
        lines.append(f"[{j.get('score', 0):>3}] {band:<16} {j['company']} — {j['title']} | {route}")
    lines.append("")
    lines.append("FOLLOW-UPS DUE")
    if d["followups"]:
        for x in d["followups"]:
            lines.append(f"- {x['company']} — {x['title']} | due {x['next_follow_up_at']}")
    else:
        lines.append("- none")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_text())
