"""Follow applications from the user's e-mails: confirmations ("candidature reçue"), rejections, interviews.

Input: messages as dicts {id, date, from, subject, body} -- from data/mail/messages.json (written by Claude
from the user's mailbox connector, or by hand), a folder of .eml files, or IMAP (read-only, same settings as
email_alerts). Output: PROPOSED status changes (data/mail/proposals.json); nothing changes in the database
until the user confirms them in the interface ("📬 Depuis tes e-mails") or runs --apply.

    python -m fitly.tracking.mail_tracker --json data/mail/messages.json  # analyse, write proposals
    python -m fitly.tracking.mail_tracker --apply                         # apply the pending proposals
"""

from __future__ import annotations

import email
import json
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from fitly import paths
from fitly.storage import connect

REJECTED = re.compile(
    r"malheureusement|nous (ne pouvons|ne pourrons) pas (donner|r[ée]pondre)|pas (pu )?(été )?(retenue?|donner suite)|"
    r"ne (pas )?donner suite|suite d[ée]favorable|ne correspond(ait)? pas|(d'autres|autres) (candidat|profils)|"
    r"unfortunately|not (be )?moving forward|decided (not to|to (move forward|pursue|proceed) with other)|"
    r"not (been )?selected|regret to inform|will not be (moving|proceeding)|position has been filled|"
    r"other candidates (whose|who)|we have decided to",
    re.I,
)
INTERVIEW = re.compile(
    r"\b(entretien|interview)s?\b.{0,80}(planifi|programm|organis|propos|disponibilit|cr[ée]neau|invit|schedul|book)|"
    r"(planifi|programm|organis|propos|schedul|book|invite).{0,60}\b(entretien|interview|call|appel|[ée]change|visio)\b|"
    r"calendly\.com|cal\.com/|meet\.google\.com|teams\.microsoft\.com|zoom\.us/j|"
    r"vos disponibilit[ée]s|your availability|prochaine [ée]tape du processus|next step(s)? (in|of) (the|our) process",
    re.I,
)
APPLIED = re.compile(
    r"candidature (a (bien )?[ée]t[ée] |bien )?(re[çc]ue|envoy[ée]e|enregistr[ée]e|transmise|prise en compte)|"
    r"merci (pour|de) (votre|ta) candidature|nous avons (bien )?re[çc]u (votre|ta) candidature|"
    r"thank(s| you) for (your )?(applying|application|interest in)|we('ve| have) received your application|"
    r"application (has been )?(received|submitted|sent)|your application (to|for|was sent)|"
    r"votre candidature a [ée]t[ée] envoy[ée]e",
    re.I,
)
# Platforms whose notification names the company in the subject.
SUBJECT_COMPANY = [
    re.compile(
        r"(?:votre candidature a [ée]t[ée] envoy[ée]e [àa]|your application was sent to)\s+(.+?)\s*$", re.I
    ),  # LinkedIn
    re.compile(r"(?:candidature (?:chez|pour|[àa])|application (?:to|at|with))\s+(.+?)(?:\s*[-–:|]|\s*$)", re.I),
    re.compile(r"^(.+?)\s*[-–:|]\s*(?:candidature|application|votre candidature|your application)", re.I),
]
ATS_SENDERS = (
    "lever.co",
    "greenhouse",
    "ashbyhq",
    "smartrecruiters",
    "workable",
    "teamtailor",
    "recruitee",
    "welcometothejungle",
    "linkedin",
    "indeed",
    "workday",
    "successfactors",
    "taleo",
    "jobteaser",
    "hellowork",
    "apec",
    "talent",
    "recrutement",
    "recruiting",
    "careers",
    "jobs",
    "rh",
    "hr",
)
RANK = {"submitted": 1, "followed_up": 1, "interview": 2, "rejected": 3, "offer": 4}


def _fold(t):
    t = "".join(c for c in unicodedata.normalize("NFKD", str(t or "")) if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def classify(msg):
    """'rejected' / 'interview' / 'submitted' / None. A rejection wins (it often thanks you for applying too)."""
    text = f"{msg.get('subject', '')}\n{msg.get('body', '')}"[:20000]
    if REJECTED.search(text):
        return "rejected"
    # "Si votre profil correspond, nous vous proposerons un entretien" is not an invitation.
    firm = re.sub(
        r"[^.!?\n]*\b(si (votre|ton|nous|vous)|if (your|we|you|there)|should (your|we|there)|"
        r"le cas [ée]ch[ée]ant|we may|nous pourrions|pourrons [ée]ventuellement)\b[^.!?\n]*",
        " ",
        text,
        flags=re.I,
    )
    if INTERVIEW.search(firm):
        return "interview"
    if APPLIED.search(text):
        return "submitted"
    return None


def company_hint(msg):
    subject = re.sub(r"^(re|tr|fwd?)\s*:\s*", "", str(msg.get("subject") or ""), flags=re.I).strip()
    for rx in SUBJECT_COMPANY:
        m = rx.search(subject)
        if m and 1 < len(m.group(1)) < 60:
            return m.group(1).strip(" .!\"'")
    name, addr = parseaddr(str(msg.get("from") or ""))
    name = re.sub(
        r"\b(via|par|recruiting|recrutement|careers?|talent|team|jobs?|hiring|rh|hr|no.?reply)\b", " ", name, flags=re.I
    )
    if name.strip(" -|@"):
        return name.strip(" -|@")
    domain = addr.split("@")[-1].split(".")
    return domain[-2] if len(domain) >= 2 and not any(a in addr for a in ATS_SENDERS) else ""


def match_job(con, msg, hint):
    """Best known offer for this message: same company (name in sender/subject/body), closest title."""
    blob = _fold(f"{msg.get('from', '')} {msg.get('subject', '')} {str(msg.get('body', ''))[:4000]}")
    hint_f = _fold(hint)
    best, best_score = None, 0.0
    for r in con.execute(
        "SELECT j.id, j.company, j.title, COALESCE(a.status,'') AS status FROM jobs j "
        "LEFT JOIN applications a ON a.job_id=j.id"
    ):
        comp = _fold(r["company"])
        if not comp or len(comp) < 3:
            continue
        named = (
            hint_f and (hint_f == comp or hint_f.startswith(comp + " ") or comp.startswith(hint_f + " "))
        ) or re.search(rf"(?<![a-z0-9]){re.escape(comp)}(?![a-z0-9])", blob)
        if not named:
            continue
        words = set(_fold(r["title"]).split()) - {"h", "f", "hf", "cdi", "de", "en", "the", "and"}
        overlap = len(words & set(blob.split())) / max(1, len(words))
        score = 1 + overlap + (0.5 if r["status"] in ("go", "prepared", "submitted", "followed_up", "interview") else 0)
        if score > best_score:
            best, best_score = dict(r), score
    return best


def _date(msg):
    d = msg.get("date")
    try:
        dt = (
            parsedate_to_datetime(d)
            if isinstance(d, str) and not re.match(r"\d{4}-\d{2}-\d{2}", d)
            else datetime.fromisoformat(str(d))
        )
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).date().isoformat()
    except (TypeError, ValueError):
        return datetime.now(UTC).date().isoformat()


def analyse(messages, db_path=None):
    """Proposed updates, oldest first, one per (job, status); never a downgrade (interview -> submitted)."""
    db_path = paths.db_path(db_path)
    con = connect(db_path)
    out, seen = [], set()
    try:
        for msg in sorted(messages, key=_date):
            status = classify(msg)
            if not status:
                continue
            hint = company_hint(msg)
            job = match_job(con, msg, hint)
            current = job["status"] if job else ""
            if job and RANK.get(current, 0) >= RANK[status]:
                continue  # already at that stage or further
            key = (job["id"] if job else _fold(hint), status)
            if key in seen or (not job and not hint):
                continue
            seen.add(key)
            out.append(
                {
                    "message_id": msg.get("id"),
                    "date": _date(msg),
                    "status": status,
                    "job_id": job["id"] if job else None,
                    "company": job["company"] if job else hint,
                    "title": job["title"] if job else re.sub(r"\s+", " ", str(msg.get("subject") or ""))[:120],
                    "current": current or None,
                    "subject": str(msg.get("subject") or "")[:160],
                    "from": str(msg.get("from") or "")[:120],
                }
            )
    finally:
        con.close()
    return out


def apply(proposals, db_path=None):
    """Apply confirmed proposals. Offers unknown to Fitly (applied elsewhere) are created as source 'email'."""
    from fitly.storage import upsert_jobs
    from fitly.tracking import tracker

    db_path = paths.db_path(db_path)
    tracker.backup_db(db_path, "before_mail_update")
    done = 0
    for p in proposals:
        job_id = p.get("job_id")
        if not job_id:
            upsert_jobs(
                [
                    {
                        "source": "email",
                        "external_id": f"mail:{_fold(p['company'])}:{_fold(p['title'])[:60]}",
                        "title": p["title"],
                        "company": p["company"],
                        "location": "",
                        "description": "",
                        "url": "",
                        "application_route": "manual",
                        "score": 0,
                    }
                ],
                db_path,
            )
            con = connect(db_path)
            row = con.execute(
                "SELECT id FROM jobs WHERE source='email' AND company=? AND title=? ORDER BY id DESC",
                (p["company"], p["title"]),
            ).fetchone()
            con.close()
            job_id = row["id"] if row else None
        if not job_id:
            continue
        if p["status"] != "submitted" and not p.get("current"):
            tracker.set_status(
                job_id, "submitted", note="candidature (déduite d'un e-mail)", when=p["date"], db_path=db_path
            )
        tracker.set_status(
            job_id, p["status"], note=f"e-mail : {p.get('subject', '')[:100]}", when=p["date"], db_path=db_path
        )
        done += 1
    return done


# --- message sources -----------------------------------------------------------------------------------
def from_eml_folder(folder):
    out = []
    for f in sorted(Path(folder).glob("*.eml")):
        m = email.message_from_bytes(f.read_bytes())
        body = ""
        for part in m.walk() if m.is_multipart() else [m]:
            if part.get_content_type() in ("text/plain", "text/html") and not body:
                payload = part.get_payload(decode=True) or b""
                body = re.sub(r"<[^>]+>", " ", payload.decode(part.get_content_charset() or "utf-8", "ignore"))
        out.append(
            {"id": f.name, "date": m.get("Date"), "from": m.get("From"), "subject": m.get("Subject"), "body": body}
        )
    return out


def proposals_path(db_path=None):
    db_path = paths.db_path(db_path)
    return Path(db_path).parent / "mail" / "proposals.json"


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Suivi des candidatures depuis tes e-mails (propositions à valider)")
    ap.add_argument("--json", help="messages [{id,date,from,subject,body}]")
    ap.add_argument("--eml", help="dossier de fichiers .eml")
    ap.add_argument("--apply", action="store_true", help="appliquer les propositions en attente")
    a = ap.parse_args()
    path = proposals_path()
    if a.apply:
        props = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        print(f"{apply(props)} mise(s) à jour appliquée(s)")
        path.unlink(missing_ok=True)
        return 0
    messages = (
        json.loads(Path(a.json).read_text(encoding="utf-8")) if a.json else from_eml_folder(a.eml) if a.eml else []
    )
    props = analyse(messages)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in props:
        print(
            f"{p['date']}  {p['status']:<10} {p['company'][:28]:<28} {p['title'][:50]}  (#{p['job_id'] or 'nouvelle'})"
        )
    print(f"{len(props)} proposition(s) → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
