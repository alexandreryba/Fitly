"""Local receiver for the "Ajouter à Fitly" browser extension (assisted capture).

The user browses LinkedIn / Indeed / WTTJ / HelloWork / APEC / Cadremploi themself, with their own account.
When they click the extension button, the extension sends what is displayed on that page (structured
JobPosting data when the page has it, otherwise the job links visible on a results page) to this server,
which normalises, scores and stores the offers like any other source.

No automated navigation, no account, nothing hidden: only pages the user opened, only when they click.
Listens on 127.0.0.1 only and accepts requests from the Fitly extension only (Origin check against the ID its
pinned key gives it), so neither a web page nor another extension can push data into Fitly or read the CV.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import UTC
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from fitly import config, paths

PORT = int(os.environ.get("FITLY_CAPTURE_PORT", "8765"))
MAX_BODY = 5_000_000


def extension_id(manifest=None) -> str:
    """ID Chrome and Edge give the unpacked extension: derived from the public key pinned in its manifest."""
    manifest = manifest or paths.EXTENSION_DIR / "manifest.json"
    key = json.loads(manifest.read_text(encoding="utf-8"))["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


EXTENSION_ORIGIN = f"chrome-extension://{extension_id()}"
REFUSED = (
    "Origine refusée : seule l'extension Fitly est acceptée. Après la mise à jour vers Fitly 1.1, réinstalle-la "
    "une fois (docs/sources.md, « Mise à jour de l'extension »)."
)

# site -> (source id, job URL pattern with the offer id in group 1)
SITES = [
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.I)),
    ("linkedin", re.compile(r"linkedin\.com/jobs/[^#]*?[?&]currentJobId=(\d{6,})", re.I)),
    ("indeed", re.compile(r"indeed\.[a-z.]+/[^#]*?[?&](?:jk|vjk)=([0-9a-f]{12,})", re.I)),
    ("wttj", re.compile(r"welcometothejungle\.com/(?:fr|en)/companies/([^/?#]+/jobs/[^/?#]+)", re.I)),
    ("hellowork", re.compile(r"hellowork\.com/fr-fr/emplois/(\d+)\.html", re.I)),
    ("apec", re.compile(r"apec\.fr/[^#]*?detail-offre/([0-9A-Z]+)", re.I)),
    ("cadremploi", re.compile(r"cadremploi\.fr/emploi/[^#]*?(\d{6,})", re.I)),
    ("free_work", re.compile(r"free-work\.com/[^#]*?/job-mission/([a-z0-9-]+)", re.I)),
    ("jobteaser", re.compile(r"jobteaser\.com/[^#]*?/job-offers/(\d+)", re.I)),
]
NOISE = re.compile(
    r"^(promu|promoted|sponsoris|nouveau|new|easy apply|candidature simplifi|postuler|apply|"
    r"enregistrer|save|il y a|\d+ (candidats|applicants)|actively|recrute|be an early|"
    r"voir|view|vérifié|verified|·)$",
    re.I,
)


def _clean(text):
    return re.sub(r"\s+", " ", unescape(str(text or ""))).strip()


def _plain(html):
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", str(html or ""), flags=re.I)
    return _clean(re.sub(r"<[^>]+>", " ", html))


def site_of(url) -> tuple[str | None, str | None]:
    for source, rx in SITES:
        m = rx.search(url or "")
        if m:
            return source, m.group(1)
    return None, None


def _route(source):
    return "linkedin_check" if source == "linkedin" else "external_or_manual"


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def _location(posting):
    loc = _first(posting.get("jobLocation")) or {}
    addr = (loc.get("address") if isinstance(loc, dict) else None) or {}
    if isinstance(addr, str):
        return addr
    parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
    parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
    text = ", ".join(p for p in parts if p)
    if str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        text = f"{text} (Remote)".strip()
    return text


def jobs_from_postings(postings, page_url) -> list[dict[str, Any]]:
    """schema.org JobPosting objects (present on most job-detail pages) -> Fitly jobs."""
    out = []
    for p in postings:
        url = p.get("url") or page_url
        source, ext = site_of(url)
        if not source:
            source, ext = site_of(page_url)
        host = urlparse(page_url).netloc.replace("www.", "")
        source = source or host.split(".")[0] or "capture"
        ident = p.get("identifier")
        if not ext and isinstance(ident, dict):
            ext = ident.get("value")
        if not ext:  # stable across runs (Python's hash() is randomised per process)
            import hashlib

            ext = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
        org = p.get("hiringOrganization") or {}
        out.append(
            {
                "id": f"{source}:{ext}",
                "external_id": str(ext),
                "source": source,
                "title": _clean(p.get("title")),
                "company": _clean(org.get("name") if isinstance(org, dict) else org),
                "location": _location(p),
                "description": _plain(p.get("description")),
                "url": url,
                "posted_at": str(p.get("datePosted") or "")[:10],
                "employment_type": _clean(
                    " ".join(p.get("employmentType"))
                    if isinstance(p.get("employmentType"), list)
                    else p.get("employmentType")
                ),
                "application_route": _route(source),
                "via": "capture",
            }
        )
    return [j for j in out if j["title"]]


def jobs_from_links(links) -> list[dict[str, Any]]:
    """Results pages: job links + the text of their card ('title / company / location' lines)."""
    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for link in links:
        href = link.get("href") or ""
        source, ext = site_of(href)
        if not source:
            continue
        lines = [
            line
            for line in (_clean(x) for x in str(link.get("card") or "").split("\n"))
            if line and len(line) <= 140 and not NOISE.match(line)
        ]
        title = _clean(link.get("text")) or (lines[0] if lines else "")
        if not title or NOISE.match(title):
            continue
        rest = [line for line in lines if line != title and title not in line]
        job = found.get((source, ext))
        cand = {
            "id": f"{source}:{ext}",
            "external_id": str(ext),
            "source": source,
            "title": title,
            "company": rest[0] if rest else "",
            "location": rest[1] if len(rest) > 1 else "",
            "description": "",
            "url": href.split("#")[0],
            "application_route": _route(source),
            "via": "capture",
        }
        if job is None or (not job["company"] and cand["company"]):
            found[(source, ext)] = cand
    return list(found.values())


def jobs_from_payload(payload) -> list[dict[str, Any]]:
    page_url = str(payload.get("url") or "")
    jobs = jobs_from_postings(payload.get("postings") or [], page_url)
    if not jobs:
        jobs = jobs_from_links(payload.get("links") or [])
    return jobs


def ingest(payload, db_path=None) -> dict[str, Any]:
    """Score and store captured offers. Returns a small report for the extension popup."""
    from fitly.scoring.pipeline import run_pipeline
    from fitly.storage import upsert_jobs

    db_path = paths.db_path(db_path)
    jobs = jobs_from_payload(payload)
    if not jobs:
        return {"ok": True, "saved": 0, "message": "Aucune offre reconnue sur cette page."}
    processed = run_pipeline(jobs, *config.scoring_inputs())
    result = upsert_jobs(processed, db_path)
    best = sorted(processed, key=lambda j: -(j.get("score") or 0))[:3]
    return {
        "ok": True,
        "saved": len(processed),
        "inserted": result["inserted"],
        "updated": result["updated"],
        "best": [{"title": j["title"], "company": j["company"], "score": j["score"]} for j in best],
        "message": f"{len(processed)} offre(s) ajoutée(s) à Fitly ({result['inserted']} nouvelle(s)).",
    }


def extension_fingerprint() -> str:
    """Hash of the extension files on disk: the extension reloads itself when it differs from what it loaded."""
    import hashlib

    h = hashlib.sha256()
    for f in sorted(paths.EXTENSION_DIR.glob("*")):
        if f.is_file() and f.suffix in (".js", ".json", ".html"):
            data = f.read_bytes().replace(b"\r\n", b"\n")  # same hash whatever Git's line endings
            h.update(f.name.encode() + b"\0" + data)
    return h.hexdigest()[:16]


# --- Pre-filling in the user's own Chrome tab (the extension does the typing, Fitly decides what) ----
def _db():
    return paths.db_path()


def _prefill_dir():
    d = paths.work_dir() / "prefill"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_cv():
    """CV used when the page is not a known offer (one-click fill on any page): tech_fr, else the first one."""
    try:
        ids = [c["id"] for c in config.load("cv_profiles.json")["cvs"]]
    except (OSError, ValueError, KeyError):
        return None
    return "tech_fr" if "tech_fr" in ids else (ids[0] if ids else None)


def resolve_job(url) -> int | None:
    """Offer whose page (or employer page) is this URL: same host and path, query and fragment ignored."""
    u = urlparse(str(url or ""))
    if not u.netloc:
        return None
    key = (u.netloc.lower(), u.path.rstrip("/").removesuffix("/apply").removesuffix("/application"))
    from fitly.storage import connect

    con = connect(_db())
    try:
        like = f"%{u.netloc}%"
        for r in con.execute(
            "SELECT id, url, direct_url FROM jobs WHERE url LIKE ? OR direct_url LIKE ? ORDER BY id DESC", (like, like)
        ):
            for cand in (r["direct_url"], r["url"]):
                c = urlparse(cand or "")
                if (c.netloc.lower(), c.path.rstrip("/").removesuffix("/apply").removesuffix("/application")) == key:
                    return r["id"]
    finally:
        con.close()
    return None


def go_page(job_id) -> str | None:
    """Relay page opened by Fitly: the extension marks the tab here, BEFORE the offer's site loads, so
    redirections (which drop the #fitly-fill marker) no longer lose the tab. Then it opens the offer."""
    import html

    from fitly.apply import launch_review

    targets = launch_review.job_targets([int(job_id)]) if str(job_id).isdigit() else []
    if not targets:
        return None
    target = launch_review.form_url(targets[0]["url"])
    if not target.startswith(("http://", "https://")):
        return None
    t = html.escape(target, quote=True)
    return (
        f'<!doctype html><meta charset="utf-8"><title>Fitly</title><body data-fitly-target="{t}" '
        f'style="font:16px Segoe UI,Arial;padding:40px;color:#1f2937">🧭 Fitly ouvre l’offre…'
        f"<script>setTimeout(function(){{location.replace(document.body.dataset.fitlyTarget)}},4000)</script>"
    )


def _pending():
    try:
        return json.loads((_prefill_dir() / "pending.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _rules():
    return config.load("application_rules.json", default={})


def fill_plan(payload) -> dict[str, Any]:
    """Fields inspected by the extension -> actions to apply. Same rules as the Playwright pre-filling:
    validated answers only, standard questions, required consents (never newsletters), nothing submitted."""
    from fitly.apply import launch_review
    from fitly.apply.application_session import gate_session

    job_id = str(payload.get("job_id") or "")
    opts = _pending().get(job_id, {})
    answers = launch_review.load_answers()
    cv_ok = False
    if not opts.get("no_cv"):
        targets = launch_review.job_targets([int(job_id)]) if job_id.isdigit() else []
        cv = (targets[0].get("recommended_cv") if targets else None) or opts.get("cv") or _default_cv()
        path = launch_review.cv_path(cv) if cv else None
        if path and path.is_file():
            answers["cv_file"] = str(path)
            cv_ok = True
    rules = _rules()
    gate = gate_session(
        str(payload.get("text") or ""),
        payload.get("fields") or [],
        answers,
        unknown_required=rules.get("unknown_required_question", "leave_to_human"),
        tick_consents=bool(rules.get("tick_required_consents", False)),
    )
    if gate["status"] != "READY_FOR_HUMAN_REVIEW":
        return {"ok": True, "status": "stopped", "reason": gate.get("reason"), "actions": []}
    plan = gate["fill_plan"]
    actions = []
    for item in plan["plan"]:
        f = item["field"]
        if f.get("type") == "file":
            actions.append({"selector": f["selector"], "type": "file", "cv": True})
        else:
            actions.append(
                {
                    "selector": f["selector"],
                    "type": f.get("type") or "text",
                    "value": item["value"] if f.get("type") != "checkbox" else True,
                }
            )
    return {
        "ok": True,
        "status": "plan",
        "actions": actions,
        "cv_available": cv_ok,
        "remaining_required": gate.get("remaining_required", []),
        # where the user must act: required gaps first, then their own decisions (radios, consents...)
        "todo_selectors": [f["selector"] for f in plan.get("unknown", []) if f.get("selector")]
        + [h["selector"] for h in plan.get("human", []) if h.get("selector") and h.get("required")],
        "your_decision": [h.get("label") for h in plan.get("human", [])],
    }


def fill_cv(job_id) -> dict[str, Any]:
    import base64

    from fitly.apply import launch_review

    targets = launch_review.job_targets([int(job_id)]) if str(job_id).isdigit() else []
    cv = (
        (targets[0].get("recommended_cv") if targets else None)
        or _pending().get(str(job_id), {}).get("cv")
        or _default_cv()
    )
    path = launch_review.cv_path(cv) if cv else None
    if not path or not path.is_file():
        return {"ok": False, "message": "CV introuvable dans le dossier cvs\\"}
    return {
        "ok": True,
        "filename": path.name,
        "mime": "application/pdf",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


_REPORT_LOCK = threading.Lock()  # tabs report at the same moment: one read-modify-write of last_run.json at a time


def fill_report(payload) -> dict[str, Any]:
    """Store one tab's result in last_run.json (read by the interface) and mark the offer 'prepared'."""
    with _REPORT_LOCK:
        return _fill_report(payload)


def _fill_report(payload):
    out = _prefill_dir() / "last_run.json"
    try:
        report = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {"at": "", "results": []}
    from datetime import datetime

    report["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    job_id = payload.get("job_id")
    entry = {
        k: payload.get(k)
        for k in (
            "job_id",
            "company",
            "title",
            "url",
            "status",
            "filled",
            "remaining_required",
            "your_decision",
            "warnings",
            "reason",
            "ext_version",
        )
    }
    if str(job_id).isdigit() and not entry.get("company"):
        try:
            from fitly.storage import connect

            con = connect(_db())
            row = con.execute("SELECT company, title FROM jobs WHERE id=?", (int(job_id),)).fetchone()
            con.close()
            if row:
                entry.update(company=row["company"], title=row["title"])
        except (sqlite3.Error, OSError):  # the report is written without the offer's name
            pass
    previous = [r for r in report.get("results", []) if str(r.get("job_id")) == str(job_id)]
    if previous and previous[-1].get("status") == "prefilled" and entry["status"] != "prefilled":
        return {"ok": True}  # another tab of this offer (the employer's form) already filled it
    report["results"] = [r for r in report.get("results", []) if str(r.get("job_id")) != str(job_id)] + [entry]
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if str(job_id).isdigit() and payload.get("status") == "closed":
        try:
            from fitly.sources import employer_match

            employer_match.mark_closed(job_id, _db())
        except (sqlite3.Error, OSError):
            pass
    if str(job_id).isdigit() and (payload.get("filled") or 0) > 0:
        try:
            from fitly.storage import connect
            from fitly.tracking import tracker

            con = connect(_db())
            row = con.execute("SELECT status FROM applications WHERE job_id=?", (int(job_id),)).fetchone()
            con.close()
            if row and row["status"] == "go":
                tracker.set_status(int(job_id), "prepared", note="formulaire pré-rempli (onglet Chrome)", db_path=_db())
        except (sqlite3.Error, OSError, ValueError):  # ValueError: offer deleted meanwhile
            pass
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _allowed_origin(self):
        return self.headers.get("Origin") == EXTENSION_ORIGIN

    def _send(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if self._allowed_origin():
            self.send_header("Access-Control-Allow-Origin", EXTENSION_ORIGIN)  # the Origin, as just checked
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.startswith("/go?"):
            from urllib.parse import parse_qs

            page = go_page((parse_qs(urlparse(self.path).query).get("job") or [""])[0])
            if page is None:
                return self._send(404, {"ok": False})
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/fill/resolve"):
            if not self._allowed_origin():
                return self._send(403, {"ok": False})
            from urllib.parse import parse_qs

            return self._send(
                200, {"ok": True, "job_id": resolve_job((parse_qs(urlparse(self.path).query).get("url") or [""])[0])}
            )
        if self.path == "/ping":
            return self._send(200, {"ok": True, "app": "Fitly", "extension": extension_fingerprint()})
        if self.path.startswith("/fill/cv"):
            if not self._allowed_origin():  # the CV is personal: extension only
                return self._send(403, {"ok": False})
            from urllib.parse import parse_qs

            job_id = (parse_qs(urlparse(self.path).query).get("job_id") or [""])[0]
            try:
                return self._send(200, fill_cv(job_id))
            except Exception as exc:  # report, never crash the server
                return self._send(500, {"ok": False, "message": str(exc)})
        self._send(404, {"ok": False})

    def do_POST(self) -> None:
        routes = {"/capture": ingest, "/fill/plan": fill_plan, "/fill/report": fill_report}
        handler = routes.get(self.path)
        if handler is None:
            return self._send(404, {"ok": False})
        if not self._allowed_origin():
            return self._send(403, {"ok": False, "message": REFUSED})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"ok": False, "message": "Requête vide ou trop grande."})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._send(200, handler(payload))
        except Exception as exc:  # report, never crash the server
            self._send(500, {"ok": False, "message": f"Erreur : {exc}"})

    def log_message(self, *args) -> None:
        pass


def make_server(port=PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def start_in_background(port=PORT) -> ThreadingHTTPServer | None:
    """Start once (idempotent per process); returns the server or None if the port is taken."""
    try:
        server = make_server(port)
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="fitly-capture").start()
    return server


if __name__ == "__main__":
    srv = make_server()
    print(f"Fitly capture : écoute sur http://127.0.0.1:{PORT} (Ctrl+C pour arrêter)")
    srv.serve_forever()
