"""Application tracking: go/no-go decisions, statuses, follow-ups and offline rescoring.

Local SQLite only. Nothing here contacts an employer or submits anything.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fitly import config, paths
from fitly.storage import connect, score_details

# Workflow: (no row) -> go | skipped -> prepared -> submitted -> followed_up -> interview -> offer
STATUSES = (
    "go",
    "skipped",
    "prepared",
    "submitted",
    "followed_up",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "no_response",
)
CLOSED = ("skipped", "offer", "rejected", "withdrawn", "no_response")
# Statuses that wait for an employer answer and therefore get follow-up reminders.
AWAITING_REPLY = ("submitted", "followed_up")
# A CSV/bulk go/no-go never overrides an application already sent or closed.
DECIDABLE = (None, "new", "review", "go", "skipped", "prepared")

GO_WORDS = {"go", "oui", "yes", "y", "o", "1", "ok", "x"}
NO_WORDS = {"no", "non", "n", "0", "nogo", "no-go", "no go", "skip", "skipped"}


def _now():
    return datetime.now(UTC)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _parse_date(value):
    """Accept YYYY-MM-DD, DD/MM/YYYY or a full ISO timestamp; return an aware datetime."""
    if value is None or value == "":
        return _now()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def rules(path=None):
    p = Path(path) if path else config.path("application_rules.json")
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {"follow_up_days": int(data.get("follow_up_days", 7)), "max_follow_ups": int(data.get("max_follow_ups", 2))}


def _job(con, job_id):
    row = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown job id: {job_id}")
    return row


def _application(con, job_id):
    return con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()


def _follow_up_count(con, job_id):
    return con.execute(
        "SELECT COUNT(*) FROM events WHERE job_id=? AND event_type='status:followed_up'", (job_id,)
    ).fetchone()[0]


def set_status(job_id, status, note=None, when=None, cv=None, db_path=None, rules_path=None):
    """Record a status change and (re)schedule the next follow-up when a reply is awaited."""
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'. Use one of: {', '.join(STATUSES)}")
    cfg = rules(rules_path)
    at = _parse_date(when)
    con = connect(db_path)
    try:
        job = _job(con, job_id)
        app = _application(con, job_id)
        applied_at = app["applied_at"] if app else None
        if status == "submitted":
            applied_at = _iso(at)
        next_follow_up = None
        if status in AWAITING_REPLY:
            next_follow_up = _iso(at + timedelta(days=cfg["follow_up_days"]))
        cv_type = cv or (app["cv_type"] if app and app["cv_type"] else None)
        if status in ("prepared", "submitted") and not cv_type:
            cv_type = job["recommended_cv"]
        notes = app["notes"] if app else None
        if note:
            notes = f"{notes}\n" if notes else ""
            notes += f"[{at.date().isoformat()}] {status}: {note}"
        if app:
            con.execute(
                """UPDATE applications SET status=?, cv_type=?, route=?, applied_at=?,
                           next_follow_up_at=?, last_action_at=?, notes=? WHERE job_id=?""",
                (status, cv_type, job["application_route"], applied_at, next_follow_up, _iso(at), notes, job_id),
            )
            app_id = app["id"]
        else:
            cur = con.execute(
                """INSERT INTO applications(job_id, cv_type, route, status, applied_at,
                                 next_follow_up_at, last_action_at, notes) VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, cv_type, job["application_route"], status, applied_at, next_follow_up, _iso(at), notes),
            )
            app_id = cur.lastrowid
        con.execute(
            "INSERT INTO events(job_id, application_id, event_type, event_at, details) VALUES(?,?,?,?,?)",
            (job_id, app_id, f"status:{status}", _iso(at), note or ""),
        )
        con.commit()
        return {
            "job_id": int(job_id),
            "company": job["company"],
            "title": job["title"],
            "status": status,
            "cv": cv_type,
            "applied_at": applied_at,
            "next_follow_up_at": next_follow_up,
        }
    finally:
        con.close()


def undo(job_id, db_path=None, backup=True):
    """Put an offer back to 'à trier': removes its decision/application and the related events
    (so a mistaken 'submitted' or time entry no longer counts in the statistics). DB backed up first."""
    if backup:
        backup_db(db_path, "before_undo")
    con = connect(db_path)
    try:
        job = _job(con, job_id)
        app = _application(con, job_id)
        previous = app["status"] if app else None
        con.execute("DELETE FROM applications WHERE job_id=?", (job_id,))
        con.execute("DELETE FROM events WHERE job_id=?", (job_id,))
        con.execute(
            "INSERT INTO events(job_id, event_type, event_at, details) VALUES(?, 'undo', ?, ?)",
            (job_id, _iso(_now()), f"was {previous}"),
        )
        con.commit()
        return {"job_id": int(job_id), "company": job["company"], "title": job["title"], "previous_status": previous}
    finally:
        con.close()


def prior_applications(job_id, db_path=None, threshold=0.8):
    """Applications already sent for the same company and a very similar title (other job ids).

    One offer = one application: callers must stop unless the user explicitly overrides.
    """
    from difflib import SequenceMatcher

    from fitly.scoring.novelty import norm

    con = connect(db_path)
    try:
        job = _job(con, job_id)
        rows = con.execute(
            """SELECT j.id, j.title, j.company, j.location, a.status, a.applied_at
                              FROM applications a JOIN jobs j ON j.id=a.job_id
                              WHERE a.applied_at IS NOT NULL AND j.id<>?""",
            (job_id,),
        ).fetchall()
    finally:
        con.close()
    company, title = norm(job["company"]), norm(job["title"])
    return [
        dict(r)
        for r in rows
        if norm(r["company"]) == company and SequenceMatcher(None, title, norm(r["title"])).ratio() >= threshold
    ]


def parse_decision(value):
    word = str(value or "").strip().lower()
    if word in GO_WORDS:
        return "go"
    if word in NO_WORDS:
        return "skipped"
    return None


def decide(job_id, decision, reason=None, db_path=None):
    status = parse_decision(decision)
    if status is None:
        raise ValueError(f"Decision must be go/oui or no/non, got '{decision}'")
    return set_status(job_id, status, note=reason, db_path=db_path)


def import_decisions(csv_path, db_path=None):
    """Apply the 'decision' column of an edited review CSV (comma or semicolon separated)."""
    raw = Path(csv_path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # Excel "CSV (séparateur : point-virgule)" saves in ANSI
    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except (csv.Error, IndexError):
        dialect = csv.excel
    result = {"go": 0, "skipped": 0, "unchanged": 0, "protected": 0, "invalid": []}
    con = connect(db_path)
    current = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    con.close()
    for row in csv.DictReader(text.splitlines(), dialect=dialect):
        raw = (row.get("decision") or "").strip()
        if not raw:
            continue
        status = parse_decision(raw)
        try:
            job_id = int(str(row.get("id") or "").strip())
        except ValueError:
            job_id = None
        if status is None or job_id is None:
            result["invalid"].append({"id": row.get("id"), "decision": raw})
            continue
        existing = current.get(job_id)
        if existing == status:
            result["unchanged"] += 1
        elif existing not in DECIDABLE:
            result["protected"] += 1
        else:
            set_status(job_id, status, note=(row.get("decision_note") or "").strip() or None, db_path=db_path)
            result[status] += 1
    return result


def pending_decisions(csv_path):
    """True if the review CSV holds at least one filled 'decision' cell."""
    p = Path(csv_path)
    if not p.exists():
        return False
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    lines = text.splitlines()
    if not lines:
        return False
    try:
        dialect = csv.Sniffer().sniff(lines[0], delimiters=",;	")
    except csv.Error:
        dialect = csv.excel
    return any((row.get("decision") or "").strip() for row in csv.DictReader(lines, dialect=dialect))


def due_followups(today=None, db_path=None, rules_path=None):
    cfg = rules(rules_path)
    limit = _iso(_parse_date(today) if today else _now())
    con = connect(db_path)
    try:
        rows = con.execute(
            f"""SELECT a.*, j.title, j.company, j.location, j.url, j.recommended_cv
                               FROM applications a JOIN jobs j ON j.id=a.job_id
                               WHERE a.status IN ({",".join("?" * len(AWAITING_REPLY))})
                                 AND a.next_follow_up_at IS NOT NULL AND a.next_follow_up_at <= ?
                               ORDER BY a.next_follow_up_at""",
            (*AWAITING_REPLY, limit),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["follow_up_count"] = _follow_up_count(con, d["job_id"])
            d["suggested_action"] = (
                "close_as_no_response"
                if d["follow_up_count"] >= cfg["max_follow_ups"]
                else f"follow_up_{d['follow_up_count'] + 1}"
            )
            out.append(d)
        return out
    finally:
        con.close()


def followup_draft(item, sender_name=""):
    """Plain follow-up template. The user completes and sends it themself."""
    lang = "en" if str(item.get("cv_type") or item.get("recommended_cv") or "").endswith("_en") else "fr"
    applied = str(item.get("applied_at") or "")[:10]
    if lang == "fr":
        body = (
            f"Objet : Suivi de ma candidature – {item['title']}\n\n"
            f"Bonjour [Nom du contact],\n\n"
            f"Je me permets de revenir vers vous au sujet de ma candidature au poste de {item['title']} "
            f"chez {item['company']}, envoyée le {applied}.\n"
            f"Ce poste m'intéresse toujours vivement et je reste disponible pour un échange "
            f"afin de vous présenter mon parcours plus en détail.\n\n"
            f"Bien cordialement,\n{sender_name}\n"
        )
    else:
        body = (
            f"Subject: Following up on my application – {item['title']}\n\n"
            f"Hello [Contact name],\n\n"
            f"I am following up on my application for the {item['title']} position at {item['company']}, "
            f"sent on {applied}.\n"
            f"I remain very interested in the role and would be glad to discuss my background with you.\n\n"
            f"Best regards,\n{sender_name}\n"
        )
    return f"<!-- {item.get('url') or ''} -->\n{body}"


def write_followup_drafts(items, out_dir=None, sender_name=""):
    out = Path(out_dir) if out_dir else paths.work_dir() / "followups"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for item in items:
        if item.get("suggested_action") == "close_as_no_response":
            continue
        p = out / f"relance_{item['job_id']}_{item['follow_up_count'] + 1}.txt"
        p.write_text(followup_draft(item, sender_name), encoding="utf-8")
        written.append(p)
    return written


def backup_db(db_path=None, label="backup", keep=14):
    """Copy the database to backups/db/, keeping the `keep` most recent copies per label."""
    src = paths.project_path(paths.db_path(db_path))
    if not src.exists():
        return None
    # The real database is backed up in the project; any other copy (sandbox, tests) next to itself.
    dest_dir = paths.BACKUP_DIR / "db" if src.resolve() == paths.DEFAULT_DB.resolve() else src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"fitly_{label}_{_now().strftime('%Y%m%dT%H%M%S')}.db"
    shutil.copy2(src, dest)
    for old in sorted(dest_dir.glob(f"fitly_{label}_*.db"))[:-keep]:
        old.unlink()
    return dest


def rescore(preferences, profile, cv_profiles, db_path=None, backup=True):
    """Recompute score/band/CV/explanation for every stored job, without network access.

    Rows are updated in place by id: no insertion, no merge, applications untouched.
    """
    from fitly.scoring.pipeline import _annotate

    saved = backup_db(db_path, "before_rescore") if backup else None
    con = connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, source, external_id, title, company, location, description, application_route, employment_type, posted_at FROM jobs"
        ).fetchall()
        changed = 0
        for r in rows:
            job = {k: (v or "") for k, v in dict(r).items()}
            old = con.execute("SELECT score FROM jobs WHERE id=?", (r["id"],)).fetchone()[0]
            _annotate(job, preferences, profile, cv_profiles)
            con.execute(
                "UPDATE jobs SET score=?, score_band=?, recommended_cv=?, score_details=? WHERE id=?",
                (job["score"], job["score_band"], job["recommended_cv"], score_details(job), r["id"]),
            )
            changed += int(old != job["score"])
        con.commit()
        return {"jobs": len(rows), "score_changed": changed, "backup": str(saved) if saved else None}
    finally:
        con.close()
