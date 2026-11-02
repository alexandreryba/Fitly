"""AI-drafted application answers (cover note, 'why this role', screening questions) with a hard monthly cost cap.

- Uses the official Anthropic SDK; credentials come from the environment (ANTHROPIC_API_KEY or an
  `ant auth login` profile) — never stored in this project.
- Grounded only in the candidate's own facts (profile.json + optional candidate_facts.md, see fitly.config).
  Missing facts are written as [À COMPLÉTER], never invented. Drafts are for the user to review.
- Budget: before each call the worst-case cost is checked against ai.json "monthly_cap_eur";
  the real cost from response.usage is then added to data/ai_usage.json.
- Without credentials, when disabled, or when the cap would be exceeded: a template draft is returned
  instead (no API call).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fitly import config
from fitly.paths import DATA_DIR

LEDGER = DATA_DIR / "ai_usage.json"
OUT_DIR = DATA_DIR / "answers"

DEFAULTS = {
    "enabled": True,
    "model": "claude-opus-5-5",
    "effort": "low",
    "max_tokens": 8000,
    "monthly_cap_eur": 10.0,
    "usd_to_eur": 0.92,
    # USD per million tokens [input, output]; unknown models are priced at the most expensive entry.
    "pricing_usd_per_mtok": {
        "claude-opus-5-5": [4.0, 20.0],
        "claude-sonnet-5-5": [2.0, 10.0],
        "claude-haiku-5-5": [0.10, 0.50],
        "claude-opus-5": [5.0, 25.0],
        "claude-opus-4-8": [5.0, 25.0],
        "claude-fable-5-1": [10.0, 50.0],
    },
}

SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["fr", "en"]},
        "cover_note": {"type": "string"},
        "why_role": {"type": "string"},
        "why_company": {"type": "string"},
        "key_matches": {"type": "array", "items": {"type": "string"}},
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                "required": ["question", "answer"],
                "additionalProperties": False,
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["language", "cover_note", "why_role", "why_company", "key_matches", "answers", "gaps"],
    "additionalProperties": False,
}

SYSTEM = """You draft job-application material for one candidate, to be reviewed and edited by the candidate before use.
Rules:
- Use ONLY facts present in the CANDIDATE section. Never invent employers, projects, dates, degrees, numbers, years of
  experience or skills. If an answer needs a fact that is not given, write [À COMPLÉTER: what is needed] (in English
  output: [TO COMPLETE: ...]) instead of guessing.
- Write in the requested language. Natural, specific, no clichés, no flattery, no exaggeration.
- cover_note: 120-170 words, ready to paste in an application form message field.
- why_role / why_company: 2-4 sentences each, tied to concrete elements of the offer.
- key_matches: 3-5 short bullets "offer requirement -> candidate fact".
- answers: answer each listed question briefly (max 80 words each).
- gaps: requirements of the offer the candidate's facts do not cover (honest list, may be empty)."""


def load_config(path=None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(Path(path or config.path("ai.json")).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def _month():
    return datetime.now(UTC).strftime("%Y-%m")


def spent_eur(ledger=LEDGER) -> float:
    try:
        data = json.loads(Path(ledger).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0
    return float(data.get(_month(), {}).get("eur", 0.0))


def _record(cost_eur, model, ledger=LEDGER):
    p = Path(ledger)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    m = data.setdefault(_month(), {"eur": 0.0, "calls": 0, "by_model": {}})
    m["eur"] = round(m["eur"] + cost_eur, 5)
    m["calls"] += 1
    m["by_model"][model] = round(m["by_model"].get(model, 0.0) + cost_eur, 5)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _price(cfg, model):
    table = cfg["pricing_usd_per_mtok"]
    return table.get(model) or max(table.values(), key=lambda x: x[1])


def worst_case_eur(cfg, prompt_chars) -> float:
    pin, pout = _price(cfg, cfg["model"])
    est_in = prompt_chars / 3.0 + 1500  # generous chars->tokens estimate + system/schema overhead
    return (est_in * pin + cfg["max_tokens"] * pout) / 1e6 * cfg["usd_to_eur"]


def usage_eur(cfg, usage, model) -> float:
    pin, pout = _price(cfg, model)
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    usd = (
        (usage.input_tokens or 0) * pin + written * pin * 1.25 + cached * pin * 0.1 + (usage.output_tokens or 0) * pout
    ) / 1e6
    return usd * cfg["usd_to_eur"]


def candidate_block(profile, facts_path=None) -> str:
    facts = ""
    try:
        facts = Path(facts_path or config.path("candidate_facts.md")).read_text(encoding="utf-8").strip()
    except OSError:
        pass
    lines = [
        f"Name: {profile.get('name', '')}",
        f"Headline: {profile.get('headline') or profile.get('experience', '')}",
        f"Skills: {', '.join(profile.get('skills', []))}",
    ]
    if facts:
        lines.append("Additional verified facts:\n" + facts)
    return "\n".join(lines)


def build_prompt(job, profile, questions, language, facts_path=None) -> str:
    qs = "\n".join(f"- {q}" for q in questions) or "- (none)"
    return (
        f"LANGUAGE: {language}\n\nOFFER\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nDescription:\n{(job.get('description') or '')[:12000]}\n\n"
        f"CANDIDATE\n{candidate_block(profile, facts_path)}\n\nQUESTIONS\n{qs}"
    )


def template_draft(job, profile, questions, language, reason) -> dict[str, Any]:
    """No-AI fallback: honest skeleton the user completes."""
    fr = language == "fr"
    tc = "[À COMPLÉTER" if fr else "[TO COMPLETE"
    skills = ", ".join(profile.get("skills", [])[:6])
    cover = (
        (
            f"Bonjour,\n\nJe vous adresse ma candidature au poste de {job.get('title')} chez {job.get('company')}. "
            f"{tc} : 2 phrases sur votre expérience la plus proche du poste]. Mes compétences en {skills} "
            f"correspondent à {tc} : exigence clé de l'offre]. {tc} : pourquoi cette entreprise].\n\n"
            f"Je serais ravi d'échanger avec vous.\n\n{profile.get('name', '')}"
        )
        if fr
        else (
            f"Hello,\n\nI am applying for the {job.get('title')} position at {job.get('company')}. "
            f"{tc}: 2 sentences on your closest experience]. My skills in {skills} match {tc}: key requirement]. "
            f"{tc}: why this company].\n\nI would be glad to discuss.\n\n{profile.get('name', '')}"
        )
    )
    return {
        "language": language,
        "cover_note": cover,
        "why_role": f"{tc}]",
        "why_company": f"{tc}]",
        "key_matches": [],
        "answers": [{"question": q, "answer": f"{tc}]"} for q in questions],
        "gaps": [],
        "source": "template",
        "note": reason,
    }


def generate(
    job, profile, questions=(), language="fr", cfg=None, client=None, ledger=LEDGER, facts_path=None
) -> dict[str, Any]:
    """Return a draft dict. `client` may be injected (tests); otherwise anthropic.Anthropic() is used."""
    cfg = cfg or load_config()
    questions = [q for q in questions if q and q.strip()]
    if not cfg.get("enabled"):
        return template_draft(job, profile, questions, language, "AI disabled in config/ai.json")
    prompt = build_prompt(job, profile, questions, language, facts_path)
    cap, spent = float(cfg["monthly_cap_eur"]), spent_eur(ledger)
    if spent + worst_case_eur(cfg, len(prompt) + len(SYSTEM)) > cap:
        return template_draft(
            job, profile, questions, language, f"Monthly AI budget reached ({spent:.2f} € spent of {cap:.2f} €)"
        )
    try:
        import anthropic
    except ImportError:  # optional extra: the SDK's error types are needed below
        return template_draft(job, profile, questions, language, 'AI extra not installed: pip install -e ".[ai]"')

    try:
        client = client or anthropic.Anthropic()
        response = client.beta.messages.create(
            model=cfg["model"],
            max_tokens=int(cfg["max_tokens"]),
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": cfg["effort"], "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError:
        return template_draft(
            job, profile, questions, language, "No valid Anthropic credentials (set ANTHROPIC_API_KEY)"
        )
    except anthropic.RateLimitError:
        return template_draft(job, profile, questions, language, "Anthropic rate limit — retry later")
    except anthropic.APIStatusError as exc:
        return template_draft(job, profile, questions, language, f"Anthropic API error {exc.status_code}")
    except anthropic.APIConnectionError:
        return template_draft(job, profile, questions, language, "Network error reaching the Anthropic API")
    except TypeError:  # the SDK raises TypeError when no credential source is configured at all
        return template_draft(
            job,
            profile,
            questions,
            language,
            "No Anthropic credentials configured: set the ANTHROPIC_API_KEY environment variable",
        )
    except Exception as exc:  # deliberate: any other SDK or network failure still gives the user a template
        return template_draft(job, profile, questions, language, f"AI unavailable: {exc.__class__.__name__}")
    served_by = getattr(response, "model", cfg["model"]) or cfg["model"]
    _record(usage_eur(cfg, response.usage, served_by), served_by, ledger)
    if response.stop_reason == "refusal":
        return template_draft(job, profile, questions, language, "The model declined this request")
    if response.stop_reason == "max_tokens":
        return template_draft(job, profile, questions, language, "Answer truncated (max_tokens) — retry")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        draft = json.loads(text)
    except ValueError:
        return template_draft(job, profile, questions, language, "Unreadable AI answer")
    draft.update({"source": served_by, "note": "AI draft — review every sentence before using it"})
    return draft


def to_markdown(job, draft) -> str:
    lines = [
        f"# Réponses préparées — {job.get('company')} — {job.get('title')}",
        "",
        f"_Source : {draft.get('source')} · {draft.get('note', '')}_",
        "",
        "## Message de candidature",
        "",
        draft.get("cover_note", ""),
        "",
        "## Pourquoi ce poste",
        "",
        draft.get("why_role", ""),
        "",
        "## Pourquoi cette entreprise",
        "",
        draft.get("why_company", ""),
        "",
    ]
    if draft.get("key_matches"):
        lines += ["## Correspondances clés", ""] + [f"- {m}" for m in draft["key_matches"]] + [""]
    if draft.get("answers"):
        lines += ["## Questions", ""]
        for a in draft["answers"]:
            lines += [f"**{a['question']}**", "", a["answer"], ""]
    if draft.get("gaps"):
        lines += ["## Points non couverts par ton profil", ""] + [f"- {g}" for g in draft["gaps"]] + [""]
    return "\n".join(lines)


def save(job_id, job, draft, out_dir=OUT_DIR) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"answers_{job_id}.json").write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    p = out / f"answers_{job_id}.md"
    p.write_text(to_markdown(job, draft), encoding="utf-8")
    return p
