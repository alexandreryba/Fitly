"""Deterministic local scoring and CV selection. No network calls.

Each offer gets a 0-100 score from the role it matches in preferences.json (priority ladder), the skills of
profile.json, location, seniority, posting age and explicit penalties (internship, off-target role, avoided
company...), never saturated at 100. Titles are accent-folded so French and English titles match alike.
The CV is chosen from cv_profiles.json: job family from the title, then language. The point breakdown is
kept in ``match_explanation`` so every score can be explained in the interface.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from fitly.scoring.novelty import JobIndex


def _norm(value):
    """Lowercase, strip accents (é -> e) and collapse punctuation to spaces."""
    value = unicodedata.normalize("NFKD", str(value or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#.]+", " ", value)).strip()


def _has(pattern, text):
    return re.search(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])", text) is not None


# French title phrasings mapped to the English role names used in preferences.
_TITLE_ALIASES = (
    (r"ingenieure? (?:de )?(?:data|donnees)", "data engineer"),
    (r"ingenieure? (?:ia|ai|intelligence artificielle)|ia engineer", "ai engineer"),
    (r"ingenieure? (?:machine learning|ml)|ml engineer|mlops engineer|ingenieure? mlops", "machine learning engineer"),
    (r"(?:ai|ia) architect|architecte (?:ia|ai)", "data architect"),
    (r"architecte (?:de )?(?:data|donnees)", "data architect"),
    (r"architecte (?:de )?solutions? (?:data|donnees)", "data solution architect"),
    (r"architecte cloud", "cloud architect"),
    (r"architecte cloud data|architecte data cloud", "data cloud architect"),
    (r"analyste (?:de )?(?:data|donnees)", "data analyst"),
    (r"consultante? (?:data|donnees)", "data consultant"),
)


# Generic titles that become a target role when the title also names data/AI
# (e.g. "Solution Architect - Theodo Data & IA", "AI Product Manager").
_CONTEXT_ALIASES = (
    (r"solutions? architect|architecte (?:de )?solutions?", "data solution architect"),
    (r"product manager", "product manager data"),
    (r"product owner", "product owner data"),
)


def _title_for_matching(title):
    out = _norm(title)
    extra = [alias for pattern, alias in _TITLE_ALIASES if re.search(pattern, out)]
    if _has(_DATA_AI, out):
        extra += [alias for pattern, alias in _CONTEXT_ALIASES if _has(pattern, out)]
    return " ".join([out, *extra])


def _roles(preferences):
    roles = []
    for key, values in (preferences.get("roles") or {}).items():
        m = re.search(r"(\d+)", key)
        priority = int(m.group(1)) if m else 99
        if isinstance(values, list):
            roles.extend((str(v), priority) for v in values)
    return roles


def _role_match(job, preferences):
    """Prefer real role phrases; do not treat scattered words as an exact match."""
    title = _title_for_matching(job.get("title"))
    candidates = _roles(preferences)
    if not candidates:
        return 0.0, None, 99, False
    best = (0.0, None, 99, False)
    title_tokens = set(title.split())
    for role, priority in candidates:
        nrole = _norm(role)
        if not nrole:
            continue
        phrase_match = _has(re.escape(nrole), title)
        if phrase_match:
            similarity = 1.0
        else:
            seq = SequenceMatcher(None, title, nrole).ratio()
            role_tokens = set(nrole.split())
            overlap = len(title_tokens & role_tokens) / max(1, len(role_tokens))
            # Token overlap alone is weak evidence (e.g. SRE mentioning an AI platform).
            similarity = max(seq, min(0.64, overlap * 0.64))
        better = similarity > best[0] or (similarity == best[0] and priority < best[2])
        if better:
            best = (similarity, role, priority, phrase_match)
    return best


_REMOTE = r"full remote|fully remote|100 remote|remote first|remote france|remote only|teletravail complet|100 teletravail|full teletravail"
_IDF = (
    r"paris|ile de france|idf|la defense|levallois|boulogne|courbevoie|nanterre|saint denis|puteaux|issy|neuilly"
    r"|montrouge|clichy|saint ouen|ivry|vincennes|montreuil|rueil|versailles|velizy|guyancourt|massy|palaiseau|saclay"
)
_FRANCE = (
    r"france|lyon|lille|toulouse|nantes|bordeaux|strasbourg|rennes|montpellier|marseille|nice|grenoble|brest"
    r"|dijon|angers|rouen|niort|metz|nancy|tours|clermont ferrand|aix en provence|sophia antipolis|orleans|caen"
)


def _is_remote(job):
    location = _norm(job.get("location"))
    text = _norm(" ".join(str(job.get(k) or "") for k in ("title", "description")))
    return _has(r"remote|teletravail", location) or _has(_REMOTE, text)


_INTERNSHIP = r"intern|internship|stage|stagiaire|apprenti|apprentie|apprentice|apprenticeship|apprentissage|alternance|alternant|alternante|working student|v\.i\.e"
_EXECUTIVE = r"principal|staff|head|director|directeur|directrice|chief|vp|vice president|cto|cdo"
_LEAD = r"lead|leader|tech lead|team lead|team leader|manager|engineering manager"
# Job families whose title contains "manager" without implying a management level.
_NOT_SENIORITY = r"product manager|project manager|program manager|account manager|key account manager"
_EXPERIENCE = re.compile(
    r"(\d{1,2})\s*(?:\+|ou plus|or more)?\s*(?:-\s*\d{1,2}\s*)?(?:ans|annees|years?)\s*(?:minimum\s*)?"
    r"(?:d |de |of |of professional |of relevant |of hands on )?(?:experience)"
)
_SENIOR = r"senior|sr"
_JUNIOR = r"junior|jr|debutant|debutante|jeune diplome|jeune diplomee|graduate|entry level|new grad|early career"
_MID = r"confirme|confirmee|experimente|experimentee|experienced"
_UNRELATED = (
    r"sre|site reliability|fullstack|full stack|frontend|front end|backend|back end|software engineer|devops"
    r"|mobile|ios|android|qa|sales|account executive|account manager|business developer|recruiter|talent"
    r"|marketing manager|customer success|support engineer|security engineer|network|quality engineer|test engineer"
    r"|field application|solutions? engineer|sales engineer|pre sales|presales|business development|bdr|sdr"
    r"|hr business partner|human resources|ressources humaines|office manager|legal|counsel|accountant|comptable"
)
_DATA_AI = r"data|donnees|ai|ia|ml|mlops|machine learning|genai|llm|analytics"


def _required_years(description):
    """Smallest explicit 'N years of experience' requirement in the ad, if any."""
    found = [int(n) for n in _EXPERIENCE.findall(_norm(description))]
    found = [n for n in found if 0 < n <= 20]
    return min(found) if found else None


def _candidate_years(profile):
    if isinstance(profile.get("years_experience"), (int, float)):
        return profile["years_experience"]
    m = re.search(r"(\d{1,2})\s*(?:ans|years)", _norm(profile.get("experience")))
    return int(m.group(1)) if m else None


def _age_days(posted, today=None):
    """Days since the offer was published (posted_at 'YYYY-MM-DD'), None if unknown."""
    from datetime import date

    try:
        d = date.fromisoformat(str(posted)[:10])
    except (TypeError, ValueError):
        return None
    return ((today or date.today()) - d).days


def _score_details(job, preferences, profile):
    """Return (score, matched_role, skill_hits, breakdown).

    Points: base 13 + role fit (<=50) + skills (<=15) + location (-15..+15),
    then explicit penalties. A perfect target offer lands around 90, never on a
    keyword-only 100. Heuristic ranking aid, not a probability of being hired.
    """
    sim, matched_role, priority, phrase_match = _role_match(job, preferences)
    title = _norm(job.get("title"))
    text = _norm(" ".join(str(job.get(k) or "") for k in ("title", "description")))
    b = {"base": 13}

    p = min(priority, 8)
    if phrase_match:
        b["role"] = 50 - 3 * p  # target title; priority_0=50 ... priority_8=26
    elif sim >= 0.65:
        b["role"] = 30 - p  # adjacent title
    elif sim >= 0.40:
        b["role"] = 16
    else:
        b["role"] = 6

    skills = [_norm(x) for x in profile.get("skills", []) if str(x).strip()]
    skill_hits = sum(1 for skill in skills if skill and _has(re.escape(skill), text))
    b["skills"] = round(15 * min(skill_hits, 8) / 8)  # full 15 points at 8 profile skills

    location = _norm(job.get("location"))
    remote_ok = (preferences.get("location") or {}).get("remote_ok", True)
    if _has(_IDF, location):
        b["location"] = 15
    elif remote_ok and _is_remote(job):
        b["location"] = 10
    elif _has(_FRANCE, location):
        b["location"] = 0
    elif location:
        b["location"] = -15
    else:
        b["location"] = 0

    contract = _norm(" ".join(str(job.get(k) or "") for k in ("employment_type", "commitment", "contract")))
    if _has(_INTERNSHIP, title) or _has(_INTERNSHIP, contract):
        b["internship"] = -50
    # Seniority ladder (user target): junior ~100, standard ~90, confirmé ~85, senior ~70, lead/exec lower.
    level_title = re.sub(rf"(?<![a-z0-9])(?:{_NOT_SENIORITY})(?![a-z0-9])", " ", title)
    required = _required_years(job.get("description"))
    if _has(_EXECUTIVE, level_title):
        b["seniority"] = -35
    elif _has(_LEAD, level_title):
        b["seniority"] = -25
    elif _has(_SENIOR, title):
        b["seniority"] = -20
    elif _has(_JUNIOR, title):
        b["seniority"] = 10
    elif _has(_MID, title):
        b["seniority"] = -5
    elif required is not None and required <= 2:
        b["seniority"] = 5  # entry-level requirement stated in the ad
    have = _candidate_years(profile)
    if required is not None and have is not None and required > have:
        b["experience"] = -5 if required - have <= 2 else -10
    age = _age_days(job.get("posted_at"))
    if age is not None and age > 21:
        b["age"] = -8 if age <= 45 else -15 if age <= 90 else -25
    generic_platform = _has(r"platform engineer|cloud engineer", title) and not _has(_DATA_AI, title)
    if (_has(_UNRELATED, title) or generic_platform) and not phrase_match:
        b["unrelated_role"] = -20

    contracts = preferences.get("contracts") or {}
    freelance_ranked_lower = contracts.get("freelance_priority", 2) > contracts.get("cdi_priority", 1)
    if freelance_ranked_lower and _has(r"freelance|contractor|independant", title + " " + contract):
        b["freelance"] = -5

    salary = preferences.get("salary", {})
    raw = str(job.get("salary") or job.get("salary_text") or "")
    nums = [int(re.sub(r"\D", "", n)) for n in re.findall(r"(?<!\d)(\d{2,3}(?:[  .,]?\d{3})+)(?!\d)", raw)]
    minimum = salary.get("minimum")
    if minimum and nums and max(nums) < minimum:
        b["salary"] = -8

    score = sum(b.values())
    avoid = [_norm(x) for x in preferences.get("avoid_companies", [])]
    company = _norm(job.get("company"))
    if any(x and _has(re.escape(x), company) for x in avoid):
        b["avoid_company_cap"] = 10
        score = min(score, 10)

    return max(0, min(100, round(score))), matched_role, skill_hits, b


def _score(job, preferences, profile):
    score, role, skill_hits, _ = _score_details(job, preferences, profile)
    return score, role, skill_hits


_FR_TITLE = (
    r"developpeur|developpeuse|ingenieur|ingenieure|architecte|analyste|chef de projet|responsable|confirme|confirmee"
    r"|experimente|experimentee|alternance|alternant|stage|stagiaire|teletravail|donnees|chez|pour|avec|h f|f h|f h nb|h f nb"
)
_FR_TEXT = r"le|la|les|des|du|vous|votre|vos|nous|notre|nos|avec|pour|dans|sur|une|est|sont|equipe|poste|missions|experience|competences"
_EN_TEXT = r"the|you|your|we|our|with|for|and|team|role|experience|requirements|responsibilities|skills|will|are|is"
_TECH_TITLE = (
    r"engineer|ingenieur|ingenieure|developer|developpeur|developpeuse|architect|architecte|scientist|mlops|devops"
)
_BUSINESS_TITLE = (
    r"consultant|consultante|consulting|business analyst|product owner|product manager|chef de projet|transformation"
)


def _language(job):
    """Language of the ad: title cues first, then description word counts."""
    title = _norm(job.get("title"))
    if _has(_FR_TITLE, title):
        return "fr"
    desc = _norm(job.get("description"))
    fr = len(re.findall(rf"(?<![a-z0-9])(?:{_FR_TEXT})(?![a-z0-9])", desc))
    en = len(re.findall(rf"(?<![a-z0-9])(?:{_EN_TEXT})(?![a-z0-9])", desc))
    if fr != en:
        return "fr" if fr > en else "en"
    # No usable signal: French for offers located in France, English otherwise.
    location = _norm(job.get("location"))
    return "fr" if not location or _has(_IDF + "|" + _FRANCE, location) else "en"


# CV families (config/cv_profiles.json "track"): the title decides, most specific rule first.
_DATA_TECH_TITLE = (
    r"architect|architecte|data engineer|analytics engineer|ingenieur (de )?donnees|ingenieur data|"
    r"data platform|dataops|big data|bi developer|developpeur bi|data analyst|analyste (de )?donnees|analyste (data|bi)"
)
_DS_TITLE = r"data scien|scientist|statisticien|statistician|quantitative (analyst|researcher)"
_AI_TITLE = (
    r"(?<![a-z])(ai|ia|genai|gen ai|llm|nlp|agents?|agentic|ml|mlops|machine learning|deep learning|"
    r"computer vision|applied ai|forward deployed|deployment strategist)(?![a-z])"
)
_ENGINEER = r"engineer|ingenieur|ingenieure|developer|developpeur|developpeuse|devops"
_BUSINESS_ROLE = (
    r"consultant|consultante|consulting|business analyst|analyste fonctionnel|moa|amoa|product owner|product manager|"
    r"(?<![a-z])po(?![a-z])|chef de projet|project manager|transformation"
)
# When a family has no CV of its own (older cv_profiles.json), the closest one is used.
TRACK_FALLBACK = {"ai": "tech", "datascience": "tech", "tech": "tech", "business": "business"}


def _track(job):
    """CV family of an offer: 'tech' (data engineering, analytics, architecture), 'datascience', 'ai'
    (AI/GenAI/LLM/ML engineering, forward deployed) or 'business' (consulting, MOA, BA, PO/PM)."""
    title = _norm(job.get("title"))
    if _has(_DATA_TECH_TITLE, title):
        return "tech"
    if _has(_ENGINEER, title):  # an engineering title: AI work, science, else data tech
        if _has(_AI_TITLE, title):
            return "ai"
        return "datascience" if _has(_DS_TITLE, title) else "tech"
    if _has(_DS_TITLE, title):
        return "datascience"
    if _has(_BUSINESS_ROLE, title):
        return "business"
    if _has(_AI_TITLE, title):
        return "ai"
    return "tech" if _has(_TECH_TITLE, title) else ("business" if _has(_BUSINESS_TITLE, title) else "tech")


# The title decides the CV; the offer's technologies may move it to a NEIGHBOURING technical CV (AI <-> Tech <->
# Data Science) only when they clearly point there (e.g. an "AI Engineer" offer that is really Spark/dbt/Airflow).
TECH_TRACKS = ("ai", "tech", "datascience")
KEYWORD_MARGIN = 3


def _keyword_hits(text, keywords):
    return sum(1 for k in keywords if re.search(rf"(?<![a-z0-9]){re.escape(_norm(k))}(?![a-z0-9])", text))


def _track_by_keywords(job, track, cvs):
    if track not in TECH_TRACKS:
        return track
    text = _norm(f"{job.get('title') or ''} {job.get('description') or ''}")
    hits = {}
    for c in cvs:
        if c.get("track") in TECH_TRACKS and c.get("keywords") and c["track"] not in hits:
            hits[c["track"]] = _keyword_hits(text, c["keywords"])
    if not hits:
        return track
    best = max(hits, key=hits.get)
    return best if hits[best] >= hits.get(track, 0) + KEYWORD_MARGIN else track


def _select_cv(job, cv_profiles):
    cvs = cv_profiles.get("cvs", []) if isinstance(cv_profiles, dict) else []
    lang = _language(job)
    track = _track_by_keywords(job, _track(job), cvs)
    if not any(c.get("track") == track for c in cvs):
        track = TRACK_FALLBACK.get(track, "tech")
    choices = [c for c in cvs if c.get("track") == track and c.get("language") == lang]
    if not choices:
        choices = [c for c in cvs if c.get("track") == track] or cvs
    return choices[0].get("id") if choices else None


def _band(score):
    return (
        "apply_now"
        if score >= 80
        else "strong_match"
        if score >= 70
        else "interesting"
        if score >= 55
        else "stretch"
        if score >= 40
        else "ignore"
    )


def run_pipeline(jobs, preferences, profile, cv_profiles):
    """Score every offer once; the same posting from several sources is kept once, with the richest text."""
    unique = JobIndex()
    for raw in jobs:
        job = dict(raw)
        for key in ("title", "company", "location", "description"):
            job[key] = str(job.get(key) or "").strip()
        job["url"] = job.get("url") or job.get("canonical_url") or ""
        position, duplicate = unique.first_match(job)
        if duplicate is not None:
            if len(job.get("description", "")) > len(duplicate.get("description", "")):
                duplicate.update(job)
                _annotate(duplicate, preferences, profile, cv_profiles)  # score the richer text
                unique.reindex(position)
            continue
        _annotate(job, preferences, profile, cv_profiles)
        unique.add(job)
    return sorted(unique.jobs, key=lambda x: x.get("score", 0), reverse=True)


def _annotate(job, preferences, profile, cv_profiles):
    score, role, skill_hits, breakdown = _score_details(job, preferences, profile)
    job["score"] = score
    job["score_band"] = _band(score)
    job["recommended_cv"] = _select_cv(job, cv_profiles)
    job["match_explanation"] = {
        "matched_role": role,
        "skill_hits": skill_hits,
        "score_breakdown": breakdown,
        "score_note": "Heuristic ranking; verify location, contract, language and offer manually.",
    }
