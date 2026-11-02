from pathlib import Path

from fitly.scoring.novelty import classify
from fitly.scoring.pipeline import run_pipeline
from fitly.storage import connect, upsert_jobs

ROOT = Path(__file__).resolve().parents[1]


def configs():
    import json

    return (
        json.loads((ROOT / "tests/fixtures/preferences.json").read_text()),
        json.loads((ROOT / "tests/fixtures/profile.json").read_text()),
        json.loads((ROOT / "tests/fixtures/cv_profiles.json").read_text()),
    )


def test_pipeline_scores_and_selects_cv():
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "fixture",
            "external_id": "1",
            "title": "Data Engineer",
            "company": "ExampleCo",
            "location": "Paris",
            "description": "Python SQL BigQuery data pipelines",
            "url": "https://example.org/job/1",
        }
    ]
    result = run_pipeline(jobs, prefs, profile, cvs)
    assert len(result) == 1
    assert 0 <= result[0]["score"] <= 100
    assert result[0]["recommended_cv"] in {"tech_fr", "tech_en", "business_fr", "business_en"}
    assert result[0]["score_band"]


def test_pipeline_deduplicates_same_job():
    prefs, profile, cvs = configs()
    jobs = [
        {
            "source": "greenhouse",
            "external_id": "1",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "Python SQL",
        },
        {
            "source": "lever",
            "external_id": "2",
            "title": "Data Engineer",
            "company": "Acme",
            "location": "Paris",
            "description": "Python SQL",
        },
    ]
    assert len(run_pipeline(jobs, prefs, profile, cvs)) == 1


def test_storage_upsert_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    jobs = [
        {
            "source": "fixture",
            "external_id": "1",
            "title": "Data Engineer",
            "company": "ExampleCo",
            "location": "Paris",
            "score": 80,
            "score_band": "strong_match",
            "recommended_cv": "tech_fr",
            "url": "https://example.org/1",
        }
    ]
    assert upsert_jobs(jobs, str(db)) == {"inserted": 1, "updated": 0}
    assert upsert_jobs(jobs, str(db)) == {"inserted": 0, "updated": 1}
    con = connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    con.close()


def test_novelty_classifies_known_and_cross_source_duplicate():
    old = [
        {
            "source": "greenhouse",
            "external_id": "1",
            "company": "Acme",
            "title": "Data Engineer",
            "location": "Paris",
            "description": "Python SQL",
        }
    ]
    current = [
        old[0],
        {
            "source": "lever",
            "external_id": "2",
            "company": "NewCo",
            "title": "AI Engineer",
            "location": "Paris",
            "description": "Python ML",
        },
        {
            "source": "linkedin",
            "external_id": "3",
            "company": "NewCo",
            "title": "AI Engineer",
            "location": "Paris",
            "description": "Python ML",
        },
    ]
    result = classify(current, old)
    assert len(result["known"]) == 1
    assert len(result["new"]) == 1
    assert len(result["duplicates"]) == 1
