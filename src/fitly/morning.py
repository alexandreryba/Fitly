"""Morning collection: read every source, score, deduplicate, store, then refresh the derived files.

    python -m fitly.morning            (or: fitly morning)

Read-only on the outside world (public APIs, e-mail alerts read-only, the manual sheet). The database is
backed up before any write. Exit code 3: no source answered, nothing was changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from fitly import config, paths
from fitly.scoring.novelty import classify
from fitly.scoring.pipeline import run_pipeline
from fitly.sources import email_alerts
from fitly.sources.collector_registry import collect_from_config
from fitly.storage import upsert_jobs
from fitly.tracking.tracker import backup_db

NO_SOURCE_ANSWERED = 3
RELEVANT = 55


def merge_previous(processed, previous) -> list[dict[str, Any]]:
    """Current batch plus previously seen offers absent today, so a failed or partial
    collection never makes known offers look 'new' on the next run."""

    def key(j):
        if j.get("external_id"):
            return j.get("source"), j.get("external_id")
        return j.get("company"), j.get("title"), j.get("location")

    seen = {key(j) for j in processed}
    return processed + [j for j in previous if key(j) not in seen]


def _step(message):
    """Progress line, shown at once: the run takes minutes and must not look frozen."""
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def _by_score(jobs):
    return sorted(jobs, key=lambda x: x.get("score", 0), reverse=True)


def _manual_offers(stats):
    """Offers saved by hand in the manual sheet; its health is appended to `stats`."""
    from fitly.sources import manual_offers

    entry = {"type": "manual", "name": manual_offers.FILE.name, "target": None, "collected": 0}
    try:
        jobs, problems = manual_offers.read()
    except Exception as exc:  # deliberate: a hand-edited workbook fails in many ways; reported as this source's error
        stats.append({**entry, "status": "error", "error": str(exc)[:200]})
        return []
    status = ("ok" if jobs else "empty") if manual_offers.FILE.exists() else "disabled"
    stats.append({**entry, "collected": len(jobs), "status": status, "error": "; ".join(problems)[:200]})
    return jobs


def _save_source_status(stats, processed, mail_jobs, mail_errors, db):
    """Per-source health shown in the interface's sidebar."""
    try:
        from fitly.sources import source_status

        status = source_status.build(
            stats,
            processed,
            email_count=len(mail_jobs),
            email_error="; ".join(str(e.get("error")) for e in mail_errors)[:200],
            email_enabled=bool(email_alerts.load_config().get("enabled")) or bool(mail_jobs),
        )
        source_status.save(status, db)
    except Exception as exc:  # deliberate: runs before the offers are stored and must never cost the collection
        print(f"Source status not saved: {exc}")


def _print_errors(errors):
    for e in errors:
        print(f"  SOURCE ERROR {e.get('source')}:{e.get('target')} — {e.get('error')}")


def _print_report(payload):
    print("=== FITLY MORNING RUN ===")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print("\nTop ranked offers (all collected, including previously seen):")
    for j in payload["ranked_jobs"][:15]:
        print(
            f"[{j.get('score', 0):.0f}] {j.get('title')} — {j.get('company')} — {j.get('location')}"
            f" — CV={j.get('recommended_cv')}"
        )
    print(f"\nNew offers this run: {len(payload['new_jobs'])}")
    for j in [x for x in payload["new_jobs"] if x.get("score", 0) >= RELEVANT][:10]:
        print(f"  NEW [{j.get('score', 0):.0f}] {j.get('title')} — {j.get('company')} — {j.get('location')}")
    _print_errors(payload["errors"])


def _after_store(db):
    """Steps that only enrich what was stored: a failure there never fails the run."""
    _step("Checking the offers on the employers' own sites...")
    try:  # same offer on the employer's careers site? expired? (public ATS APIs, cached per company)
        from fitly.sources import employer_match

        print(f"\nEmployer-site check: {employer_match.check(db_path=db)}")
    except Exception as exc:  # deliberate: network check of many employer sites, enrichment only
        print(f"\nEmployer-site check skipped: {exc}")
    try:  # Excel view of the offers
        from fitly.review import export_offers

        xlsx, n = export_offers.write(db_path=db)
        print(f"Excel export: {n} offers -> {xlsx}")
    except Exception as exc:  # deliberate: enrichment only (openpyxl also rejects some characters found in offers)
        print(f"Excel export skipped: {exc}")


def parse_args(argv=None) -> argparse.Namespace:
    # In a sandbox (FITLY_DB), the run summary and the history live next to the copy.
    work = paths.work_dir()
    p = argparse.ArgumentParser(prog="fitly morning", description=__doc__)
    p.add_argument("--config", help="sources file (default: ats_sources.json, see fitly.config)")
    p.add_argument("--db", default=paths.db_path())
    p.add_argument("--output", default=str(work / "morning_run.json"))
    p.add_argument("--previous", default=str(work / "last_seen_jobs.json"))
    return p.parse_args(argv)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # emoji titles on cp1252 pipes
    a = parse_args(argv)
    sources = json.loads(paths.project_path(a.config).read_text(encoding="utf-8")) if a.config else None
    stats: list[dict[str, Any]] = []
    _step("Collecting offers from the configured sources (a few minutes)...")
    raw, errors = collect_from_config(sources or config.load("ats_sources.json"), stats=stats)
    mail_jobs, mail_errors = email_alerts.collect()
    raw += mail_jobs + _manual_offers(stats)
    errors += mail_errors

    _step(f"Scoring and deduplicating {len(raw)} offers...")
    processed = run_pipeline(raw, *config.scoring_inputs())
    history = paths.project_path(a.previous)
    previous = json.loads(history.read_text(encoding="utf-8")) if history.exists() else []
    novelty = classify(processed, previous)
    if not raw and errors:
        print("=== FITLY MORNING RUN ===")
        print("No offer collected: every source failed (network?). Database and history left unchanged.")
        _print_errors(errors)
        return NO_SOURCE_ANSWERED

    _step(f"Saving {len(processed)} offers (database backed up first)...")
    _save_source_status(stats, processed, mail_jobs, mail_errors, a.db)
    backup = backup_db(a.db, "before_morning") if processed else None
    stored = upsert_jobs(processed, a.db)
    history.write_text(json.dumps(merge_previous(processed, previous), ensure_ascii=False, indent=2), encoding="utf-8")

    payload = {
        "summary": {
            "collected": len(raw),
            "processed": len(processed),
            "new": len(novelty["new"]),
            "known": len(novelty["known"]),
            "duplicates": len(novelty["duplicates"]),
            "errors": len(errors),
        },
        "new_jobs": _by_score(novelty["new"]),
        "ranked_jobs": _by_score(processed),
        "errors": errors,
        "database": stored,
        "backup": str(backup) if backup else None,
    }
    paths.project_path(a.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(payload)
    _after_store(a.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
