import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fitly.apply import candidate_pack
from fitly.review import review_queue
from fitly.scoring.pipeline import _required_years, run_pipeline
from fitly.storage import connect, upsert_jobs

ROOT = Path(__file__).resolve().parents[1]


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


def score(title, location="Paris", description="Python SQL GCP"):
    prefs, profile, cvs = configs()
    job = {
        "source": "fx",
        "external_id": title,
        "title": title,
        "company": "Co",
        "location": location,
        "description": description,
    }
    return run_pipeline([job], prefs, profile, cvs)[0]


def test_french_and_contextual_titles_reach_target_roles():
    assert (
        score("Architecte Solutions Data & IA – Practice IA")["match_explanation"]["matched_role"]
        == "Data Solution Architect"
    )
    assert score("Lead IA Engineer")["match_explanation"]["matched_role"] == "AI Engineer"
    assert score("AI Product Manager (H/F)")["match_explanation"]["matched_role"] == "Product Manager Data"
    assert score("Architecte Solutions Data & IA")["score"] >= 70


def test_product_manager_is_a_job_family_not_a_seniority_level():
    pm = score("Product Manager Data")["match_explanation"]["score_breakdown"]
    em = score("Engineering Manager Data")["match_explanation"]["score_breakdown"]
    kam = score("Key Account Manager Data")["match_explanation"]["score_breakdown"]
    assert "seniority" not in pm
    assert em["seniority"] == -25
    assert "seniority" not in kam and kam["unrelated_role"] == -20


def test_generic_titles_without_data_context_are_not_promoted():
    assert score("Product Manager - Fintech")["match_explanation"]["score_breakdown"]["role"] < 32
    assert score("Health Solution Architect")["score"] < score("Solution Architect - Data & IA")["score"]


def test_experience_requirement_extraction_and_penalty():
    assert _required_years("Vous justifiez d'au moins 7 ans d'expérience") == 7
    assert _required_years("5+ years of experience with Python") == 5
    assert _required_years("Acme a 15 ans et 3 agences") is None
    ok = score("Data Engineer", description="3 ans d'expérience minimum. Python SQL")
    gap = score("Data Engineer", description="7 ans d'expérience minimum. Python SQL")
    big = score("Data Engineer", description="10 years of experience. Python SQL")
    assert "experience" not in ok["match_explanation"]["score_breakdown"]
    assert gap["match_explanation"]["score_breakdown"]["experience"] == -5
    assert big["match_explanation"]["score_breakdown"]["experience"] == -10
    assert ok["score"] > gap["score"] > big["score"]


def _set_seen(db, ext, first, last):
    con = connect(db)
    con.execute("UPDATE jobs SET first_seen_at=?, last_seen_at=? WHERE external_id=?", (first, last, ext))
    con.commit()
    con.close()


def test_review_queue_flags_new_and_hides_closed_offers(tmp_path):
    prefs, profile, cvs = configs()
    db = str(tmp_path / "f.db")
    jobs = [
        {
            "source": "lever",
            "external_id": "old",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "x",
        },
        {
            "source": "lever",
            "external_id": "gone",
            "title": "AI Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "y",
        },
        {
            "source": "lever",
            "external_id": "fresh",
            "title": "Data Analyst",
            "company": "Beta",
            "location": "Paris",
            "description": "z",
        },
        {
            "source": "linkedin",
            "external_id": "imp",
            "title": "Data Architect",
            "company": "Gamma",
            "location": "Paris",
            "description": "w",
        },
    ]
    upsert_jobs(run_pipeline(jobs, prefs, profile, cvs), db)
    now = datetime.now(UTC)
    old = (now - timedelta(days=5)).isoformat()
    _set_seen(db, "old", old, now.isoformat())
    _set_seen(db, "gone", old, old)  # Acme re-collected today without this one
    _set_seen(db, "fresh", now.isoformat(), now.isoformat())
    _set_seen(db, "imp", old, old)  # manual import: never marked closed
    q = {r["title"]: r for r in review_queue.build(db)}
    assert "AI Engineer" not in q
    assert q["Data Analyst"]["freshness"] == "new"
    assert q["Data Engineer"]["freshness"] == ""
    assert q["Data Architect"]["freshness"] == ""
    assert any(r["freshness"] == "closed?" for r in review_queue.build(db, include_closed=True))


def test_same_day_second_run_detects_closed_offer(tmp_path):
    prefs, profile, cvs = configs()
    db = str(tmp_path / "s.db")
    a = {
        "source": "lever",
        "external_id": "a",
        "title": "Data Engineer",
        "company": "Acme",
        "location": "Paris",
        "description": "x",
    }
    b = {
        "source": "lever",
        "external_id": "b",
        "title": "Stage Data Analyst",
        "company": "Acme",
        "location": "Paris",
        "description": "y",
    }
    upsert_jobs(run_pipeline([a, b], prefs, profile, cvs), db)  # run 1
    upsert_jobs(run_pipeline([a], prefs, profile, cvs), db)  # run 2, minutes later: b withdrawn
    titles = [r["title"] for r in review_queue.build(db)]
    assert titles == ["Data Engineer"]


def test_candidate_pack_reports_missing_cv_and_explanation(tmp_path):
    prefs, profile, cvs = configs()
    db = str(tmp_path / "p.db")
    job = {
        "source": "lever",
        "external_id": "1",
        "title": "Data Engineer",
        "company": "Acme",
        "location": "Paris",
        "description": "5+ years of experience. Python SQL BigQuery",
        "url": "https://x/1",
    }
    upsert_jobs(run_pipeline([job], prefs, profile, cvs), db)
    assert candidate_pack.main(["1"], db=db, out_dir=tmp_path) == 0
    text = (tmp_path / "candidate_pack_1.md").read_text(encoding="utf-8")
    assert "Why: Data Engineer | role +47" in text
    assert "Experience required: 5+ years" in text
    if not (ROOT / "cvs").exists():
        assert "FILE MISSING" in text
