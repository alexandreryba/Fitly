"""Per-company application quota and profile coherence.

Rule (config/application_rules.json):
  max_offers_per_company          (default 5) — offers engaged or shown per company when they are variants
                                                of the same job (Data Engineer, Senior Data Engineer, Data
                                                Engineer Retail...): applying to all of them is one candidacy.
  max_distinct_roles_per_company  (default 3) — different jobs per company (Data Engineer, Solution
                                                Architect, Analytics Engineer).
  company_window_days             (default 90) — refusals / no answer older than this free their slot.
Engaged offers (GO, prepared, sent, followed up, interview, offer, recent refusals) count first; offers to
triage are then shown best-score first while both limits allow it, the rest goes to the reserve. A NO-GO
takes no slot, so the next reserve offer moves up by itself.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fitly import config
from fitly.scoring.novelty import norm
from fitly.scoring.pipeline import _norm as fold  # accent-folding: 'ingénieur' -> 'ingenieur'
from fitly.storage import connect

ACTIVE = ("go", "prepared", "submitted", "followed_up", "interview", "offer")
CLOSED_RECENT = ("rejected", "no_response", "withdrawn")

# Order matters: the first family whose pattern matches the title wins.
FAMILIES = [
    ("Management", r"\b(engineering manager|head of|director|directeur|directrice|practice manager|cto|cdo|chief)\b"),
    ("Architecture data", r"\b(architect|architecte)\b"),
    ("Produit", r"\b(product owner|product manager|chef de produit|\bpo\b)"),
    ("Business analysis", r"\b(business analyst|analyste fonctionnel|moa)\b"),
    (
        "Ingénierie data / IA",
        r"\b(engineer|ingenieur|ingenieure|developer|developpeur|developpeuse|mlops|devops|sre)\b",
    ),
    ("Data science", r"\b(scientist|data science|machine learning|research)\b"),
    ("Analyse data", r"\b(analyst|analyste|bi\b|business intelligence)"),
    ("Conseil", r"\b(consultant|consultante|consulting|conseil)\b"),
]
# Pairs a recruiter reads as one coherent profile.
COMPATIBLE = [
    {"Ingénierie data / IA", "Architecture data"},
    {"Ingénierie data / IA", "Data science"},
    {"Analyse data", "Data science"},
    {"Analyse data", "Business analysis"},
    {"Produit", "Business analysis"},
    {"Conseil", "Ingénierie data / IA"},
    {"Conseil", "Architecture data"},
]


def family(title):
    t = fold(title)
    for name, pattern in FAMILIES:
        if re.search(pattern, t):
            return name
    return "Autre"


# Target roles that are the same job for a recruiter (grouped under one "core").
CORE_OF_ROLE = {
    "junior data architect": "data architect",
    "data cloud architect": "data architect",
    "cloud data architect": "data architect",
    "data solution architect": "data architect",
    "data architect": "data architect",
    "enterprise data architect": "data architect",
    "ai engineer": "ai engineer",
    "genai engineer": "ai engineer",
    "applied ai engineer": "ai engineer",
    "ai data engineer": "data engineer",
    "data engineer": "data engineer",
    "machine learning engineer": "machine learning engineer",
    "product owner data": "product",
    "product manager data": "product",
    "data consultant": "data consultant",
    "data & ai consultant": "data consultant",
    "data transformation consultant": "data consultant",
    "business analyst data": "business analyst",
    "data business analyst": "business analyst",
}
# Words that make a variant of the same job (seniority, contract, gender marker, place, sector...).
VARIANT_WORDS = (
    r"junior|jr|senior|sr|lead|confirme|confirmee|experimente|experimentee|stage|stagiaire|alternance|"
    r"cdi|cdd|freelance|h|f|x|nb|n|m|w|d|paris|lyon|lille|nantes|niort|rennes|toulouse|bordeaux|"
    r"strasbourg|bruxelles|luxembourg|france|idf|remote|hybrid|teletravail|secteur|retail|banque|"
    r"assurance|sante|industrie|energie|luxe|public|chez|for|the|de|des|du|en|and|et|a|au|"
    r"gcp|aws|azure|snowflake|databricks|dbt|fabric|microsoft|google|spark|python"
)


@lru_cache(maxsize=20000)
def _family_cached(title):
    return family(title)


def rules(path=None):
    p = Path(path) if path else config.path("application_rules.json")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {
        "daily_selection": {"size": 10, "min_score": 80, "max_age_days": 14, **(data.get("daily_selection") or {})},
        "max_offers_per_company": int(data.get("max_offers_per_company", 5)),
        "max_distinct_roles_per_company": int(data.get("max_distinct_roles_per_company", 3)),
        "company_window_days": int(data.get("company_window_days", 90)),
    }


_PREFS = None


def _preferences():
    global _PREFS
    if _PREFS is None:
        try:
            _PREFS = config.load("preferences.json")
        except (OSError, ValueError):
            _PREFS = {}
    return _PREFS


_ROLE_PATTERNS = None


def _role_patterns(preferences):
    """[(compiled phrase regex, folded role, priority)] — built once."""
    from fitly.scoring.pipeline import _roles

    out = []
    for role, priority in _roles(preferences):
        r = fold(role)
        if r:
            out.append((re.compile(rf"(?<![a-z0-9]){re.escape(r)}(?![a-z0-9])"), r, priority))
    return sorted(out, key=lambda x: x[2])


def role_core(title, preferences=None):
    """Same core = same job for a recruiter. Target-role phrase first, else the title without variant words.

    Exact phrase matching only (no fuzzy comparison): this runs for every offer on every screen refresh.
    """
    if preferences is not None:
        return _role_core(title, _role_patterns(preferences))
    return _role_core_cached(str(title or ""))


@lru_cache(maxsize=20000)
def _role_core_cached(title):
    global _ROLE_PATTERNS
    if _ROLE_PATTERNS is None:
        _ROLE_PATTERNS = _role_patterns(_preferences())
    return _role_core(title, _ROLE_PATTERNS)


def _role_core(title, patterns):
    from fitly.scoring.pipeline import _title_for_matching

    t = _title_for_matching(title)
    for rx, role, _prio in patterns:  # sorted by priority: first hit wins (same tie rule as scoring)
        if rx.search(t):
            return CORE_OF_ROLE.get(role, role)
    t = fold(re.split(r"\s[-–|]\s|\(", str(title or ""))[0])
    words = [w for w in t.split() if not re.fullmatch(VARIANT_WORDS, w)]
    return " ".join(words) or t


def engaged_by_company(db_path, window_days=90, now=None):
    """{normalized company: [{job_id, title, status, family}]} of offers occupying a slot."""
    now = now or datetime.now(UTC)
    limit = (now - timedelta(days=window_days)).isoformat()
    con = connect(db_path)
    try:
        rows = con.execute(
            f"""SELECT a.job_id, a.status, a.applied_at, a.last_action_at, j.company, j.title
                               FROM applications a JOIN jobs j ON j.id=a.job_id
                               WHERE a.status IN ({",".join("?" * len(ACTIVE + CLOSED_RECENT))})""",
            ACTIVE + CLOSED_RECENT,
        ).fetchall()
    finally:
        con.close()
    out = {}
    for r in rows:
        when = r["applied_at"] or r["last_action_at"] or ""
        if r["status"] in CLOSED_RECENT and when < limit:
            continue
        out.setdefault(norm(r["company"]), []).append(
            {
                "job_id": r["job_id"],
                "title": r["title"],
                "status": r["status"],
                "family": family(r["title"]),
                "core": role_core(r["title"]),
            }
        )
    return out


def apply(queue, db_path, max_offers=None, max_roles=None, window_days=None):
    """Annotate triage rows with quota='visible'|'reserve', a readable reason, family and role core."""
    cfg = rules()
    max_offers = cfg["max_offers_per_company"] if max_offers is None else max_offers
    max_roles = cfg["max_distinct_roles_per_company"] if max_roles is None else max_roles
    window_days = cfg["company_window_days"] if window_days is None else window_days
    engaged = engaged_by_company(db_path, window_days)
    state = {}  # company -> {"count": n, "cores": {core,...}}
    for key, items in engaged.items():
        state[key] = {"count": len(items), "cores": {e["core"] for e in items}, "engaged": len(items)}
    for r in sorted(
        (r for r in queue if r["application_status"] == "not_started"), key=lambda r: (-(r["score"] or 0), r["id"])
    ):
        key = norm(r["company"])
        st_ = state.setdefault(key, {"count": 0, "cores": set(), "engaged": 0})
        r["family"], r["role_core"] = _family_cached(r["title"]), role_core(r["title"])
        r["company_engaged"] = st_["engaged"]
        same_job = r["role_core"] in st_["cores"]
        if st_["count"] >= max_offers:
            r["quota"] = "reserve"
            r["quota_reason"] = f"{max_offers} offres déjà retenues ou affichées pour cette entreprise"
        elif not same_job and len(st_["cores"]) >= max_roles:
            r["quota"] = "reserve"
            r["quota_reason"] = (
                f"{max_roles} métiers différents déjà retenus ici (" + ", ".join(sorted(st_["cores"])) + ")"
            )
        else:
            r["quota"], r["quota_reason"] = "visible", ""
            st_["count"] += 1
            st_["cores"].add(r["role_core"])
    for r in queue:
        r.setdefault("quota", "engaged" if r["application_status"] != "not_started" else "visible")
        r.setdefault("quota_reason", "")
        if "family" not in r:
            r["family"] = _family_cached(r["title"])
        if "role_core" not in r:
            r["role_core"] = role_core(r["title"])
    return queue


def coherence_warning(job_id, db_path, window_days=None):
    """Message when this offer's job family does not fit the other engaged offers at the same company."""
    window_days = rules()["company_window_days"] if window_days is None else window_days
    con = connect(db_path)
    try:
        job = con.execute("SELECT company, title FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        con.close()
    if not job:
        return None
    mine = family(job["title"])
    others = [
        e for e in engaged_by_company(db_path, window_days).get(norm(job["company"]), []) if e["job_id"] != job_id
    ]
    clash = [e for e in others if e["family"] != mine and {e["family"], mine} not in COMPATIBLE]
    if not clash:
        return None
    return (
        f"Cohérence : chez {job['company']}, tu as aussi « "
        + "», « ".join(e["title"] for e in clash)
        + f" » ({', '.join(sorted({e['family'] for e in clash}))}). Ce poste relève de « {mine} » : "
        "un recruteur qui voit toutes tes candidatures pourrait y lire un profil dispersé."
    )
