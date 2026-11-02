"""Read-only clients for public job-board APIs (GET only, no login, no scraping).

Supported: Ashby, SmartRecruiters, Workable, Recruitee, and the official
France Travail "Offres d'emploi v2" API (free; requires the user's own
client id/secret in environment variables FT_CLIENT_ID / FT_CLIENT_SECRET).
Each `list_*` function returns normalized job dicts:
  {id, external_id, source, title, company, location, description, url, application_route}
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from datetime import UTC
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from fitly import __version__, user_env

UA = f"Fitly/{__version__} public-job-reader"
# Detail requests are only made for titles that could plausibly be relevant.
DETAIL_HINT = re.compile(
    r"data|donn|\bai\b|\bia\b|\bml\b|machine|llm|genai|cloud|architect|engineer|ing[ée]nieur"
    r"|analyst|analyste|scien|consult|product|platform|bi\b",
    re.I,
)


def plain(value) -> str:
    value = unescape(str(value or ""))
    value = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def iso_date(value) -> str:
    """Posting date as YYYY-MM-DD from epoch ms/s, ISO strings or 'YYYY-MM-DD'; '' if unknown."""
    from datetime import datetime

    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=UTC).strftime("%Y-%m-%d")
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
        return m.group(1) if m else ""


def _ssl_context():
    """Up-to-date CA bundle (certifi) when available: an outdated Windows root store can reject valid
    certificates ('certificate has expired', seen with serpapi.com). Falls back to the system store."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):  # certifi missing, or its bundle unreadable
        return ssl.create_default_context()


_SSL = None


def get_json(url, headers=None, data=None, retries=2, timeout=20, pause=1.5) -> Any:
    """GET (or POST form when `data` is given) with bounded retries on 429/5xx/timeouts."""
    for attempt in range(retries + 1):
        req = Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
        try:
            global _SSL
            _SSL = _SSL or _ssl_context()
            with urlopen(req, timeout=timeout, context=_SSL) as r:
                body = r.read()
                return json.loads(body.decode("utf-8")) if body else {}
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(pause * (attempt + 1))
                continue
            raise
        except (URLError, TimeoutError):
            if attempt < retries:
                time.sleep(pause * (attempt + 1))
                continue
            raise


def _job(source, board, ext, title, company, location, description, url, posted=None, employment_type=""):
    return {
        "id": f"{source}:{board}:{ext}",
        "external_id": str(ext),
        "source": source,
        "title": title or "",
        "company": company or board,
        "location": location or "",
        "description": plain(description),
        "url": url or "",
        "application_route": "external_ats",
        "posted_at": iso_date(posted),
        "employment_type": employment_type or "",
    }


def list_ashby(board, company=None) -> list[dict[str, Any]]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=false")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location") or ""
        if j.get("isRemote") or j.get("workplaceType") == "Remote":
            loc = f"{loc} (Remote)".strip()
        out.append(
            _job(
                "ashby",
                board,
                j.get("id"),
                j.get("title"),
                company,
                loc,
                j.get("descriptionPlain") or j.get("descriptionHtml"),
                j.get("jobUrl"),
                posted=j.get("publishedAt"),
                employment_type=j.get("employmentType") or "",
            )
        )
    return out


def list_smartrecruiters(board, company=None, country="fr", max_details=150) -> list[dict[str, Any]]:
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(board)}/postings"
    postings, offset = [], 0
    while True:
        page = get_json(
            f"{base}?{urlencode({'country': country, 'limit': 100, 'offset': offset})}"
            if country
            else f"{base}?{urlencode({'limit': 100, 'offset': offset})}"
        )
        content = page.get("content", [])
        postings.extend(content)
        offset += len(content)
        if not content or offset >= page.get("totalFound", 0) or offset >= 1000:
            break
    out, details = [], 0
    for p in postings:
        loc = p.get("location") or {}
        location = ", ".join(x for x in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if x)
        if loc.get("remote"):
            location += " (Remote)"
        name = (p.get("company") or {}).get("name") or company
        description, url = "", f"https://jobs.smartrecruiters.com/{board}/{p.get('id')}"
        if details < max_details and DETAIL_HINT.search(p.get("name") or ""):
            try:
                d = get_json(f"{base}/{p.get('id')}")
                sections = (d.get("jobAd") or {}).get("sections") or {}
                description = " ".join(
                    (sections.get(k) or {}).get("text", "")
                    for k in ("jobDescription", "qualifications", "additionalInformation")
                )
                url = d.get("postingUrl") or url
            except (HTTPError, URLError, TimeoutError, ValueError):
                pass
            details += 1
        out.append(
            _job(
                "smartrecruiters",
                board,
                p.get("id"),
                p.get("name"),
                company or name,
                location,
                description,
                url,
                posted=p.get("releasedDate"),
                employment_type=(p.get("typeOfEmployment") or {}).get("label", ""),
            )
        )
    return out


def list_workable(board, company=None) -> list[dict[str, Any]]:
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{quote(board)}?details=true")
    out = []
    for j in data.get("jobs", []):
        parts = [j.get("city"), j.get("state"), j.get("country")]
        location = ", ".join(x for x in parts if x)
        if j.get("telecommuting"):
            location += " (Remote)"
        ext = j.get("shortcode") or j.get("code") or j.get("id")
        out.append(
            _job(
                "workable",
                board,
                ext,
                j.get("title"),
                company or data.get("name"),
                location,
                j.get("description"),
                j.get("url") or j.get("shortlink"),
                posted=j.get("published_on") or j.get("created_at"),
                employment_type=j.get("employment_type") or "",
            )
        )
    return out


def list_recruitee(board, company=None) -> list[dict[str, Any]]:
    data = get_json(f"https://{quote(board)}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        location = j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
        if j.get("remote"):
            location = f"{location} (Remote)".strip()
        out.append(
            _job(
                "recruitee",
                board,
                j.get("id"),
                j.get("title"),
                company or j.get("company_name"),
                location,
                f"{j.get('description') or ''} {j.get('requirements') or ''}",
                j.get("careers_url"),
                posted=j.get("published_at") or j.get("created_at"),
                employment_type=j.get("employment_type_code") or "",
            )
        )
    return out


# --- France Travail (official API, user credentials) -------------------------------------------
FT_TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
FT_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"


def france_travail_token(client_id=None, client_secret=None) -> str:
    client_id = client_id or user_env.get("FT_CLIENT_ID")
    client_secret = client_secret or user_env.get("FT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "France Travail credentials missing: set FT_CLIENT_ID and FT_CLIENT_SECRET "
            "(free account on francetravail.io, API 'Offres d'emploi v2')."
        )
    form = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        }
    ).encode()
    data = get_json(FT_TOKEN_URL, headers={"Content-Type": "application/x-www-form-urlencoded"}, data=form)
    return data["access_token"]


def list_france_travail(
    keywords,
    departements=("75", "92", "93", "94", "91", "78", "95", "77"),
    max_per_query=150,
    token=None,
    published_since_days=7,
) -> list[dict[str, Any]]:
    """One search per keyword over the given départements; returns normalized jobs."""
    token = token or france_travail_token()
    out, seen = [], set()
    for kw in keywords:
        params = {
            "motsCles": kw,
            "departement": ",".join(departements),
            "range": f"0-{max_per_query - 1}",
            "publieeDepuis": published_since_days,
        }
        try:
            data = get_json(f"{FT_SEARCH_URL}?{urlencode(params)}", headers={"Authorization": f"Bearer {token}"})
        except HTTPError as exc:
            if exc.code == 204:  # no result
                continue
            raise
        for o in (data or {}).get("resultats", []):
            if o.get("id") in seen:
                continue
            seen.add(o.get("id"))
            ent = o.get("entreprise") or {}
            url = (o.get("origineOffre") or {}).get(
                "urlOrigine"
            ) or f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}"
            job = _job(
                "france_travail",
                "api",
                o.get("id"),
                o.get("intitule"),
                ent.get("nom") or "Entreprise non communiquée",
                (o.get("lieuTravail") or {}).get("libelle"),
                o.get("description"),
                url,
                posted=o.get("dateCreation"),
            )
            job["application_route"] = "external_or_manual"
            job["employment_type"] = o.get("typeContratLibelle") or o.get("typeContrat") or ""
            job["salary_text"] = (o.get("salaire") or {}).get("libelle") or ""
            out.append(job)
    return out


# --- Teamtailor (public RSS career feed) ----------------------------------------------------------
def list_teamtailor(board, company=None) -> list[dict[str, Any]]:
    """https://<board>.teamtailor.com/jobs.rss — public feed of a company's Teamtailor career site."""
    import xml.etree.ElementTree as ET

    req = Request(f"https://{quote(board)}.teamtailor.com/jobs.rss", headers={"User-Agent": UA})
    with urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    out = []
    for it in root.iter("item"):

        def get(tag, it=it) -> str:
            return (it.findtext(tag) or "").strip()

        city = next((c.text for c in it.iter() if c.tag.endswith("city") and c.text), "")
        country = next((c.text for c in it.iter() if c.tag.endswith("country") and c.text), "")
        location = ", ".join(x for x in (city, country) if x)
        remote = get("remoteStatus")
        if remote in ("fully", "remote"):
            location = f"{location} (Remote)".strip()
        out.append(
            _job(
                "teamtailor",
                board,
                get("guid") or get("link"),
                get("title"),
                company or board,
                location,
                get("description"),
                get("link"),
                posted=_rss_date(get("pubDate")),
            )
        )
    return out


def _rss_date(value):
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(value).strftime("%Y-%m-%d") if value else ""
    except (TypeError, ValueError):
        return ""


# --- Aggregators with an official free API key (user's own account) -------------------------------
def _key(name):
    value = user_env.get(name)
    if not value:
        raise RuntimeError(f"{name} not set (free key: see README, section Sources)")
    return value


def list_adzuna(keywords, where="Paris", country="fr", max_days_old=21, pages=2) -> list[dict[str, Any]]:
    """Adzuna official API: aggregates many French job boards. Env: ADZUNA_APP_ID, ADZUNA_APP_KEY."""
    app_id, app_key = _key("ADZUNA_APP_ID"), _key("ADZUNA_APP_KEY")
    out, seen = [], set()
    for kw in keywords:
        for page in range(1, pages + 1):
            params = {
                "app_id": app_id,
                "app_key": app_key,
                "what": kw,
                "where": where,
                "results_per_page": 50,
                "max_days_old": max_days_old,
                "content-type": "application/json",
            }
            data = get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?{urlencode(params)}")
            results = data.get("results", [])
            for j in results:
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                job = _job(
                    "adzuna",
                    country,
                    j.get("id"),
                    j.get("title"),
                    (j.get("company") or {}).get("display_name"),
                    (j.get("location") or {}).get("display_name"),
                    j.get("description"),
                    j.get("redirect_url"),
                    posted=j.get("created"),
                    employment_type=j.get("contract_type") or "",
                )
                job["application_route"] = "external_or_manual"
                out.append(job)
            if len(results) < 50:
                break
    return out


def list_jooble(keywords, where="Paris, France", pages=2) -> list[dict[str, Any]]:
    """Jooble official API (free key on request). Env: JOOBLE_API_KEY."""
    key = _key("JOOBLE_API_KEY")
    out, seen = [], set()
    for kw in keywords:
        for page in range(1, pages + 1):
            body = json.dumps({"keywords": kw, "location": where, "page": str(page)}).encode("utf-8")
            data = get_json(
                f"https://jooble.org/api/{quote(key)}", headers={"Content-Type": "application/json"}, data=body
            )
            jobs = data.get("jobs", [])
            for j in jobs:
                if re.search(r",\s*[A-Z]{2}$", str(j.get("location") or "")):  # 'Paris, TX': US homonym
                    continue
                ext = j.get("id") or j.get("link")
                if ext in seen:
                    continue
                seen.add(ext)
                job = _job(
                    "jooble",
                    "api",
                    ext,
                    j.get("title"),
                    j.get("company"),
                    j.get("location"),
                    j.get("snippet"),
                    j.get("link"),
                    posted=j.get("updated"),
                    employment_type=j.get("type") or "",
                )
                job["application_route"] = "external_or_manual"
                out.append(job)
            if not jobs:
                break
    return out


# Job boards relaying an offer: an employer/ATS link (direct form, pre-fillable) is preferred over them.
JOB_BOARDS = (
    "linkedin.",
    "indeed.",
    "apec.fr",
    "hellowork.",
    "welcometothejungle.",
    "cadremploi.",
    "jobteaser.",
    "bebee.",
    "lefigaro.fr",
    "jooble.",
    "glassdoor.",
    "monster.",
    "meteojob.",
    "free-work.",
    "talent.com",
    "jobijoba.",
    "optioncarriere.",
    "jobted.",
    "zippia.",
    "ziprecruiter.",
    "builtin.",
    "teaserjobs",
    "jobleads.",
    "jobrapido.",
    "careerjet.",
    "simplyhired.",
    "neuvoo.",
    "adzuna.",
    "keljob.",
    "regionsjob.",
    "staffingindustry.",
    "emploi.lefigaro",
    "choisirleservicepublic.",
    "francetravail.",
    "pole-emploi.",
)


# Careers-site software used by employers: a link there is the employer's own form.
ATS_HOSTS = (
    "lever.co",
    "greenhouse.io",
    "ashbyhq.com",
    "smartrecruiters.com",
    "workable.com",
    "recruitee.com",
    "teamtailor.com",
    "myworkdayjobs.com",
    "workday.com",
    "successfactors.",
    "taleo.net",
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "personio.",
    "welcomekit.co",
    "flatchr.io",
    "breezy.hr",
    "pinpointhq.com",
    "join.com",
    "softgarden.",
    "talent-soft.com",
    "csod.com",
    "jobs.ashbyhq.com",
    "eightfold.ai",
    "oraclecloud.com",
    "avature.net",
    "beetween.com",
    "digitalrecruiters.com",
    "jobaffinity.fr",
)


def _company_tokens(company):
    words = re.sub(r"[^a-z0-9 ]+", " ", plain(company).lower()).split()
    stop = {"group", "groupe", "france", "the", "and", "les", "des", "sas", "inc", "ltd", "partners", "consulting"}
    toks = [w for w in words if len(w) >= 4 and w not in stop]
    return toks + (["".join(words)] if len(words) > 1 else [])


def is_employer_link(url, company=None) -> bool:
    """Known careers software, or a domain bearing the company's name (careers.example.com, jobs.acme.fr)."""
    from urllib.parse import urlparse

    host = urlparse(str(url or "")).netloc.lower()
    if not host or any(b in host for b in JOB_BOARDS):
        return False
    if any(a in host for a in ATS_HOSTS):
        return True
    return bool(company) and any(t in host.replace("-", "") for t in _company_tokens(company))


def best_apply_link(options, company=None) -> str | None:
    """The employer's own apply link when Google Jobs lists one; else a non-board link; else the first one."""
    links = [o.get("link") for o in (options or []) if isinstance(o, dict) and o.get("link")]
    from urllib.parse import urlparse

    employer = [u for u in links if is_employer_link(u, company)]
    direct = [u for u in links if not any(b in urlparse(str(u)).netloc.lower() for b in JOB_BOARDS)]
    return (employer or direct or links or [None])[0]


def list_google_jobs(
    keywords, location="Paris, Ile-de-France, France", max_searches=4, rotate=True
) -> list[dict[str, Any]]:
    """Google Jobs results through SerpApi (paid API with a free monthly quota). Env: SERPAPI_KEY.
    max_searches caps the API calls per collection run (budget)."""
    key = _key("SERPAPI_KEY")
    keywords = list(keywords or [])
    if rotate and keywords:
        # A few searches per day: start from a different keyword each day so all of them get covered.
        from datetime import date

        start = date.today().toordinal() % len(keywords)
        keywords = keywords[start:] + keywords[:start]
    out, seen, searches = [], set(), 0
    for kw in keywords:
        if searches >= max_searches:
            break
        # Measured: the 'location' parameter returns nothing for French queries; the city inside the query works.
        city = str(location or "").split(",")[0].strip()
        params = {
            "engine": "google_jobs",
            "q": f"{kw} {city}".strip(),
            "google_domain": "google.fr",
            "hl": "fr",
            "gl": "fr",
            "api_key": key,
        }
        data = get_json(f"https://serpapi.com/search.json?{urlencode(params)}")
        searches += 1
        for j in data.get("jobs_results", []):
            ext = j.get("job_id") or j.get("share_link")
            if ext in seen:
                continue
            seen.add(ext)
            ext_info = j.get("detected_extensions") or {}
            apply = best_apply_link(j.get("apply_options"), j.get("company_name")) or j.get("share_link")
            job = _job(
                "google_jobs",
                "serpapi",
                ext,
                j.get("title"),
                j.get("company_name"),
                j.get("location"),
                j.get("description"),
                apply,
                posted=_relative_date(ext_info.get("posted_at")),
                employment_type=ext_info.get("schedule_type") or "",
            )
            job["application_route"] = "external_or_manual"
            job["via"] = j.get("via", "")
            out.append(job)
    return out


def _relative_date(text):
    """'il y a 3 jours' / '3 days ago' / 'il y a 5 heures' -> YYYY-MM-DD ('' if unknown)."""
    from datetime import datetime, timedelta

    m = re.search(r"(\d+)\s*(heure|hour|jour|day|semaine|week|mois|month)", str(text or ""), re.I)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * (
        7
        if unit.startswith(("semaine", "week"))
        else 30
        if unit.startswith(("mois", "month"))
        else 0
        if unit.startswith(("heure", "hour"))
        else 1
    )
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


LISTERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "ashby": list_ashby,
    "smartrecruiters": list_smartrecruiters,
    "workable": list_workable,
    "recruitee": list_recruitee,
    "teamtailor": list_teamtailor,
}
# Keyword-search sources (need the user's own free API key in an environment variable).
SEARCHERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "adzuna": list_adzuna,
    "jooble": list_jooble,
    "google_jobs": list_google_jobs,
}


def slug_candidates(company) -> list[str]:
    base = re.sub(r"[^a-z0-9 ]+", "", plain(company).lower()).strip()
    words = base.split()
    cands = ["".join(words), "-".join(words), words[0] if words else ""]
    return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


def probe(company, timeout=10) -> list[dict[str, Any]]:
    """Find which public job boards exist for a company. Returns [{type, board, jobs}]."""
    from fitly.sources.greenhouse_client import list_jobs as gh
    from fitly.sources.lever_client import list_postings as lv

    found = []
    checks = {
        "greenhouse": lambda s: len(gh(s, content=False).get("jobs", [])),
        "lever": lambda s: len(lv(s)),
        "ashby": lambda s: len(
            get_json(f"https://api.ashbyhq.com/posting-api/job-board/{s}", retries=0, timeout=timeout).get("jobs", [])
        ),
        "smartrecruiters": lambda s: get_json(
            f"https://api.smartrecruiters.com/v1/companies/{s}/postings?limit=1", retries=0, timeout=timeout
        ).get("totalFound", 0),
        "workable": lambda s: len(
            get_json(f"https://apply.workable.com/api/v1/widget/accounts/{s}", retries=0, timeout=timeout).get(
                "jobs", []
            )
        ),
        "recruitee": lambda s: len(
            get_json(f"https://{s}.recruitee.com/api/offers/", retries=0, timeout=timeout).get("offers", [])
        ),
        "teamtailor": lambda s: len(list_teamtailor(s)),
    }
    for slug in slug_candidates(company):
        for kind, fn in checks.items():
            try:
                n = fn(slug)
            except Exception:  # deliberate: an unknown board fails in many ways (404, HTML instead of JSON, bad XML...)
                continue
            # Lever answers [] for unknown boards and SmartRecruiters 0 for unknown companies.
            if n:
                found.append({"type": kind, "board": slug, "jobs": n})
    return found
