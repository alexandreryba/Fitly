"""Per-company rule: up to 5 offers of the same job, at most 3 different jobs; NO-GO lets the next one move up."""

import json
from pathlib import Path

from fitly.review import review_queue
from fitly.scoring import company_quota
from fitly.scoring.pipeline import run_pipeline
from fitly.storage import connect, upsert_jobs
from fitly.tracking import tracker

ROOT = Path(__file__).resolve().parents[1]


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


def seed(tmp_path, titles, company="Acme"):
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "lever",
            "external_id": f"{company}{i}",
            "title": t,
            "company": company,
            "location": "Paris",
            "description": "Python SQL BigQuery dbt",
        }
        for i, t in enumerate(titles)
    ]
    db = str(tmp_path / "q.db")
    upsert_jobs(run_pipeline(jobs, prefs, profile, cvs), db)
    con = connect(db)
    ids = {r["title"]: r["id"] for r in con.execute("SELECT id, title FROM jobs WHERE company=?", (company,))}
    con.close()
    return db, ids


def split(db, company="Acme"):
    q = [r for r in review_queue.build(db) if r["application_status"] == "not_started" and r["company"] == company]
    return ({r["title"] for r in q if r["quota"] == "visible"}, {r["title"] for r in q if r["quota"] == "reserve"})


def rules(monkeypatch, offers=5, roles=3, window=90):
    monkeypatch.setattr(
        company_quota,
        "rules",
        lambda path=None: {
            "max_offers_per_company": offers,
            "max_distinct_roles_per_company": roles,
            "company_window_days": window,
        },
    )


VARIANTS = [
    "Data Engineer",
    "Senior Data Engineer",
    "Data Engineer - Secteur Retail",
    "Big Data Engineer",
    "Data Engineer GCP (H/F)",
    "Data Engineer Lyon",
]


def test_variants_of_one_job_count_as_one_role_up_to_five_offers(tmp_path, monkeypatch):
    rules(monkeypatch)
    db, _ = seed(tmp_path, VARIANTS)
    assert {company_quota.role_core(t) for t in VARIANTS} == {"data engineer"}
    visible, reserve = split(db)
    assert len(visible) == 5 and len(reserve) == 1


def test_at_most_three_different_jobs(tmp_path, monkeypatch):
    rules(monkeypatch)
    titles = ["Data Engineer", "Analytics Engineer", "Data Architect", "Data Scientist", "Product Owner Data"]
    db, _ = seed(tmp_path, titles)
    visible, reserve = split(db)
    assert len(visible) == 3 and len(reserve) == 2
    assert "Data Engineer" in visible and "Product Owner Data" in reserve
    q = next(r for r in review_queue.build(db) if r["title"] == "Product Owner Data")
    assert "métiers différents" in q["quota_reason"]


def test_mixed_variants_and_roles(tmp_path, monkeypatch):
    rules(monkeypatch)
    titles = [
        "Data Engineer",
        "Senior Data Engineer",
        "Data Engineer Retail",
        "Analytics Engineer",
        "Data Architect",
        "Data Scientist",
    ]
    db, _ = seed(tmp_path, titles)
    visible, reserve = split(db)
    assert len(visible) == 5  # 3 DE variants + 2 other jobs = 3 roles, 5 offers
    assert reserve == {"Data Scientist"} or len(reserve) == 1


def test_go_takes_a_slot_nogo_lets_the_next_offer_move_up(tmp_path, monkeypatch):
    rules(monkeypatch, offers=5, roles=2)
    titles = ["Data Engineer", "Analytics Engineer", "Data Architect", "Data Scientist"]
    db, ids = seed(tmp_path, titles)
    visible, reserve = split(db)
    assert len(visible) == 2 and len(reserve) == 2
    gone = sorted(visible)[0]
    tracker.decide(ids[gone], "no", db_path=db)  # NO-GO: takes no slot
    visible2, reserve2 = split(db)
    assert len(visible2) == 2 and len(reserve2) == 1 and (visible2 - visible)
    tracker.decide(ids[sorted(visible2)[0]], "go", db_path=db)  # GO: occupies a role slot
    visible3, _ = split(db)
    assert len(visible3) == 1


def test_old_closed_application_frees_its_slot(tmp_path, monkeypatch):
    rules(monkeypatch, offers=1, roles=1)
    db, ids = seed(tmp_path, ["Data Engineer", "Analytics Engineer"])
    tracker.set_status(ids["Data Engineer"], "rejected", when="2020-01-01", db_path=db)
    visible, _ = split(db)
    assert len(visible) == 1  # a 2020 refusal no longer counts
    tracker.set_status(ids["Data Engineer"], "submitted", db_path=db)
    visible, _ = split(db)
    assert len(visible) == 0


def test_family_and_coherence_warning(tmp_path, monkeypatch):
    rules(monkeypatch)
    assert company_quota.family("Ingénieur Data (H/F)") == "Ingénierie data / IA"
    assert company_quota.family("Engineering Manager - Data") == "Management"
    db, ids = seed(tmp_path, ["Data Engineer", "Data Architect", "Product Owner Data"])
    tracker.set_status(ids["Data Engineer"], "submitted", db_path=db)
    assert company_quota.coherence_warning(ids["Data Architect"], db) is None
    warn = company_quota.coherence_warning(ids["Product Owner Data"], db)
    assert warn and "Data Engineer" in warn and "Produit" in warn
