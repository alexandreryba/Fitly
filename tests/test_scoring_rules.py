from pathlib import Path

from fitly.scoring.pipeline import _score, _select_cv, run_pipeline

ROOT = Path(__file__).resolve().parents[1]

PREFERENCES = {
    "location": {"primary": "Paris", "remote_ok": True, "idf_ok": True},
    "contracts": {"cdi_priority": 1, "freelance_priority": 2},
    "salary": {"minimum": 35000},
    "roles": {
        "priority_0": ["Junior Data Architect"],
        "priority_1": ["Data Engineer", "AI Engineer"],
        "priority_2": ["Analytics Engineer"],
    },
    "avoid_companies": ["Umbrella"],
}
PROFILE = {"skills": ["Python", "SQL", "GCP", "BigQuery", "Data pipelines", "Data Architecture", "AI", "GenAI"]}
CVS = {
    "cvs": [
        {"id": "business_fr", "language": "fr", "track": "business"},
        {"id": "business_en", "language": "en", "track": "business"},
        {"id": "tech_fr", "language": "fr", "track": "tech"},
        {"id": "tech_en", "language": "en", "track": "tech"},
    ]
}


def test_french_accents_are_normalized_for_role_matching():
    score, role, _ = _score({"title": "Ingénieur Data — Développeur Python", "location": "Paris"}, PREFERENCES, PROFILE)
    assert role is not None
    assert score > 0


def test_paris_offer_scores_above_same_offer_outside_idf():
    paris = {"title": "Data Engineer", "company": "Acme", "location": "Paris", "description": "Python SQL GCP BigQuery"}
    lyon = {**paris, "location": "Lyon"}
    assert _score(paris, PREFERENCES, PROFILE)[0] > _score(lyon, PREFERENCES, PROFILE)[0]


def test_remote_offer_does_not_receive_outside_idf_penalty():
    remote = {
        "title": "Data Engineer",
        "company": "Acme",
        "location": "Lyon",
        "description": "Full remote role. Python SQL GCP BigQuery",
    }
    local = {"title": "Data Engineer", "company": "Acme", "location": "Lyon", "description": "Python SQL GCP BigQuery"}
    assert _score(remote, PREFERENCES, PROFILE)[0] > _score(local, PREFERENCES, PROFILE)[0]


def test_avoid_company_is_capped_low():
    job = {
        "title": "Data Engineer",
        "company": "Umbrella",
        "location": "Paris",
        "description": "Python SQL GCP BigQuery",
    }
    assert _score(job, PREFERENCES, PROFILE)[0] <= 10


def test_french_title_selects_french_technical_cv_even_if_description_has_english_boilerplate():
    job = {
        "title": "AI Software Engineer / Développeur Augmenté (H/F)",
        "description": "Join our team and work with the latest technology. Vous participerez aux projets.",
    }
    assert _select_cv(job, CVS) == "tech_fr"


def test_english_engineer_title_selects_english_technical_cv():
    job = {"title": "Data Engineer", "description": "Join our team. You will work with Python and SQL."}
    assert _select_cv(job, CVS) == "tech_en"


def test_scores_are_not_all_saturated_at_100():
    jobs = [
        {"title": "Data Engineer", "company": "Acme", "location": "Paris", "description": "Python SQL GCP BigQuery"},
        {"title": "Data Engineer", "company": "Acme", "location": "Brussels", "description": "Python SQL GCP BigQuery"},
        {"title": "Data Analyst", "company": "Acme", "location": "Paris", "description": "Excel reporting"},
    ]
    out = run_pipeline(jobs, PREFERENCES, PROFILE, CVS)
    scores = [job["score"] for job in out]
    assert max(scores) <= 96
    assert len(set(scores)) > 1
