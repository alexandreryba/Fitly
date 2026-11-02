"""Job-alert e-mails (LinkedIn first, also APEC / HelloWork / WTTJ / Indeed / France Travail links).

Two inputs, both read-only:
  1. data/inbox/*.eml  — alert e-mails saved by the user (moved to data/inbox/processed/ once read);
  2. IMAP (optional)   — email_alerts.json (see fitly.config) + password in the FITLY_IMAP_PASSWORD environment
                         variable (e.g. a Gmail "app password"). The mailbox is opened read-only
                         (EXAMINE): nothing is deleted, moved or marked as read.
LinkedIn itself is never contacted: offers are discovered from the e-mails LinkedIn sends.
"""

from __future__ import annotations

import imaplib
import json
import re
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup

from fitly import config, paths

INBOX = paths.DATA_DIR / "inbox"
SEEN = paths.DATA_DIR / "email_seen.json"

LINKEDIN = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"'<>]*?-)?(\d{6,})", re.I)
# Other boards: (source, regex with the offer id in group 1)
OTHER = [
    ("apec", re.compile(r"https?://(?:www\.)?apec\.fr/[^\s\"'<>]*?detail-offre/([0-9A-Z]+)", re.I)),
    ("hellowork", re.compile(r"https?://(?:www\.)?hellowork\.com/fr-fr/emplois/(\d+)\.html", re.I)),
    (
        "wttj",
        re.compile(
            r"https?://(?:www\.)?welcometothejungle\.com/(?:fr|en)/companies/([^/\s\"'<>]+/jobs/[^/?\s\"'<>]+)", re.I
        ),
    ),
    ("indeed", re.compile(r"https?://(?:[a-z]+\.)?indeed\.(?:fr|com)/[^\s\"'<>]*?[?&]jk=([0-9a-f]{12,})", re.I)),
    (
        "france_travail",
        re.compile(r"https?://candidat\.(?:francetravail|pole-emploi)\.fr/offres/recherche/detail/([0-9A-Z]+)", re.I),
    ),
    ("cadremploi", re.compile(r"https?://(?:www\.)?cadremploi\.fr/emploi/[^\s\"'<>]*?(\d{6,})", re.I)),
    ("free_work", re.compile(r"https?://(?:www\.)?free-work\.com/[^\s\"'<>]*?/job-mission/([a-z0-9-]+)", re.I)),
    ("meteojob", re.compile(r"https?://(?:www\.)?meteojob\.com/[^\s\"'<>]*?(\d{6,})", re.I)),
    ("jobteaser", re.compile(r"https?://(?:www\.)?jobteaser\.com/[^\s\"'<>]*?/job-offers/(\d+)", re.I)),
    ("glassdoor", re.compile(r"https?://(?:www\.)?glassdoor\.(?:fr|com)/[^\s\"'<>]*?[?&]jobListingId=(\d+)", re.I)),
    ("wellfound", re.compile(r"https?://(?:www\.)?wellfound\.com/jobs/(\d+)", re.I)),
]
NOISE = re.compile(
    r"^(view job|voir l.offre|voir l.emploi|apply|postuler|see all jobs|voir toutes|easy apply|candidature simplifi|"
    r"actively recruiting|recrute activement|be an early applicant|new|nouveau|promoted|sponsoris|\d+ (applicants|candidats)|"
    r"this company is actively hiring|top applicant|.*(alumni|ancien).*travaill|.*connection|.*relation)",
    re.I,
)


DEFAULT_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "welcometothejungle.com",
    "hellowork.com",
    "indeed.com",
    "apec.fr",
    "francetravail.fr",
    "jobteaser.com",
    "cadremploi.fr",
    "free-work.com",
    "meteojob.com",
    "glassdoor.com",
    "wellfound.com",
]


def _clean(text):
    return re.sub(r"\s+", " ", unescape(str(text or ""))).strip()


def _useful(line):
    line = _clean(line)
    return bool(line) and len(line) <= 140 and not NOISE.match(line) and not line.startswith("http")


def _job(source, ext, title, company, location, url, route):
    company, location = _clean(company), _clean(location)
    if not location and " · " in company:  # LinkedIn: "Company · Location" on one line
        company, location = (x.strip() for x in company.split(" · ", 1))
    return {
        "id": f"{source}:{ext}",
        "external_id": str(ext),
        "source": source,
        "title": _clean(title),
        "company": _clean(company),
        "location": _clean(location),
        "description": "",
        "url": url,
        "application_route": route,
    }


def parse_linkedin_text(text):
    """Plain-text LinkedIn alert: 'Title / Company / Location / View job: <url>' blocks."""
    jobs, lines = {}, [line.strip() for line in (text or "").splitlines()]
    for i, line in enumerate(lines):
        m = LINKEDIN.search(line)
        if not m or m.group(1) in jobs:
            continue
        before = [x for x in lines[max(0, i - 8) : i] if _useful(x)]
        before = before[-3:]
        if len(before) == 3:
            title, company, location = before
        elif len(before) == 2:
            (title, company), location = before, ""
        else:
            continue
        jobs[m.group(1)] = _job(
            "linkedin",
            m.group(1),
            title,
            company,
            location,
            f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
            "linkedin_check",
        )
    return list(jobs.values())


def _block_strings(a, jid, max_up=6):
    """Useful strings of the smallest ancestor describing this job only (never a neighbour's block)."""
    block, best = a, []
    for _ in range(max_up):
        parent = block.parent
        if parent is None:
            break
        others = {mm.group(1) for x in parent.find_all("a", href=True) for mm in [LINKEDIN.search(x["href"])] if mm}
        if others - {jid}:
            break
        block = parent
        best = [s for s in (_clean(x) for x in block.stripped_strings) if _useful(s)]
        if len(best) >= 3 or any(" · " in s for s in best[1:]):
            break
    return best


def parse_linkedin_html(html):
    """HTML LinkedIn alert: the job link's text is the title; company and location follow in the same block."""
    soup = BeautifulSoup(html or "", "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        m = LINKEDIN.search(a["href"])
        if not m:
            continue
        jid, anchor = m.group(1), _clean(a.get_text(" "))
        strings = _block_strings(a, jid)
        from_anchor = _useful(anchor)
        title = anchor if from_anchor else (strings[0] if strings else "")
        rest = [s for s in strings if s != title]
        cand = {
            "title": title,
            "company": rest[0] if rest else "",
            "location": rest[1] if len(rest) > 1 else "",
            "_rank": (from_anchor, len(rest)),
        }
        # A real title from the job link beats a guessed one; then the richer block wins.
        if title and (jid not in found or cand["_rank"] > found[jid]["_rank"]):
            found[jid] = cand
    return [
        _job(
            "linkedin",
            jid,
            d["title"],
            d["company"],
            d["location"],
            f"https://www.linkedin.com/jobs/view/{jid}/",
            "linkedin_check",
        )
        for jid, d in found.items()
        if d["title"]
    ]


def parse_other_boards(html, text):
    soup = BeautifulSoup(html or "", "html.parser")
    links = [(a["href"], _clean(a.get_text(" "))) for a in soup.find_all("a", href=True)]
    links += [(u, "") for u in re.findall(r"https?://[^\s<>\"']+", text or "")]
    jobs = {}
    for href, label in links:
        for source, rx in OTHER:
            m = rx.search(unescape(href))
            if m and (source, m.group(1)) not in jobs and _useful(label):
                company = ""
                if source == "wttj":  # .../companies/<company-slug>/jobs/<job-slug>
                    company = m.group(1).split("/")[0].replace("-", " ").title()
                jobs[(source, m.group(1))] = _job(
                    source, m.group(1), label, company, "", href.split("#")[0], "external_or_manual"
                )
    return list(jobs.values())


def parse_message(msg):
    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    html = html_part.get_content() if html_part else ""
    text = text_part.get_content() if text_part else ""
    jobs = {}
    # Text blocks are the most regular; HTML fills in whatever the text part lacks.
    for j in parse_linkedin_html(html) + parse_linkedin_text(text):
        old = jobs.get(j["id"])
        if old is None or (not old["company"] and j["company"]):
            jobs[j["id"]] = j
    for j in parse_other_boards(html, text):
        jobs.setdefault(j["id"], j)
    try:
        from email.utils import parsedate_to_datetime

        sent = parsedate_to_datetime(msg.get("Date")).strftime("%Y-%m-%d") if msg.get("Date") else ""
    except (TypeError, ValueError):
        sent = ""
    for j in jobs.values():
        j["posted_at"] = sent  # alert date: the offer was published on or shortly before it
    return list(jobs.values())


def parse_eml_bytes(raw):
    return parse_message(BytesParser(policy=policy.default).parsebytes(raw))


def _load_seen(path=SEEN):
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_seen(ids, path=SEEN):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(sorted(ids)[-5000:], indent=0), encoding="utf-8")


def collect_inbox(inbox=INBOX):
    """Parse every .eml in the inbox folder, then move it to inbox/processed/."""
    inbox = Path(inbox)
    jobs, errors = [], []
    if not inbox.exists():
        return jobs, errors
    done = inbox / "processed"
    for p in sorted(inbox.glob("*.eml")):
        try:
            jobs.extend(parse_eml_bytes(p.read_bytes()))
            if not paths.is_sandbox():  # sandbox: leave the file for the real run
                done.mkdir(exist_ok=True)
                p.replace(done / p.name)
        except Exception as exc:  # one bad file must not stop the run
            errors.append({"source": "email", "target": p.name, "error": str(exc)})
    return jobs, errors


def load_config(path=None):
    try:
        return json.loads(Path(path or config.path("email_alerts.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def collect_imap(cfg, password=None, seen_path=SEEN, imap_factory=imaplib.IMAP4_SSL):
    """Read alert e-mails from IMAP (read-only). Returns (jobs, errors)."""
    if not cfg.get("enabled"):
        return [], []
    from fitly import user_env

    password = password or user_env.get(cfg.get("password_env", "FITLY_IMAP_PASSWORD"))
    if not password:
        return [], [
            {"source": "email", "target": cfg.get("user"), "error": "IMAP enabled but password env var not set"}
        ]
    since = (datetime.now(UTC) - timedelta(days=int(cfg.get("since_days", 3)))).strftime("%d-%b-%Y")
    seen = _load_seen(seen_path)
    jobs, errors = [], []
    conn = imap_factory(cfg.get("host", "imap.gmail.com"), int(cfg.get("port", 993)))
    try:
        conn.login(cfg["user"], password)
        conn.select(cfg.get("folder", "INBOX"), readonly=True)
        for sender in cfg.get("senders", DEFAULT_SENDERS):
            status, data = conn.search(None, "SINCE", since, "FROM", f'"{sender}"')
            if status != "OK":
                continue
            for num in (data[0] or b"").split():
                status, parts = conn.fetch(num, "(BODY.PEEK[])")  # PEEK: does not set \\Seen
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                raw = parts[0][1]
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                mid = msg.get("Message-ID") or f"{sender}:{num.decode()}"
                if mid in seen:
                    continue
                try:
                    jobs.extend(parse_message(msg))
                    seen.add(mid)
                except Exception as exc:
                    errors.append({"source": "email", "target": mid, "error": str(exc)})
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    _save_seen(seen, seen_path)
    return jobs, errors


def collect(inbox=INBOX, config_path=None):
    jobs, errors = collect_inbox(inbox)
    cfg = load_config(config_path)
    if cfg.get("enabled"):
        try:
            j2, e2 = collect_imap(cfg)
            jobs += j2
            errors += e2
        except Exception as exc:
            errors.append({"source": "email", "target": cfg.get("host"), "error": str(exc)})
    return jobs, errors
