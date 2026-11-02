import json
from pathlib import Path

from fitly.scoring.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parents[1]


def configs():
    return (
        json.loads((ROOT / "config/preferences.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "config/profile.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "config/cv_profiles.json").read_text(encoding="utf-8")),
    )


def test_regular_role_scores_above_senior_and_internship():
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "fixture",
            "external_id": "regular",
            "title": "Data Engineer",
            "company": "RegularCo",
            "location": "Paris",
            "description": "Python SQL BigQuery data pipelines",
        },
        {
            "source": "fixture",
            "external_id": "senior",
            "title": "Senior Data Engineer",
            "company": "SeniorCo",
            "location": "Paris",
            "description": "Python SQL BigQuery data pipelines",
        },
        {
            "source": "fixture",
            "external_id": "intern",
            "title": "Stage Data Engineer",
            "company": "InternCo",
            "location": "Paris",
            "description": "Python SQL BigQuery data pipelines",
        },
    ]
    result = {j["external_id"]: j for j in run_pipeline(jobs, prefs, profile, cvs)}
    assert result["regular"]["score"] > result["senior"]["score"] > result["intern"]["score"]


def test_paris_data_role_scores_above_foreign_and_irrelevant_sre_role():
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "fixture",
            "external_id": "paris",
            "title": "Data Engineer",
            "company": "ParisCo",
            "location": "Paris, France",
            "description": "Python SQL BigQuery",
        },
        {
            "source": "fixture",
            "external_id": "foreign",
            "title": "Data Engineer",
            "company": "BrusselsCo",
            "location": "Brussels, Belgium",
            "description": "Python SQL BigQuery",
        },
        {
            "source": "fixture",
            "external_id": "sre",
            "title": "Site Reliability Engineer, AI Platform",
            "company": "SRECo",
            "location": "Paris, France",
            "description": "Python SQL AI platform reliability",
        },
    ]
    result = {j["external_id"]: j for j in run_pipeline(jobs, prefs, profile, cvs)}
    assert result["paris"]["score"] > result["foreign"]["score"]
    assert result["paris"]["score"] > result["sre"]["score"]


def test_french_title_prefers_french_cv_even_if_description_has_english_terms():
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "fixture",
            "external_id": "fr",
            "title": "Data Engineer confirmé (F/H/NB)",
            "company": "ExampleCo",
            "location": "Paris",
            "description": "Our team works with modern engineering tools.",
        }
    ]
    result = run_pipeline(jobs, prefs, profile, cvs)[0]
    assert result["recommended_cv"] == "tech_fr"


def test_scores_are_bounded_and_target_role_beats_lead_and_principal():
    prefs, profile, cvs = configs()
    base = {
        "source": "fixture",
        "company": "Co",
        "location": "Paris",
        "description": "Python SQL BigQuery GCP data pipelines",
    }
    titles = {
        "regular": "Data Engineer",
        "lead": "Lead Data Engineer",
        "principal": "Principal AI Engineer",
        "fr": "Ingénieur Data (H/F)",
        "po": "Product Manager Data",
    }
    jobs = [{**base, "external_id": k, "title": t, "company": k} for k, t in titles.items()]
    r = {j["external_id"]: j for j in run_pipeline(jobs, prefs, profile, cvs)}
    assert all(0 <= j["score"] <= 100 for j in r.values())
    assert r["regular"]["score"] < 100
    assert r["regular"]["score"] > r["lead"]["score"] > r["principal"]["score"]
    assert r["fr"]["match_explanation"]["matched_role"] == "Data Engineer"
    assert r["fr"]["recommended_cv"] == "tech_fr"
    assert "seniority" not in r["po"]["match_explanation"]["score_breakdown"]
    assert r["po"]["recommended_cv"].startswith("business_")


def test_technical_title_keeps_tech_cv_despite_business_words():
    prefs, profile, cvs = configs()
    job = {
        "source": "fixture",
        "external_id": "x",
        "title": "Data Engineer - Practice Cloud Transformation chez NORTHWIND",
        "company": "NORTHWIND",
        "location": "Paris",
        "description": "Vous rejoindrez notre équipe data.",
    }
    assert run_pipeline([job], prefs, profile, cvs)[0]["recommended_cv"] == "tech_fr"
