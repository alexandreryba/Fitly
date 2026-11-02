"""Health of every data source after a collection: offers collected, relevant offers (score >= 55), errors,
missing API keys. Written by morning_run.py to data/source_status.json, shown in the interface's sidebar."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fitly import config, paths

RELEVANT = 55
SEARCH_KEYS = {
    "google_jobs": ["SERPAPI_KEY"],
    "jooble": ["JOOBLE_API_KEY"],
    "adzuna": ["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
    "france_travail": ["FT_CLIENT_ID", "FT_CLIENT_SECRET"],
}
LABELS = {
    "lever": "Lever",
    "greenhouse": "Greenhouse",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "workable": "Workable",
    "recruitee": "Recruitee",
    "teamtailor": "Teamtailor",
    "google_jobs": "Google Jobs (SerpApi)",
    "jooble": "Jooble",
    "adzuna": "Adzuna",
    "france_travail": "France Travail",
    "email": "Alertes e-mail",
    "manual": "Ajout manuel",
}


def path_for(db_path=None) -> Path:
    db_path = paths.db_path(db_path)
    return Path(db_path).parent / "source_status.json"


def _key_of(job):
    """(type, board) of a collected job: ids look like 'lever:<board>:<id>' / 'google_jobs:serpapi:<id>'."""
    parts = str(job.get("id") or "").split(":")
    src = job.get("source") or (parts[0] if parts else "")
    if src in SEARCH_KEYS or src in ("email", "manual"):
        return (src, None)
    return (src, parts[1].lower() if len(parts) > 2 else None)


def build(stats, processed, email_count=None, email_error="", email_enabled=None) -> dict[str, Any]:
    """stats: entries from collector_registry.collect_from_config(stats=...); processed: scored offers."""
    relevant: dict[tuple[str, str | None], int] = {}
    collected: dict[tuple[str, str | None], int] = {}
    for j in processed:
        k = _key_of(j)
        collected[k] = collected.get(k, 0) + 1
        if (j.get("score") or 0) >= RELEVANT:
            relevant[k] = relevant.get(k, 0) + 1
    rows = []
    for s in stats:
        k = (
            (s["type"], None)
            if s["type"] in SEARCH_KEYS or s["type"] == "manual"
            else (s["type"], str(s.get("target") or "").lower())
        )
        rows.append(
            {
                **s,
                "label": LABELS.get(s["type"], s["type"]),
                "relevant": relevant.get(k, 0),
                "kept": collected.get(k, 0),
            }
        )
    if email_enabled is not None:
        rows.append(
            {
                "type": "email",
                "name": "Alertes e-mail (LinkedIn, APEC…)",
                "target": "",
                "label": LABELS["email"],
                "status": "error"
                if email_error
                else ("disabled" if not email_enabled else ("ok" if email_count else "empty")),
                "collected": email_count or 0,
                "error": email_error,
                "relevant": relevant.get(("email", None), 0),
                "kept": collected.get(("email", None), 0),
            }
        )
    return {"at": datetime.now(UTC).isoformat(timespec="seconds"), "sources": rows}


def save(report, db_path=None) -> Path:
    p = path_for(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def load(db_path=None) -> dict[str, Any] | None:
    try:
        return json.loads(path_for(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def rebuild_from_last_run(config_path=None, run_path=None, db_path=None) -> dict[str, Any]:
    """Status from the last morning_run.json (for runs made before this file existed). Search sources without
    their API key are reported as 'no_key'."""
    from fitly import user_env

    cfg = json.loads(Path(config_path or config.path("ats_sources.json")).read_text(encoding="utf-8"))
    run_path = Path(run_path or path_for(db_path).parent / "morning_run.json")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    errors = {(e.get("source"), str(e.get("target") or "").lower()): e.get("error") for e in run.get("errors", [])}
    processed = run.get("ranked_jobs", [])
    counts: dict[tuple[str, str | None], int] = {}
    for j in processed:
        counts[_key_of(j)] = counts.get(_key_of(j), 0) + 1
    stats = []
    for item in cfg.get("sources", []):
        kind = item["type"]
        target = item.get("board_token") if kind == "greenhouse" else item.get("site_name") or item.get("board")
        entry = {
            "type": kind,
            "name": item.get("company") or target or kind,
            "target": target,
            "error": "",
            "collected": counts.get((kind, None) if kind in SEARCH_KEYS else (kind, str(target or "").lower()), 0),
        }
        err = errors.get((kind, str(target or "").lower()))
        if item.get("enabled") is False:
            entry["status"] = "disabled"
        elif kind in SEARCH_KEYS and not all(user_env.get(k) for k in SEARCH_KEYS[kind]):
            entry["status"] = "no_key"
        elif err:
            entry.update(status="error", error=str(err)[:200])
        else:
            entry["status"] = "ok" if entry["collected"] else "empty"
        stats.append(entry)
    report = build(stats, processed)
    report["at"] = datetime.fromtimestamp(run_path.stat().st_mtime, UTC).isoformat(timespec="seconds")
    return report
