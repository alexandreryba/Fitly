"""Failure modes of daily use: Excel-edited/locked CSV, network outage, partial collection, backups."""

import csv
import json
import pathlib
from pathlib import Path

from fitly import cli as fitly
from fitly import morning as morning_run
from fitly.review import export_review
from fitly.scoring.pipeline import run_pipeline
from fitly.storage import connect, upsert_jobs
from fitly.tracking import tracker

ROOT = Path(__file__).resolve().parents[1]


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


def seed(db, n=3):
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "lever",
            "external_id": str(i),
            "title": t,
            "company": f"Co{i}",
            "location": "Paris",
            "description": "Python SQL",
            "url": f"https://x/{i}",
            "application_route": "external_ats",
        }
        for i, t in enumerate(["Data Engineer", "Analytics Engineer", "Data Analyst"][:n])
    ]
    upsert_jobs(run_pipeline(jobs, prefs, profile, cvs), str(db))
    return jobs


def statuses(db):
    con = connect(str(db))
    out = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    con.close()
    return out


def test_review_imports_pending_csv_decisions_before_regenerating(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    seed(db)
    monkeypatch.setenv("FITLY_DB", str(db))
    assert fitly.main(["review"]) == 0
    path = tmp_path / "review_queue.csv"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    assert rows and "decision" in rows[0]
    rows[0]["decision"] = "oui"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    # User forgot `decisions`: the next review/morning must not lose the go.
    assert fitly.main(["review"]) == 0
    assert statuses(db) == {int(rows[0]["id"]): "go"}
    assert not tracker.pending_decisions(path)  # regenerated CSV is clean


def test_decisions_import_accepts_excel_ansi_file(tmp_path):
    db = tmp_path / "j.db"
    seed(db, 1)
    path = tmp_path / "excel.csv"
    path.write_bytes("id;decision;decision_note;title\n1;non;poste trop éloigné;Data Engineer\n".encode("cp1252"))
    assert tracker.import_decisions(path, db_path=str(db))["skipped"] == 1
    con = connect(str(db))
    assert "poste trop éloigné" in con.execute("SELECT notes FROM applications").fetchone()[0]
    con.close()


def test_csv_export_falls_back_when_file_is_locked(tmp_path, monkeypatch):
    inp = tmp_path / "q.json"
    inp.write_text(json.dumps([{"id": 1, "title": "Data Engineer"}]), encoding="utf-8")
    out = tmp_path / "review_queue.csv"
    real_open = pathlib.Path.open

    def locked(self, mode="r", *args, **kwargs):
        if self == out and "w" in mode:
            raise PermissionError("locked by Excel")
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", locked)
    assert export_review.main(inp=inp, out=out) == 0
    alt = list(tmp_path.glob("review_queue_*.csv"))
    assert len(alt) == 1 and "Data Engineer" in alt[0].read_text(encoding="utf-8-sig")


def _run_morning(tmp_path, monkeypatch, collected, errors):
    cfg = tmp_path / "cfg.json"
    cfg.write_text('{"sources": []}', encoding="utf-8")
    monkeypatch.setattr(morning_run, "collect_from_config", lambda _cfg, **_kw: (collected, errors))
    monkeypatch.setattr(morning_run.email_alerts, "collect", lambda: ([], []))
    from fitly.sources import employer_match

    monkeypatch.setattr(employer_match, "check", lambda **_kw: {})  # offline: no probe of employer sites
    argv = ["--config", str(cfg), "--db", str(tmp_path / "j.db")]
    argv += ["--output", str(tmp_path / "out.json"), "--previous", str(tmp_path / "prev.json")]
    return morning_run.main(argv)


def test_total_network_failure_changes_nothing(tmp_path, monkeypatch):
    jobs = seed(tmp_path / "j.db")
    (tmp_path / "prev.json").write_text(json.dumps(jobs), encoding="utf-8")
    before = (tmp_path / "j.db").read_bytes(), (tmp_path / "prev.json").read_text(encoding="utf-8")
    code = _run_morning(tmp_path, monkeypatch, [], [{"source": "lever", "target": "x", "error": "timeout"}])
    assert code == 3
    assert ((tmp_path / "j.db").read_bytes(), (tmp_path / "prev.json").read_text(encoding="utf-8")) == before


def test_partial_collection_keeps_history_and_backs_up(tmp_path, monkeypatch):
    jobs = seed(tmp_path / "j.db")
    (tmp_path / "prev.json").write_text(json.dumps(jobs), encoding="utf-8")
    # Day 1: one board fails, only job 0 collected.
    assert _run_morning(tmp_path, monkeypatch, jobs[:1], [{"source": "lever", "target": "y", "error": "500"}]) == 0
    assert list((tmp_path / "backups").glob("fitly_before_morning_*.db"))  # copies are backed up next to themselves
    assert len(json.loads((tmp_path / "prev.json").read_text(encoding="utf-8"))) == 3
    # Day 2: board is back: the other offers must be 'known', not 'new'.
    assert _run_morning(tmp_path, monkeypatch, jobs, []) == 0
    summary = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["summary"]
    assert (summary["new"], summary["known"]) == (0, 3)
    con = connect(str(tmp_path / "j.db"))
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
    con.close()


def test_backup_rotation_keeps_latest_copies(tmp_path, monkeypatch):
    db = tmp_path / "j.db"
    seed(db, 1)
    names = iter(f"2026010{i}T000000" for i in range(1, 6))

    class FakeNow:
        def strftime(self, _fmt):
            return next(names)

    monkeypatch.setattr(tracker, "_now", lambda: FakeNow())
    for _ in range(5):
        tracker.backup_db(str(db), "t", keep=3)
    kept = sorted(p.name for p in (tmp_path / "backups").glob("fitly_t_*.db"))
    assert kept == ["fitly_t_20260103T000000.db", "fitly_t_20260104T000000.db", "fitly_t_20260105T000000.db"]
