import csv
import json
from pathlib import Path

import pytest

from fitly.review import export_review, report_html, review_queue
from fitly.scoring.pipeline import run_pipeline
from fitly.storage import connect, upsert_jobs
from fitly.tracking import tracker

ROOT = Path(__file__).resolve().parents[1]


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


@pytest.fixture
def db(tmp_path):
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "lever",
            "external_id": "a",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "Python SQL BigQuery dbt",
            "url": "https://x/a",
            "application_route": "external_ats",
        },
        {
            "source": "lever",
            "external_id": "b",
            "title": "Senior Data Engineer",
            "company": "Beta",
            "location": "Paris",
            "description": "Python SQL",
            "url": "https://x/b",
            "application_route": "external_ats",
        },
        {
            "source": "lever",
            "external_id": "c",
            "title": "Stage Data Analyst",
            "company": "Gamma",
            "location": "Lyon",
            "description": "Excel",
            "url": "https://x/c",
            "application_route": "external_ats",
        },
    ]
    path = tmp_path / "t.db"
    upsert_jobs(run_pipeline(jobs, prefs, profile, cvs), str(path))
    return str(path)


def ids(db):
    con = connect(db)
    out = {r["company"]: r["id"] for r in con.execute("SELECT id, company FROM jobs")}
    con.close()
    return out


def test_score_details_are_stored_and_explained(db):
    queue = review_queue.build(db)
    top = queue[0]
    assert top["company"] == "Acme"
    assert top["matched_role"] == "Data Engineer"
    assert top["why"].startswith("Data Engineer | role +47")
    senior = next(r for r in queue if r["company"] == "Beta")
    assert "niveau -20" in senior["why"]


def test_go_no_go_and_queue_filtering(db):
    j = ids(db)
    assert tracker.decide(j["Acme"], "oui", db_path=db)["status"] == "go"
    assert tracker.decide(j["Gamma"], "non", reason="stage", db_path=db)["status"] == "skipped"
    queue = {r["company"]: r for r in review_queue.build(db)}
    assert "Gamma" not in queue
    assert queue["Acme"]["human_action"] == "prepare_application"
    with pytest.raises(ValueError):
        tracker.decide(j["Beta"], "maybe", db_path=db)


def test_applied_schedules_follow_up_and_cycle(db, tmp_path):
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"follow_up_days": 7, "max_follow_ups": 1}), encoding="utf-8")
    j = ids(db)["Acme"]
    r = tracker.set_status(j, "submitted", when="2026-10-01", db_path=db, rules_path=rules)
    assert r["applied_at"].startswith("2026-10-01")
    assert r["next_follow_up_at"].startswith("2026-10-08")
    assert r["cv"] == "tech_fr"
    assert tracker.due_followups(today="2026-10-07", db_path=db, rules_path=rules) == []
    due = tracker.due_followups(today="2026-10-08", db_path=db, rules_path=rules)
    assert [d["suggested_action"] for d in due] == ["follow_up_1"]
    tracker.set_status(j, "followed_up", when="2026-10-08", db_path=db, rules_path=rules)
    due = tracker.due_followups(today="2026-10-15", db_path=db, rules_path=rules)
    assert due[0]["suggested_action"] == "close_as_no_response"
    assert due[0]["applied_at"].startswith("2026-10-01")  # original send date kept
    tracker.set_status(j, "no_response", db_path=db, rules_path=rules)
    assert tracker.due_followups(today="2027-01-01", db_path=db, rules_path=rules) == []


def test_follow_up_draft_language_and_no_send(db, tmp_path):
    j = ids(db)["Acme"]
    tracker.set_status(j, "submitted", when="2026-10-01", cv="tech_en", db_path=db)
    items = tracker.due_followups(today="2026-12-01", db_path=db)
    paths = tracker.write_followup_drafts(items, out_dir=tmp_path / "fu", sender_name="Jane Martin")
    text = paths[0].read_text(encoding="utf-8")
    assert "Following up on my application" in text and "Jane Martin" in text


def test_csv_decisions_round_trip_semicolon_and_protection(db, tmp_path):
    j = ids(db)
    tracker.set_status(j["Beta"], "submitted", db_path=db)
    qjson, qcsv = tmp_path / "q.json", tmp_path / "q.csv"
    review_queue.main(db=db, out=qjson)
    export_review.main(inp=qjson, out=qcsv, delimiter=";")
    rows = list(csv.DictReader(qcsv.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    for row in rows:
        row["decision"] = {"Acme": "GO", "Beta": "non", "Gamma": "n"}[row["company"]]
    with qcsv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    result = tracker.import_decisions(qcsv, db_path=db)
    assert (result["go"], result["skipped"], result["protected"]) == (1, 1, 1)
    con = connect(db)
    statuses = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    con.close()
    assert statuses[j["Beta"]] == "submitted"  # already sent: CSV no-go must not overwrite it
    assert statuses[j["Acme"]] == "go" and statuses[j["Gamma"]] == "skipped"


def test_rescore_updates_in_place_without_inserting(db, tmp_path, monkeypatch):
    con = connect(db)
    con.execute("UPDATE jobs SET score=100, score_band='apply_now', score_details=NULL")
    con.commit()
    con.close()
    result = tracker.rescore(*configs(), db_path=db, backup=True)
    assert result["jobs"] == 3 and result["score_changed"] == 3
    assert Path(result["backup"]).exists()
    con = connect(db)
    rows = con.execute("SELECT company, score, score_details FROM jobs ORDER BY score DESC").fetchall()
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    con.close()
    assert rows[0]["company"] == "Acme" and rows[0]["score"] < 100 and rows[0]["score_details"]


def test_html_report_lists_due_follow_ups(db, tmp_path):
    tracker.set_status(ids(db)["Acme"], "submitted", when="2020-01-01", db_path=db)
    out = tmp_path / "r.html"
    report_html.main(db=db, out=out)
    html = out.read_text(encoding="utf-8")
    assert "Acme" in html and "follow_up_1" in html


def test_migration_adds_column_to_legacy_database(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_key TEXT UNIQUE NOT NULL, external_id TEXT, source TEXT, title TEXT, company TEXT, location TEXT, description TEXT, url TEXT, score REAL, score_band TEXT, recommended_cv TEXT, application_route TEXT, first_seen_at TEXT, last_seen_at TEXT)"
    )
    con.execute(
        "INSERT INTO jobs(canonical_key, source, external_id, title, company, location) VALUES('lever:1','lever','1','Data Engineer','Acme','Paris')"
    )
    con.commit()
    con.close()
    c = connect(str(path))
    cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
    assert "score_details" in cols
    assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    c.close()


def test_cli_parses_every_command():
    from fitly import cli as fitly

    with pytest.raises(SystemExit) as exc:
        fitly.main(["--help"])
    assert exc.value.code == 0


def test_cli_unknown_job_id_is_reported_not_raised(tmp_path, monkeypatch, db):
    from fitly import cli as fitly

    monkeypatch.setenv("FITLY_DB", str(db))
    assert fitly.main(["decide", "999999", "go"]) == 2
    assert fitly.main(["decide", str(ids(db)["Acme"]), "go"]) == 0
