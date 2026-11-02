"""Cross-check offers against the employer's own careers site.

1. Offers relayed by a job board (LinkedIn, Indeed, APEC, Figaro, BeBee... mostly via Google Jobs / Jooble):
   Fitly looks for the company's public job board (Lever, Greenhouse, Ashby, SmartRecruiters, Workable,
   Recruitee, Teamtailor -- official public APIs) and for the same offer on it.
   - found     -> jobs.direct_url = the employer's page (opened for pre-filling instead of the job board);
   - board found but offer absent -> availability 'not_on_employer_site' (probably filled or expired);
   - no public board -> unchanged ('no_board').
2. Direct employer pages: one plain HTTP request; 404/410 or an "offer no longer available" page
   -> availability 'closed'. Job-board pages are never fetched (their terms forbid automated access).

Results are cached (data/employer_boards.json) so each company is probed at most once a month.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fitly import config, paths
from fitly.sources.ats_clients import JOB_BOARDS, _ssl_context, probe
from fitly.storage import connect

BOARD_TTL_DAYS = 30
RECHECK_DAYS = 3
EXPIRED = re.compile(
    r"(n'est plus disponible|n.est plus en ligne|offre (a )?expir|offre (est )?(close|cl[oô]tur|pourvue)|"
    r"no longer (available|accepting|open)|job (has )?expired|position has been filled|"
    r"job (is )?(closed|not found)|this job is no longer|page (not found|introuvable))",
    re.I,
)
STOP = {
    "h",
    "f",
    "hf",
    "fh",
    "m",
    "w",
    "x",
    "cdi",
    "cdd",
    "stage",
    "alternance",
    "the",
    "a",
    "de",
    "des",
    "du",
    "en",
    "et",
    "le",
    "la",
    "les",
    "pour",
    "with",
    "and",
    "of",
    "in",
    "at",
    "paris",
    "france",
    "remote",
    "hybrid",
    "teletravail",
}


def is_relay(url, company=None):
    """Not the employer's own page: a job board or aggregator (an open-ended list, so anything that is neither
    a careers software nor a domain with the company's name counts as a relay when the company is known)."""
    from fitly.sources.ats_clients import is_employer_link

    host = urlparse(str(url or "")).netloc.lower()
    if not host:
        return False
    if any(b in host for b in JOB_BOARDS):
        return True
    return company is not None and not is_employer_link(url, company)


def _now():
    return datetime.now(UTC)


def _tokens(text):
    t = "".join(c for c in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(c)).lower()
    return {w for w in re.sub(r"[^a-z0-9+#]+", " ", t).split() if w not in STOP and len(w) > 1}


def title_similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def best_match(job, postings, threshold=0.6):
    """Posting of the same offer on the employer's board (same title words; same city when both are known)."""
    city = (job.get("location") or "").split(",")[0].strip().lower()
    scored = []
    for p in postings:
        sim = title_similarity(job.get("title"), p.get("title"))
        pcity = (p.get("location") or "").lower()
        if city and pcity and city not in pcity and "remote" not in pcity and "france" not in pcity:
            sim -= 0.2
        scored.append((sim, p))
    scored.sort(key=lambda x: -x[0])
    return scored[0][1] if scored and scored[0][0] >= threshold else None


# --- company -> public job boards (cached) ------------------------------------------------------------
def _cache_path(db_path):
    return Path(db_path).parent / "employer_boards.json"


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _configured_boards():
    try:
        cfg = config.load("ats_sources.json")
    except (OSError, ValueError):
        return {}
    out = {}
    for s in cfg.get("sources", []):
        board = s.get("board_token") or s.get("site_name") or s.get("board")
        if board and s.get("company") and s.get("type") not in ("google_jobs", "jooble", "adzuna", "france_travail"):
            out.setdefault(s["company"].lower(), []).append({"type": s["type"], "board": board})
    return out


def boards_for(company, cache, prober=probe):
    key = str(company or "").strip().lower()
    if not key:
        return []
    configured = _configured_boards().get(key)
    if configured:
        return configured
    hit = cache.get(key)
    if hit and _now() - datetime.fromisoformat(hit["at"]) < timedelta(days=BOARD_TTL_DAYS):
        return hit["boards"]
    words = re.sub(r"[^a-z0-9 ]+", "", key).split()
    try:
        # A board named after the first word only ("capital" for Capital Fund Management) is someone else's.
        boards = [
            {"type": b["type"], "board": b["board"]}
            for b in prober(company)
            if len(words) < 2 or b["board"].lower() != words[0]
        ]
    except Exception:
        boards = []
    cache[key] = {"boards": boards, "at": _now().isoformat(timespec="seconds")}
    return boards


FRANCE = re.compile(
    r"france|paris|ile.de.france|île.de.france|lyon|lille|nantes|bordeaux|toulouse|marseille|rennes|"
    r"montpellier|strasbourg|grenoble|nice|sophia|la d[ée]fense|boulogne|levallois|issy|neuilly",
    re.I,
)


def french_board(postings):
    """A homonymous foreign company's board (no offer in France) proves nothing about a French offer."""
    return any(FRANCE.search(p.get("location") or "") for p in postings)


def postings_of(board, company, lister=None):
    if lister:
        return lister(board)
    from fitly.sources.collector_registry import collect_from_config

    key = {"greenhouse": "board_token", "lever": "site_name"}.get(board["type"], "board")
    jobs, _ = collect_from_config({"sources": [{"type": board["type"], key: board["board"], "company": company}]})
    return jobs


# --- liveness of a direct employer page ---------------------------------------------------------------
def page_closed(url, timeout=12):
    """True when the employer page answers 404/410 or says the offer is no longer available. None if unknown."""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Fitly availability check)",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
        with urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            text = r.read(400_000).decode("utf-8", "ignore")
            final = r.geturl()
    except HTTPError as e:
        return True if e.code in (404, 410) else None
    except Exception:
        return None
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    if EXPIRED.search(body[:20000]):
        return True
    # Redirected from an offer page to the general job list: the offer is gone.
    return urlparse(final).path.rstrip("/").count("/") < urlparse(url).path.rstrip("/").count("/") - 1


# --- main entry ----------------------------------------------------------------------------------------
def _fetch_all(urls, fetch=None, workers=8):
    """Availability of several direct pages in parallel: {url: True (closed) / False (open) / None (unknown)}."""
    from concurrent.futures import ThreadPoolExecutor

    fetch = fetch or page_closed
    urls = list(dict.fromkeys(u for u in urls if u))
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        return dict(zip(urls, pool.map(fetch, urls), strict=False))


def check(db_path=None, limit=400, min_score=55, prober=probe, lister=None, fetch=None, verbose=False, relay_limit=40):
    """Check the open offers (not yet decided or GO): every direct employer page (in parallel), and the
    `relay_limit` best offers relayed by job boards (employer-board lookup). Returns counters."""
    db_path = paths.db_path(db_path)
    cache_file = _cache_path(db_path)
    cache = _load(cache_file)
    since = (_now() - timedelta(days=RECHECK_DAYS)).isoformat(timespec="seconds")
    con = connect(db_path)
    rows = [
        dict(r)
        for r in con.execute(
            """
        SELECT j.id, j.company, j.title, j.location, j.url, j.direct_url, j.score
        FROM jobs j LEFT JOIN applications a ON a.job_id = j.id
        WHERE COALESCE(a.status, 'not_started') IN ('not_started', 'go')
          AND COALESCE(j.availability, '') NOT IN ('closed', 'not_on_employer_site')
          AND (j.availability_checked_at IS NULL OR j.availability_checked_at < ?)
          AND COALESCE(j.score, 0) >= ?
        ORDER BY j.score DESC LIMIT ?""",
            (since, min_score, limit),
        )
    ]
    stats = {
        "checked": 0,
        "direct_found": 0,
        "open": 0,
        "not_on_employer_site": 0,
        "closed": 0,
        "no_board": 0,
        "unknown": 0,
    }
    postings_cache = {}
    pages = _fetch_all(
        [r.get("direct_url") or r["url"] for r in rows if r.get("direct_url") or not is_relay(r["url"], r["company"])],
        fetch,
    )
    relays_done = 0
    try:
        for job in rows:
            availability, direct = None, job.get("direct_url")
            if is_relay(job["url"], job["company"]) and not direct:
                if relays_done >= relay_limit:
                    continue
                relays_done += 1
                boards = boards_for(job["company"], cache, prober)
                if not boards:
                    availability = "no_board"
                else:
                    found, trusted = None, False
                    for b in boards:
                        k = (b["type"], b["board"])
                        if k not in postings_cache:
                            try:
                                postings_cache[k] = postings_of(b, job["company"], lister)
                            except Exception:
                                postings_cache[k] = []
                        if not french_board(postings_cache[k]):
                            continue
                        trusted = True
                        found = best_match(job, postings_cache[k])
                        if found:
                            break
                    if found:
                        direct, availability = found.get("url"), "open"
                    else:
                        availability = "not_on_employer_site" if trusted else "no_board"
            else:
                closed = pages.get(direct or job["url"])
                availability = "closed" if closed else ("open" if closed is False else None)
            stats["checked"] += 1
            stats[availability or "unknown"] = stats.get(availability or "unknown", 0) + 1
            if direct and direct != job.get("direct_url"):
                stats["direct_found"] += 1
            con.execute(
                "UPDATE jobs SET direct_url=?, availability=?, availability_checked_at=? WHERE id=?",
                (direct, availability, _now().isoformat(timespec="seconds"), job["id"]),
            )
            con.commit()
            if verbose:
                print(
                    f"  {job['company'][:28]:<28} {job['title'][:45]:<45} -> {availability or '?'}"
                    + (f"  ({direct})" if direct and direct != job.get("direct_url") else "")
                )
    finally:
        con.close()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return stats


def verify_before_opening(job_ids, db_path=None, fetch=None):
    """Last check right before opening tabs: returns the ids whose employer page is gone (marked closed).
    Pages of job boards are not fetched (the extension reports them if they say 'expired')."""
    db_path = paths.db_path(db_path)
    con = connect(db_path)
    marks = ",".join("?" * len(job_ids)) or "NULL"
    rows = [
        dict(r)
        for r in con.execute(
            f"SELECT id, company, COALESCE(direct_url, url) AS url FROM jobs WHERE id IN ({marks})", list(job_ids)
        )
    ]
    con.close()
    pages = _fetch_all([r["url"] for r in rows if not is_relay(r["url"], r["company"])], fetch)
    closed = [r["id"] for r in rows if pages.get(r["url"]) is True]
    for job_id in closed:
        mark_closed(job_id, db_path)
    return closed


def mark_closed(job_id, db_path=None):
    """The pre-fill extension saw an 'offer no longer available' page."""
    db_path = paths.db_path(db_path)
    con = connect(db_path)
    con.execute(
        "UPDATE jobs SET availability='closed', availability_checked_at=? WHERE id=?",
        (_now().isoformat(timespec="seconds"), int(job_id)),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Vérifie les offres sur le site carrière de l'employeur")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--min-score", type=int, default=55)
    a = ap.parse_args()
    print(check(limit=a.limit, min_score=a.min_score, verbose=True))
