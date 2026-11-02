"""Helpers shared by the sidebar and the tabs of the interface.

The database is resolved at each call (``paths.db_path()``), never stored at import: Streamlit keeps this module
loaded between reruns while the sandbox and the tests point FITLY_DB at another database.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st

from fitly import config, paths
from fitly.apply.candidate_pack import render as _render_pack
from fitly.review import review_queue
from fitly.storage import connect
from fitly.tracking import tracker
from fitly.ui.offer_rows import Row

STATUS_FR = {
    "not_started": "à trier",
    "go": "GO",
    "skipped": "NO-GO",
    "prepared": "préparée",
    "submitted": "envoyée",
    "followed_up": "relancée",
    "interview": "entretien",
    "offer": "offre",
    "rejected": "refus",
    "withdrawn": "abandon",
    "no_response": "sans réponse",
}
TRACKED = ("submitted", "followed_up", "interview", "offer", "rejected", "withdrawn", "no_response")
PREP_NOTE = (
    " · préparation : les onglets s'affichent tour à tour le temps d'être remplis, puis retour au premier "
    "(laisse Chrome faire, ~10 s par offre)"
)
PREFILL_ICONS: dict[str | None, str] = {  # keyed by a report status, which may be missing
    "prefilled": "✅",
    "stopped": "⛔",
    "no_form": "⚠️",
    "captcha": "🧩",
    "platform": "🧭",
    "closed": "🚫",
    "login": "🔐",
}


def prefill_dir() -> Path:
    """Pre-fill run files, next to the database (sandbox copy stays separate)."""
    return paths.work_dir() / "prefill"


def _db_stamp() -> tuple[int, int, int]:
    """Changes whenever the database file is written: used to invalidate the cached queue."""
    try:
        st_ = os.stat(paths.db_path())
        prefs = os.stat(config.path("preferences.json")).st_mtime_ns  # excluded companies live there
        return (st_.st_mtime_ns, st_.st_size, prefs)
    except OSError:
        return (0, 0, 0)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_queue(db: str, stamp: tuple[int, int, int]) -> list[Row]:
    return review_queue.build(db)


def queue() -> list[Row]:
    """Review queue of the database in use, rebuilt only when the database or the preferences change."""
    return cached_queue(paths.db_path(), _db_stamp())


def job_row(job_id: int) -> tuple[Row | None, Row, list[Row]]:
    con = connect(paths.db_path())
    try:
        j = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        a = con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        ev = [
            dict(e)
            for e in con.execute("SELECT event_type,event_at,details FROM events WHERE job_id=? ORDER BY id", (job_id,))
        ]
    finally:
        con.close()
    return (dict(j) if j else None), (dict(a) if a else {}), ev


def applications(statuses: tuple[str, ...]) -> list[Row]:
    con = connect(paths.db_path())
    try:
        marks = ",".join("?" * len(statuses))
        rows = con.execute(
            f"""SELECT a.*, j.title, j.company, j.location, j.url, j.score, j.recommended_cv
                               FROM applications a JOIN jobs j ON j.id=a.job_id
                               WHERE a.status IN ({marks}) ORDER BY a.last_action_at DESC""",
            statuses,
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def shrink_headings(markdown: str) -> str:
    """Headings pushed three levels down, to fit inside an expander."""
    return re.sub(r"^(#+) ", lambda m: "#" * min(6, len(m.group(1)) + 3) + " ", markdown, flags=re.M)


def render_pack(job: Row | None, app: Row, events: list[Row]) -> str:
    """Candidate pack with headings shrunk to fit inside an expander."""
    return shrink_headings(_render_pack(job, app, events))


def apply_decisions(job_ids: list[int], decision: str, note: str | None = None, cv: str | None = None) -> int:
    """GO/NO-GO for several offers at once; returns how many were recorded. `cv` (GO only): the CV chosen
    for these applications, used by the pre-fill instead of each offer's recommended CV."""
    db = paths.db_path()
    for jid in job_ids:
        if cv and decision == "go":
            tracker.set_status(jid, "go", note=note or None, cv=cv, db_path=db)
        else:
            tracker.decide(jid, decision, reason=note or None, db_path=db)
    return len(job_ids)


def undo_many(job_ids: list[int]) -> int:
    for jid in job_ids:
        tracker.undo(jid, db_path=paths.db_path())
    return len(job_ids)


def application_rules() -> dict[str, Any]:
    return config.load("application_rules.json", default={})


def launch_prefill(job_ids: list[int], attach_cv: bool = True) -> None:
    """Open one tab per offer, pre-filled. Default: new tabs in the user's own Chrome, filled by the Fitly
    extension ("prefill_mode": "chrome_tab"); fallback "window": separate Chrome window driven by Playwright."""
    from fitly.sources import employer_match

    db = paths.db_path()
    closed = employer_match.verify_before_opening(job_ids, db)  # offer page gone: not opened, back out of GO
    for jid in closed:
        tracker.undo(jid, db_path=db, backup=False)
    job_ids = [j for j in job_ids if j not in closed]
    st.session_state["prefill_closed"] = len(closed)
    if not job_ids:
        return
    if application_rules().get("prefill_mode", "chrome_tab") == "chrome_tab":
        from fitly.apply import launch_review

        launch_review.open_tabs_for_extension(job_ids, no_cv=not attach_cv)
        return
    run_dir = prefill_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "-u",
        "-m",
        "fitly.apply.launch_review",
        "--jobs",
        ",".join(str(i) for i in job_ids),
        "--wait-close",
        "--mark-prepared",
    ] + ([] if attach_cv else ["--sans-cv"])
    (run_dir / "last_run.json").unlink(missing_ok=True)
    with open(run_dir / "last_run.log", "w", encoding="utf-8") as log:  # the child keeps its own handle
        subprocess.Popen(
            args,
            cwd=paths.ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "FITLY_DB": db},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def prefill_report() -> dict[str, Any] | None:
    p = prefill_dir() / "last_run.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def render_prefill_results(results: list[dict[str, Any]]) -> None:
    for res in results:
        icon = PREFILL_ICONS.get(res.get("status"), "❌")
        extra = ""
        if res.get("remaining_required"):
            extra += " · à compléter : " + ", ".join(x.split(chr(10))[0][:40] for x in res["remaining_required"][:4])
        if res.get("reason"):
            extra += f" · {res['reason']}"
        st.markdown(
            f"{icon} **{res.get('company') or ''}** — {res.get('title') or ''} : "
            f"{res.get('filled', 0)} champ(s) rempli(s){extra}"
        )


def flash(msg: str, icon: str = "✅") -> None:
    """Message shown as a toast at the next run (the current one ends with st.rerun())."""
    closed = st.session_state.pop("prefill_closed", 0)
    if closed:
        msg += f" · {closed} offre(s) expirée(s) écartée(s) avant ouverture"
    st.session_state["flash"] = (msg, icon)
