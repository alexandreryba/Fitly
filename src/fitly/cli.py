"""Fitly command line. Local only; never submits an application.

Daily loop:
  morning     collect public offers, score, store             (network, read-only GET)
  review      build review queue JSON + CSV + HTML report     (offline)
  decide      go / no-go on one offer, or `decisions` to import the CSV 'decision' column
  applied     record that YOU submitted an application; schedules a follow-up
  followups   list follow-ups due (+ --drafts writes follow-up templates)
  set         any other status change (interview, rejected, offer, ...)

`FITLY_DB` points every command at another database (sandbox copy); the review files then go next to
it, never over the real ones.
"""

from __future__ import annotations

import argparse
import json
import sys

from fitly import config, paths

KEYED_SOURCES = {
    "google_jobs": ("SERPAPI_KEY",),
    "adzuna": ("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
    "jooble": ("JOOBLE_API_KEY",),
    "france_travail": ("FT_CLIENT_ID", "FT_CLIENT_SECRET"),
}
REQUIRED_CONFIG = ("profile.json", "preferences.json", "application_rules.json", "cv_profiles.json", "ats_sources.json")


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# --- daily loop ----------------------------------------------------------------------------------------
def cmd_morning(a) -> int:
    from fitly import morning

    return morning.main([])


def cmd_review(a, import_first=True) -> int:
    from fitly.review import export_review, report_html, review_queue
    from fitly.tracking.tracker import import_decisions, pending_decisions

    db, work = paths.db_path(), paths.work_dir()
    csv_path = work / "review_queue.csv"
    if import_first and pending_decisions(csv_path):
        # Never regenerate the CSV over go/no-go cells the user has not imported yet.
        print("Importing decisions found in review_queue.csv before regenerating it:")
        _print(import_decisions(csv_path, db_path=db))
    review_queue.main(db=db, out=work / "review_queue.json")
    export_review.main(
        inp=work / "review_queue.json", out=csv_path, delimiter="," if getattr(a, "comma", False) else ";"
    )
    report_html.main(db=db, out=work / "morning_review.html")
    return 0


def cmd_rescore(a) -> int:
    from fitly.tracking.tracker import rescore

    _print(rescore(*config.scoring_inputs(), db_path=paths.db_path()))
    return cmd_review(a) if a.review else 0


def cmd_decide(a) -> int:
    from fitly.tracking.tracker import decide

    _print(decide(a.job_id, a.decision, reason=a.note, db_path=paths.db_path()))
    return 0


def cmd_decisions(a) -> int:
    from fitly.tracking.tracker import import_decisions

    result = import_decisions(a.csv or paths.work_dir() / "review_queue.csv", db_path=paths.db_path())
    _print(result)
    if result["go"] or result["skipped"]:
        cmd_review(a, import_first=False)
    return 1 if result["invalid"] else 0


def cmd_undo(a) -> int:
    from fitly.tracking.tracker import undo

    _print(undo(a.job_id, db_path=paths.db_path()))
    return 0


def cmd_set(a) -> int:
    from fitly.tracking.tracker import set_status

    _print(set_status(a.job_id, a.status, note=a.note, when=a.date, cv=a.cv, db_path=paths.db_path()))
    return 0


def cmd_applied(a) -> int:
    from fitly.review.kpis import log_effort
    from fitly.tracking.tracker import prior_applications, set_status

    db = paths.db_path()
    prior = prior_applications(a.job_id, db_path=db)
    if prior and not a.force:
        print("STOP: an application was already sent for a very similar offer (one offer = one application):")
        for p in prior:
            print(f"  id {p['id']}: {p['title']} — {p['company']} ({p['status']}, sent {str(p['applied_at'])[:10]})")
        print("If this really is a different position, re-run with --force.")
        return 4
    _print(set_status(a.job_id, "submitted", note=a.note, when=a.date, cv=a.cv, db_path=db))
    log_effort(a.job_id, a.minutes, db_path=db)
    return 0


def cmd_followups(a) -> int:
    from fitly.tracking.tracker import due_followups, write_followup_drafts

    items = due_followups(today=a.date, db_path=paths.db_path())
    if not items:
        print("No follow-up due.")
        return 0
    for x in items:
        print(
            f"[id {x['job_id']}] {x['company']} — {x['title']} | sent {str(x['applied_at'])[:10]} | "
            f"due {str(x['next_follow_up_at'])[:10]} | follow-ups done: {x['follow_up_count']} | "
            f"{x['suggested_action']}"
        )
    if a.drafts:
        name = config.load("profile.json").get("name", "")
        for p in write_followup_drafts(items, sender_name=name):
            print("draft:", p)
    print("\nAfter sending a follow-up yourself: fitly set ID followed_up")
    print("No answer after the last follow-up: fitly set ID no_response")
    return 0


# --- views -----------------------------------------------------------------------------------------------
def cmd_status(a) -> int:
    from fitly.storage import connect

    con = connect(paths.db_path())
    try:
        rows = con.execute(
            "SELECT title, company, location, score, score_band, recommended_cv FROM jobs "
            "WHERE score >= ? ORDER BY score DESC",
            (a.min_score,),
        ).fetchall()
    finally:
        con.close()
    print(f"{len(rows)} jobs")
    for r in rows:
        print(
            f"[{r['score']:.0f}] {r['title']} — {r['company']} — {r['location']} — {r['score_band']}"
            f" — CV={r['recommended_cv']}"
        )
    return 0


def cmd_dashboard(a) -> int:
    from fitly.review import dashboard

    print(dashboard.render_text(db_path=paths.db_path()))
    return 0


def cmd_metrics(a) -> int:
    from fitly.review.kpis import RELEVANT, compute

    k = compute(paths.db_path())

    def pct(x):
        return "-" if x is None else f"{x * 100:.0f}%"

    print("FITLY FUNNEL")
    print(f"Jobs known:               {k['jobs_known']}")
    print(f"Relevant (score >= {RELEVANT}):   {k['relevant_offers']}   new this run: {k['new_offers']}")
    print(
        f"To review:                {k['to_review']}  (ATS {k['automatable_to_review']}, "
        f"Easy Apply {k['manual_easy_apply_to_review']}, other {k['other_route_to_review']})"
    )
    print(f"Automation rate:          {pct(k['automation_rate'])}")
    print(f"Submitted (ever):         {k['submitted']}")
    print(f"Response / interview:     {pct(k['response_rate'])} / {pct(k['interview_rate'])}")
    per_hour = k["interviews_per_hour"] if k["interviews_per_hour"] is not None else "-"
    print(f"Interviews per hour:      {per_hour}  ({k['hours_logged']} h logged)")
    print(f"Follow-ups due:           {k['followups_due']}")
    for s, v in sorted(k["statuses"].items()):
        print(f"  {s:<16} {v}")
    return 0


def cmd_pack(a) -> int:
    from fitly.apply import candidate_pack

    return candidate_pack.main([str(a.job_id)])


# --- sources -------------------------------------------------------------------------------------------
def _board(item):
    return item.get("board_token") or item.get("site_name") or item.get("board") or ",".join(item.get("keywords") or [])


def cmd_sources(a) -> int:
    for i, item in enumerate(config.load("ats_sources.json").get("sources", []), 1):
        print(f"{i:>2}. {item.get('type'):<16} {_board(item):<28} {item.get('company') or ''}")
    return 0


def add_source(entry, path=None) -> bool:
    """Append a source to the user's sources file (previous version kept), unless the same type+board is there.

    Without `path`, the shared default is copied to config/local/ on first use: added companies stay personal.
    """
    current = json.loads(path.read_text(encoding="utf-8")) if path else config.load("ats_sources.json")
    key = (entry["type"], _board(entry).lower())
    if any((x.get("type"), _board(x).lower()) == key for x in current.get("sources", [])):
        return False
    current.setdefault("sources", []).append(entry)
    if path:
        path.with_suffix(".json.bak").write_bytes(path.read_bytes())
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        config.save("ats_sources.json", current)
    return True


def cmd_probe(a) -> int:
    from fitly.sources.ats_clients import probe

    found = probe(a.company)
    if not found:
        print(f"No public Greenhouse/Lever/Ashby/SmartRecruiters/Workable/Recruitee board found for '{a.company}'.")
        print("Try another spelling (e.g. the slug in the company's careers URL) or import offers manually.")
        return 1
    for f in found:
        print(f"{f['type']:<16} {f['board']:<24} {f['jobs']} offers")
    if a.add:
        best = max(found, key=lambda f: f["jobs"])
        key = {"greenhouse": "board_token", "lever": "site_name"}.get(best["type"], "board")
        added = add_source({"type": best["type"], key: best["board"], "company": a.company})
        print(("Added " if added else "Already configured: ") + f"{best['type']}:{best['board']}")
    return 0


def cmd_check_sources(a) -> int:
    """Show which optional sources have their key, and (with --live) make one small real query each."""
    from fitly import user_env
    from fitly.sources import email_alerts
    from fitly.sources.collector_registry import collect_from_config

    print("Sources à clé (variables d'environnement) :")
    for kind, names in KEYED_SOURCES.items():
        missing = [n for n in names if not user_env.get(n)]
        if missing:
            print(f"  {kind:<15} ✗ clé absente : {', '.join(missing)}")
            continue
        if not a.live:
            print(f"  {kind:<15} ✓ clé présente (ajoute --live pour tester une vraie requête)")
            continue
        item = {"type": kind, "keywords": ["data engineer"], "pages": 1, "max_searches": 1}
        jobs, errors = collect_from_config({"sources": [item]})
        result = f"✓ {len(jobs)} offre(s) reçue(s)" if not errors else f"✗ erreur : {errors[0]['error'][:120]}"
        print(f"  {kind:<15} {result}")
    cfg = email_alerts.load_config()
    inbox = email_alerts.INBOX
    print("Alertes e-mail :")
    print(f"  dossier data\\inbox : {len(list(inbox.glob('*.eml'))) if inbox.exists() else 0} e-mail(s) en attente")
    if cfg.get("enabled"):
        password_env = cfg.get("password_env", "FITLY_IMAP_PASSWORD")
        state = "mot de passe présent" if user_env.get(password_env) else f"✗ variable {password_env} absente"
        print(f"  IMAP {cfg.get('user') or '(adresse manquante)'} : {state}")
    else:
        print('  IMAP : désactivé (email_alerts.json, "enabled": false)')
    return 0


# --- assistance ----------------------------------------------------------------------------------------
def cmd_answers(a) -> int:
    from fitly.apply import ai_answers
    from fitly.storage import connect

    con = connect(paths.db_path())
    try:
        row = con.execute("SELECT * FROM jobs WHERE id=?", (a.job_id,)).fetchone()
    finally:
        con.close()
    if not row:
        raise ValueError(f"Unknown job id: {a.job_id}")
    job = dict(row)
    lang = a.lang or ("en" if str(job.get("recommended_cv") or "").endswith("_en") else "fr")
    draft = ai_answers.generate(job, config.load("profile.json"), questions=a.question or [], language=lang)
    path = ai_answers.save(a.job_id, job, draft)
    cfg = ai_answers.load_config()
    print(f"{draft['source']}: {draft.get('note', '')}")
    print(f"AI budget this month: {ai_answers.spent_eur():.2f} / {float(cfg['monthly_cap_eur']):.2f} EUR")
    print(path)
    return 0


def cmd_doctor(a) -> int:
    """Settings files readable, database present, CV files declared in cv_profiles.json present."""
    ok = True
    print("FITLY DOCTOR")
    for name in REQUIRED_CONFIG:
        try:
            config.load(name)
            where = "personal" if config.path(name) == config.local_path(name) else "default"
            print(f"OK       {name} ({where})")
        except (OSError, ValueError) as exc:
            print(f"INVALID  {name}: {exc}")
            ok = False
    db = paths.project_path(paths.db_path())
    print(f"{'OK' if db.exists() else 'NEW':<8} {db}  (created at the first collection when missing)")
    for cv in config.load("cv_profiles.json", default={}).get("cvs", []):
        present = paths.project_path(cv["path"]).is_file()
        print(f"{'OK' if present else 'MISSING':<8} {cv['path']}  (CV {cv['id']})")
    print("RESULT:", "READY" if ok else "fix the files above first")
    return 0 if ok else 1


# --- parser ----------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    from fitly.tracking.tracker import STATUSES

    p = argparse.ArgumentParser(prog="fitly", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("morning", help="collect public offers, score and store them (read-only on the network)")
    r = sub.add_parser("review", help="import pending CSV decisions, then rebuild queue JSON + CSV + HTML report")
    r.add_argument("--comma", action="store_true", help="',' separated CSV (default ';' for French Excel)")
    r = sub.add_parser("rescore", help="recompute scores of stored offers offline (DB backed up first)")
    r.add_argument("--review", action="store_true", help="regenerate the review files afterwards")
    r.add_argument("--comma", action="store_true")
    r = sub.add_parser("decide", help="go / no-go for one offer")
    r.add_argument("job_id", type=int)
    r.add_argument("decision", help="go|oui|no|non")
    r.add_argument("--note")
    r = sub.add_parser("decisions", help="import the 'decision' column of the review CSV")
    r.add_argument("--csv", default=None, help="default: data/review_queue.csv")
    r.add_argument("--comma", action="store_true")
    r = sub.add_parser("undo", help="cancel a decision or a mistakenly recorded application (offer back to triage)")
    r.add_argument("job_id", type=int)
    r = sub.add_parser("set", help="set an application status")
    r.add_argument("job_id", type=int)
    r.add_argument("status", choices=STATUSES)
    r.add_argument("--note")
    r.add_argument("--date", help="YYYY-MM-DD or DD/MM/YYYY")
    r.add_argument("--cv")
    r = sub.add_parser("applied", help="record a submitted application (you clicked Submit)")
    r.add_argument("job_id", type=int)
    r.add_argument("--date", help="YYYY-MM-DD or DD/MM/YYYY (default: today)")
    r.add_argument("--cv", help="CV actually sent, e.g. tech_fr")
    r.add_argument("--note")
    r.add_argument("--minutes", type=float, help="time spent on this application (feeds interviews/hour)")
    r.add_argument("--force", action="store_true", help="apply even if a similar application was already sent")
    r = sub.add_parser("followups", help="follow-ups due")
    r.add_argument("--drafts", action="store_true", help="write follow-up templates to data/followups/")
    r.add_argument("--date", help="evaluate as of this date")
    r = sub.add_parser("status", help="stored offers, best first")
    r.add_argument("--min-score", type=float, default=0)
    sub.add_parser("dashboard", help="best offers and follow-ups due, as text")
    sub.add_parser("metrics", help="funnel indicators: coverage, automation, responses, interviews per hour")
    r = sub.add_parser("pack", help="candidate pack for one offer")
    r.add_argument("job_id", type=int)
    sub.add_parser("sources", help="list configured job boards")
    r = sub.add_parser(
        "check-sources",
        help="check API keys of optional sources (Google Jobs, Adzuna, Jooble, France Travail) and e-mail alerts",
    )
    r.add_argument("--live", action="store_true", help="make one small real query per configured source")
    r = sub.add_parser("answers", help="draft application answers (AI, capped budget; template without API key)")
    r.add_argument("job_id", type=int)
    r.add_argument("--question", "-q", action="append", help="screening question (repeatable)")
    r.add_argument("--lang", choices=["fr", "en"])
    r = sub.add_parser("probe", help="find a company's public job board")
    r.add_argument("company")
    r.add_argument("--add", action="store_true", help="add the largest board found to your sources")
    sub.add_parser("doctor", help="check settings files, database and CV files")
    return p


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # emoji titles on cp1252 pipes
    a = build_parser().parse_args(argv)
    try:
        return globals()[f"cmd_{a.cmd.replace('-', '_')}"](a)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
