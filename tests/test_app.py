"""Headless tests of the Streamlit UI (streamlit.testing) against a temporary database."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


st_testing = pytest.importorskip("streamlit.testing.v1")
from fitly.scoring.pipeline import run_pipeline
from fitly.storage import connect, upsert_jobs
from fitly.tracking import tracker


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "lever",
            "external_id": "1",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "Python SQL BigQuery dbt Airflow",
            "url": "https://x/1",
            "application_route": "external_ats",
        },
        {
            "source": "lever",
            "external_id": "2",
            "title": "Analytics Engineer",
            "company": "Beta",
            "location": "Paris",
            "description": "SQL dbt",
            "url": "https://x/2",
            "application_route": "external_ats",
        },
        {
            "source": "linkedin",
            "external_id": "3",
            "title": "Data Architect",
            "company": "Gamma",
            "location": "Paris",
            "description": "GCP",
            "url": "https://www.linkedin.com/jobs/view/3",
            "application_route": "linkedin_easy_apply",
        },
        {
            "source": "greenhouse",
            "external_id": "4",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris, France",
            "description": "Python SQL BigQuery data pipelines platform",
            "url": "https://x/4",
            "application_route": "external_ats",
        },
    ]
    db = tmp_path / "app.db"
    upsert_jobs(run_pipeline(jobs[:3], prefs, profile, cvs), str(db))
    monkeypatch.setenv("FITLY_DB", str(db))
    monkeypatch.setenv("FITLY_NO_CAPTURE", "1")  # do not bind the extension port during tests
    return db


def ids(db):
    con = connect(str(db))
    out = {(r["company"], r["title"]): r["id"] for r in con.execute("SELECT id, company, title FROM jobs")}
    con.close()
    return out


def run_app():
    at = st_testing.AppTest.from_file(str(ROOT / "src" / "fitly" / "ui" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_dashboard_renders_brief_indicators(app_db):
    at = run_app()
    assert metric(at, "Offres pertinentes") == "3"
    assert metric(at, "Via ATS (à trier)") == "2"
    assert metric(at, "Easy Apply manuels") == "1"
    assert metric(at, "Relances dues") == "0"
    assert len(at.tabs) == 7


def test_full_application_cycle_through_the_ui(app_db):
    jid = ids(app_db)[("Acme", "Data Engineer")]
    tracker.decide(jid, "go", db_path=str(app_db))
    at = run_app()
    form_submit = next(b for b in at.button if b.label == "✅ Enregistrer l'envoi")
    form_submit.click().run()
    assert not at.exception
    con = connect(str(app_db))
    app_row = con.execute("SELECT status, cv_type FROM applications WHERE job_id=?", (jid,)).fetchone()
    effort = con.execute("SELECT details FROM events WHERE job_id=? AND event_type='effort'", (jid,)).fetchone()
    con.close()
    assert app_row["status"] == "submitted" and app_row["cv_type"] == "tech_fr"
    assert float(effort["details"]) == 20.0
    # follow-up becomes due after 7 days -> visible in the Suivi tab
    tracker.set_status(jid, "submitted", when="2020-01-01", db_path=str(app_db))
    at = run_app()
    assert metric(at, "Relances dues") == "1"
    next(b for b in at.button if b.label == "Relance envoyée").click().run()
    assert tracker.due_followups(db_path=str(app_db)) == []


def test_ui_blocks_duplicate_application_without_explicit_override(app_db):
    prefs, profile, cvs = configs()
    first = ids(app_db)[("Acme", "Data Engineer")]
    tracker.set_status(first, "submitted", db_path=str(app_db))
    dup = {
        "source": "greenhouse",
        "external_id": "9",
        "title": "Data Engineer",
        "company": "Acme",
        "location": "Lyon",
        "description": "x",
        "url": "https://x/9",
        "application_route": "external_ats",
    }
    upsert_jobs(run_pipeline([dup], prefs, profile, cvs), str(app_db))
    second = max(ids(app_db).values())  # the newly inserted Lyon duplicate
    tracker.decide(second, "go", db_path=str(app_db))
    at = run_app()
    assert any("Candidature déjà envoyée" in e.value for e in at.error)
    next(b for b in at.button if b.label == "✅ Enregistrer l'envoi").click().run()
    con = connect(str(app_db))
    status = con.execute("SELECT status FROM applications WHERE job_id=?", (second,)).fetchone()["status"]
    con.close()
    assert status == "go"  # not recorded as sent


def test_go_can_be_undone_and_nogo_tab_lists_rejections(app_db):
    jid = ids(app_db)[("Acme", "Data Engineer")]
    other = ids(app_db)[("Beta", "Analytics Engineer")]
    tracker.decide(jid, "go", db_path=str(app_db))
    tracker.decide(other, "no", reason="trop loin", db_path=str(app_db))
    at = run_app()
    assert metric(at, "GO / NO-GO") == "1 / 1"
    next(b for b in at.button if b.label == "↩️ Remettre à trier").click().run()
    assert not at.exception
    con = connect(str(app_db))
    remaining = {r["job_id"]: r["status"] for r in con.execute("SELECT job_id, status FROM applications")}
    con.close()
    assert remaining == {other: "skipped"}


def test_mistaken_submission_can_be_undone_and_leaves_statistics(app_db):
    jid = ids(app_db)[("Acme", "Data Engineer")]
    tracker.set_status(jid, "submitted", db_path=str(app_db))
    at = run_app()
    at.checkbox(key="undo_sure").check().run()
    next(b for b in at.button if b.label == "Annuler cet enregistrement").click().run()
    assert not at.exception
    con = connect(str(app_db))
    assert con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 0
    assert (
        con.execute("SELECT COUNT(*) FROM events WHERE event_type LIKE 'status:%' OR event_type='effort'").fetchone()[0]
        == 0
    )
    con.close()


def test_dashboard_shows_tech_business_and_language_split(app_db):
    at = run_app()
    assert metric(at, "Tech à trier") == "3"
    assert int(metric(at, "FR à trier")) + int(metric(at, "EN à trier")) == 3


def test_app_renders_with_reserve_and_nogo_rows(app_db, monkeypatch, tmp_path):
    """Regression: Offres and Réserve both hold NO-GO buttons (duplicate widget id crashed the page)."""
    rules = tmp_path / "rules.json"
    rules.write_text('{"max_offers_per_company": 1, "company_window_days": 90}', encoding="utf-8")
    from fitly.scoring import company_quota

    monkeypatch.setattr(
        company_quota,
        "rules",
        lambda path=None: {"max_offers_per_company": 1, "max_distinct_roles_per_company": 1, "company_window_days": 90},
    )
    prefs, profile, cvs = configs()
    extra = [
        {
            "source": "lever",
            "external_id": f"e{i}",
            "title": t,
            "company": "Acme",
            "location": "Paris",
            "description": "Python SQL BigQuery",
            "url": f"https://x/e{i}",
            "application_route": "external_ats",
        }
        for i, t in enumerate(["Analytics Engineer", "Data Architect"])
    ]
    upsert_jobs(run_pipeline(extra, prefs, profile, cvs), str(app_db))
    tracker.decide(ids(app_db)[("Beta", "Analytics Engineer")], "no", db_path=str(app_db))
    at = run_app()
    assert int(metric(at, "En réserve")) >= 1
    assert metric(at, "GO / NO-GO") == "0 / 1"


def test_source_filter_lists_origins(app_db):
    at = run_app()
    src = next(m for m in at.multiselect if m.label.startswith("Source"))
    assert "Lever (site carrière)" in src.options and "LinkedIn (alerte e-mail)" in src.options
    src.set_value(["LinkedIn (alerte e-mail)"]).run()
    assert not at.exception
    table = next(d.value for d in at.dataframe if "source" in d.value.columns)
    assert set(table["source"]) == {"LinkedIn (alerte e-mail)"}


class _FakePopen:
    calls = []

    def __init__(self, args, **kw):
        _FakePopen.calls.append(args)


def _seed_fresh(app_db):
    from datetime import date

    prefs, profile, cvs = configs()
    fresh = [
        {
            "source": "lever",
            "external_id": f"f{i}",
            "title": t,
            "company": c,
            "location": "Paris",
            "description": "Python SQL BigQuery GCP dbt Airflow data pipelines Spark",
            "url": f"https://x/f{i}",
            "application_route": "external_ats",
            "posted_at": date.today().isoformat(),
        }
        for i, (t, c) in enumerate([("Junior Data Engineer", "Delta"), ("Data Engineer", "Epsilon")])
    ]
    upsert_jobs(run_pipeline(fresh, prefs, profile, cvs), str(app_db))
    return {r: i for r, i in ids(app_db).items() if r[0] in ("Delta", "Epsilon")}


def test_daily_selection_goes_and_launches_prefill(app_db, monkeypatch):
    import subprocess

    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    fresh = _seed_fresh(app_db)
    at = run_app()
    btn = next(b for b in at.button if b.label.startswith("🚀 GO + pré-remplir"))
    btn.click().run()
    assert not at.exception
    assert statuses_of(app_db, fresh.values()) == {"go"}
    args = _FakePopen.calls[-1]
    launched = {u.split("#fitly-fill=")[1] for u in args if "#fitly-fill=" in u}  # new Chrome tabs
    assert {str(i) for i in fresh.values()} <= launched  # fresh high-score offers are proposed
    assert statuses_of(app_db, [int(i) for i in launched]) == {"go"}  # everything launched went to GO
    assert str(args[0]).lower().endswith(("chrome.exe", "msedge.exe"))
    assert (app_db.parent / "prefill" / "pending.json").exists()  # options for the extension, next to the test DB


def test_candidatures_prefill_button_and_bulk_sent(app_db, monkeypatch):
    import subprocess

    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    jid = ids(app_db)[("Acme", "Data Engineer")]
    tracker.decide(jid, "go", db_path=str(app_db))
    at = run_app()
    next(b for b in at.button if b.label.startswith("🚀 Ouvrir et pré-remplir (")).click().run()
    assert any(u.endswith(f"#fitly-fill={jid}") for u in _FakePopen.calls[-1])
    at.multiselect(key="sent_pick").set_value(list(at.multiselect(key="sent_pick").options)).run()
    next(b for b in at.button if b.label.startswith("Enregistrer (")).click().run()
    assert not at.exception
    assert statuses_of(app_db, [jid]) == {"submitted"}


def statuses_of(db, job_ids):
    con = connect(str(db))
    out = {
        r["status"]
        for r in con.execute(
            f"SELECT status FROM applications WHERE job_id IN ({','.join('?' * len(list(job_ids)))})", list(job_ids)
        )
    }
    con.close()
    return out


def test_prefill_test_series_opens_tabs_without_go(app_db, monkeypatch):
    import subprocess

    from fitly.sources import employer_match

    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(employer_match, "page_closed", lambda url, timeout=12: False)
    prefs, profile, cvs = configs()
    upsert_jobs(
        run_pipeline(
            [
                {
                    "source": "lever",
                    "external_id": f"t{i}",
                    "title": "Data Engineer",
                    "company": c,
                    "location": "Paris",
                    "description": "Python SQL BigQuery dbt",
                    "url": f"https://jobs.lever.co/{c.lower()}/{i}",
                    "application_route": "external_ats",
                }
                for i, c in enumerate(["Zeta", "Kappa"])
            ],
            prefs,
            profile,
            cvs,
        ),
        str(app_db),
    )
    at = run_app()
    next(b for b in at.button if b.label.startswith("🧪 Ouvrir les offres 1 à")).click().run()
    assert not at.exception
    opened = [u for u in _FakePopen.calls[-1] if "#fitly-fill=" in u]
    assert opened and statuses_of(app_db, [int(u.split("=")[-1]) for u in opened]) == set()  # nothing went to GO


def test_mail_proposals_are_applied_only_after_confirmation(app_db):
    import json as _json

    jid = ids(app_db)[("Acme", "Data Engineer")]
    path = app_db.parent / "mail" / "proposals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _json.dumps(
            [
                {
                    "message_id": "1",
                    "date": "2026-10-01",
                    "status": "interview",
                    "job_id": jid,
                    "company": "Acme",
                    "title": "Data Engineer",
                    "current": None,
                    "subject": "Entretien",
                    "from": "x",
                }
            ]
        ),
        encoding="utf-8",
    )
    at = run_app()
    assert statuses_of(app_db, [jid]) == set()
    next(b for b in at.button if b.label.startswith("✅ Appliquer la sélection")).click().run()
    assert not at.exception
    assert statuses_of(app_db, [jid]) == {"interview"} and not path.exists()


def test_daily_selection_by_family_go_with_one_cv(app_db, monkeypatch):
    import subprocess

    _FakePopen.calls = []
    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    fresh = _seed_fresh(app_db)  # Junior Data Engineer + Data Engineer
    at = run_app()
    labels = [e.label for e in at.expander]
    assert any(x.startswith("🛠️ Data Engineer —") for x in labels)
    idx = next(
        i
        for i, x in enumerate(
            y for y in labels if y.startswith(("🤖", "🛠️", "🧠", "🔬", "🏛️", "📐", "📊", "💼", "🧩", "📋", "🗂️"))
        )
        if x.startswith("🛠️ Data Engineer")
    )
    at.selectbox(key=f"cat_cv_{idx}").set_value("tech_en").run()
    at.button(key=f"cat_batch_{idx}").click().run()
    assert not at.exception
    from fitly.storage import connect

    con = connect(str(app_db))
    got = {
        r["job_id"]: (r["status"], r["cv_type"])
        for r in con.execute("SELECT job_id, status, cv_type FROM applications")
    }
    con.close()
    for jid in fresh.values():
        assert got[jid] == ("go", "tech_en")  # whole family in GO, with the CV chosen for the family
    import os

    from fitly.apply import launch_review

    os.environ["FITLY_DB"] = str(app_db)
    assert launch_review.job_targets([next(iter(fresh.values()))])[0]["recommended_cv"] == "tech_en"


def test_offers_grouped_by_company_and_batches_never_split_a_company():

    # app.py is a Streamlit script: test its pure helpers through a tiny namespace instead of importing it.
    src = (ROOT / "src" / "fitly" / "ui" / "app.py").read_text(encoding="utf-8")
    ns = {}
    for name in ("by_company", "first_batch"):
        start = src.index(f"def {name}(")
        end = src.index("\n\n\ndef ", start)
        exec(src[start:end], ns)
    rows = [
        {"company": c, "score": s}
        for c, s in [
            ("Umbrella", 70),
            ("Stark", 95),
            ("Umbrella", 90),
            ("Wayne", 80),
            ("Stark", 90),
            ("Umbrella", 85),
            ("Stark", 85),
            ("Solo", 96),
            ("Solo", 60),
            ("Top", 98),
        ]
    ]
    ordered = ns["by_company"](rows)
    # Top 98 | Stark (95+90)/2=92.5 | Umbrella (90+81.7)/2=85.8 | Solo (96+78)/2=87 | Wayne 80
    assert [r["company"] for r in ordered] == [
        "Top",
        "Stark",
        "Stark",
        "Stark",
        "Solo",
        "Solo",
        "Umbrella",
        "Umbrella",
        "Umbrella",
        "Wayne",
    ]
    assert [r["score"] for r in ordered][:4] == [98, 95, 90, 85]
    assert [r["company"] for r in ns["first_batch"](ordered, 3)] == ["Top", "Stark", "Stark", "Stark"]
    assert len(ns["first_batch"](ordered, 1)) == 1 and len(ns["first_batch"](ordered, 2)) == 4
    assert ns["first_batch"]([], 10) == []
