"""Seniority ladder, posting-age malus, search sources (mocked, no network), partial form fill."""

import json
from datetime import date, timedelta
from pathlib import Path

from fitly.apply.application_session import classify_field, gate_session
from fitly.scoring.pipeline import run_pipeline
from fitly.sources import ats_clients, email_alerts
from fitly.sources.collector_registry import collect_from_config

ROOT = Path(__file__).resolve().parents[1]

DESC = "Python SQL BigQuery GCP dbt Airflow data pipelines Spark"


def configs():
    return tuple(
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )


def score(title, days_ago=1, description=DESC):
    prefs, profile, cvs = configs()
    job = {
        "source": "x",
        "external_id": f"{title}{days_ago}",
        "title": title,
        "company": "C",
        "location": "Paris",
        "description": description,
        "posted_at": (date.today() - timedelta(days=days_ago)).isoformat(),
    }
    return run_pipeline([job], prefs, profile, cvs)[0]["score"]


def test_seniority_ladder_matches_user_targets():
    assert score("Junior Data Engineer") == 100
    assert score("Data Engineer") == 90
    assert score("Senior Data Engineer") == 70
    assert score("Data Engineer confirmé") == 85
    assert score("Stage Data Engineer") <= 40
    assert score("Team Leader Data Engineer") < score("Senior Data Engineer")


def test_old_offers_are_pushed_down():
    assert score("Data Engineer", 10) == 90
    assert score("Data Engineer", 30) < score("Data Engineer", 10)
    assert score("Data Engineer", 60) < score("Data Engineer", 30)
    assert score("Data Engineer", 200) < score("Data Engineer", 60)


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel>
<item><title>Data Engineer Junior F/H</title><description>&lt;p&gt;Python SQL&lt;/p&gt;</description>
<pubDate>Fri, 09 Oct 2026 10:00:00 +0200</pubDate><link>https://jobs.acme.fr/jobs/1-data-engineer</link>
<remoteStatus>hybrid</remoteStatus><guid>abc-1</guid>
<tt:locations><tt:location><tt:city>Paris</tt:city><tt:country>France</tt:country></tt:location></tt:locations></item>
</channel></rss>"""


class FakeResp:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_teamtailor_feed_is_parsed(monkeypatch):
    monkeypatch.setattr(ats_clients, "urlopen", lambda req, timeout=20: FakeResp(RSS))
    jobs, errors = collect_from_config({"sources": [{"type": "teamtailor", "board": "acme", "company": "Acme"}]})
    assert not errors and len(jobs) == 1
    j = jobs[0]
    assert (j["title"], j["company"], j["location"], j["posted_at"]) == (
        "Data Engineer Junior F/H",
        "Acme",
        "Paris, France",
        "2026-10-09",
    )
    assert j["description"] == "Python SQL" and j["url"].startswith("https://jobs.acme.fr/")


def test_search_sources_with_mocked_apis(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    monkeypatch.setenv("SERPAPI_KEY", "k")
    calls = []

    def fake_get_json(url, headers=None, data=None, **kw):
        calls.append(url)
        if "adzuna" in url:
            return {
                "results": [
                    {
                        "id": "a1",
                        "title": "Data Engineer",
                        "company": {"display_name": "Acme"},
                        "location": {"display_name": "Paris"},
                        "description": "Python",
                        "redirect_url": "https://adzuna/a1",
                        "created": "2026-10-10T08:00:00Z",
                    }
                ]
            }
        if "jooble" in url:
            return {
                "jobs": [
                    {
                        "id": 7,
                        "title": "Analytics Engineer",
                        "company": "Beta",
                        "location": "Paris",
                        "snippet": "dbt",
                        "link": "https://jooble/7",
                        "updated": "2026-10-09T00:00:00",
                    }
                ]
            }
        return {
            "jobs_results": [
                {
                    "job_id": "g1",
                    "title": "AI Engineer",
                    "company_name": "Gamma",
                    "location": "Paris",
                    "via": "via HelloWork",
                    "description": "LLM",
                    "detected_extensions": {"posted_at": "il y a 3 jours"},
                    "apply_options": [{"title": "HelloWork", "link": "https://hellowork/g1"}],
                }
            ]
        }

    monkeypatch.setattr(ats_clients, "get_json", fake_get_json)
    jobs, errors = collect_from_config(
        {
            "sources": [
                {"type": "adzuna", "keywords": ["data engineer"], "pages": 1},
                {"type": "jooble", "keywords": ["analytics engineer"], "pages": 1},
                {"type": "google_jobs", "keywords": ["ai engineer", "x", "y"], "max_searches": 1, "rotate": False},
            ]
        }
    )
    assert not errors
    by = {j["source"]: j for j in jobs}
    assert by["adzuna"]["company"] == "Acme" and by["adzuna"]["posted_at"] == "2026-10-10"
    assert by["jooble"]["url"] == "https://jooble/7"
    assert by["google_jobs"]["url"] == "https://hellowork/g1" and by["google_jobs"]["posted_at"]
    assert sum("serpapi" in c for c in calls) == 1  # budget cap respected
    assert "q=ai+engineer+Paris" in next(c for c in calls if "serpapi" in c)  # city inside the query


def test_auto_sources_without_keys_are_silent(monkeypatch):
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY", "SERPAPI_KEY", "FT_CLIENT_ID", "FT_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    srcs = [
        {"type": t, "enabled": "auto", "keywords": ["data"]}
        for t in ("adzuna", "jooble", "google_jobs", "france_travail")
    ]
    assert collect_from_config({"sources": srcs}) == ([], [])
    _jobs, errors = collect_from_config({"sources": [{"type": "adzuna", "keywords": ["data"]}]})
    assert errors and "ADZUNA_APP_ID" in errors[0]["error"]  # explicit source: error is reported
    assert collect_from_config({"sources": [{"type": "adzuna", "enabled": False}]}) == ([], [])


def test_wttj_alert_link_gives_company():
    html = '<a href="https://www.welcometothejungle.com/fr/companies/blue-river/jobs/data-engineer_paris">Data Engineer</a>'
    jobs = email_alerts.parse_other_boards(html, "")
    assert jobs[0]["company"] == "Blue River" and jobs[0]["title"] == "Data Engineer"


def test_partial_fill_and_derived_full_name():
    fields = [
        {"label": "Full name *", "required": True, "type": "text", "selector": "#n"},
        {
            "label": "Why us? Tell us about a project you are proud of *",
            "required": True,
            "type": "textarea",
            "selector": "#w",
        },
    ]
    answers = {"first_name": "Jane", "last_name": "Test"}
    strict = gate_session("ok", fields, answers)  # library default: conservative
    assert strict["status"] == "STOP"
    partial = gate_session("ok", fields, answers, unknown_required="leave_to_human")
    assert partial["status"] == "READY_FOR_HUMAN_REVIEW" and partial["submit_allowed"] is False
    assert partial["fill_plan"]["plan"][0]["value"] == "Jane Test"
    assert partial["remaining_required"] == ["Why us? Tell us about a project you are proud of *"]
    assert (
        gate_session("please verify you are human", fields, answers, unknown_required="leave_to_human")["status"]
        == "STOP"
    )


def test_long_questions_are_never_auto_answered():
    assert classify_field("Current location") == "location"
    assert classify_field("Quelle localisation privilégiez-vous pour ce poste ?") is None
    assert classify_field("Country*") == "country"


def test_more_alert_sites_are_recognised():
    html = (
        '<a href="https://www.cadremploi.fr/emploi/detail_offre?offreId=158123456">Data Engineer H/F</a>'
        '<a href="https://www.free-work.com/fr/tech-it/data-engineer/job-mission/data-engineer-gcp-paris">Data Engineer GCP</a>'
        '<a href="https://www.jobteaser.com/fr/job-offers/9876543-acme-data-engineer">Junior Data Engineer</a>'
    )
    jobs = {j["source"]: j for j in email_alerts.parse_other_boards(html, "")}
    assert set(jobs) == {"cadremploi", "free_work", "jobteaser"}
    assert jobs["jobteaser"]["title"] == "Junior Data Engineer"


def test_check_sources_reports_missing_keys(monkeypatch, capsys):
    from fitly import cli as fitly

    for k in ("SERPAPI_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY", "FT_CLIENT_ID", "FT_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "x")
    assert fitly.main(["check-sources"]) == 0
    out = capsys.readouterr().out
    assert "google_jobs" in out and "clé présente" in out and "ADZUNA_APP_ID" in out


def test_search_sources_ignore_options_meant_for_others(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    monkeypatch.setattr(ats_clients, "get_json", lambda *a, **k: {"jobs": []})
    _jobs, errors = collect_from_config(
        {"sources": [{"type": "jooble", "keywords": ["x"], "pages": 1, "max_searches": 1}]}
    )
    assert errors == []


def test_google_jobs_rotates_keywords_daily(monkeypatch):
    from datetime import date

    monkeypatch.setenv("SERPAPI_KEY", "k")
    seen = []
    monkeypatch.setattr(ats_clients, "get_json", lambda url, **k: seen.append(url) or {"jobs_results": []})
    kws = ["a", "b", "c", "d"]
    ats_clients.list_google_jobs(kws, max_searches=1)
    expected = kws[date.today().toordinal() % len(kws)]
    assert f"q={expected}+Paris" in seen[0]


def test_user_env_reads_registry_outside_tests_only(monkeypatch):
    from fitly import user_env

    monkeypatch.delenv("SOME_FITLY_TEST_KEY", raising=False)
    assert user_env.get("SOME_FITLY_TEST_KEY") is None  # isolation: no registry lookup in tests
    monkeypatch.setenv("SOME_FITLY_TEST_KEY", "v")
    assert user_env.get("SOME_FITLY_TEST_KEY") == "v"


def test_same_posting_on_two_sources_is_merged(tmp_path):
    from fitly.scoring.novelty import same_job
    from fitly.storage import connect, upsert_jobs

    a = {
        "source": "teamtailor",
        "external_id": "1",
        "company": "Contoso",
        "title": "Consultant Data Engineer Junior",
        "location": "Paris, France",
    }
    b = {
        "source": "google_jobs",
        "external_id": "g",
        "company": "Contoso",
        "title": "Consultant Data Engineer Junior",
        "location": "Paris",
    }
    c = {
        "source": "lever",
        "external_id": "2",
        "company": "Contoso",
        "title": "Consultant Data Engineer Junior",
        "location": "Lyon",
    }
    assert same_job(a, b) and not same_job(a, c)
    db = str(tmp_path / "d.db")
    upsert_jobs([a], db)
    assert upsert_jobs([b], db) == {"inserted": 0, "updated": 1}
    assert upsert_jobs([c], db) == {"inserted": 1, "updated": 0}
    con = connect(db)
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    con.close()


def test_jooble_drops_us_homonyms(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    monkeypatch.setattr(
        ats_clients,
        "get_json",
        lambda *a, **k: {
            "jobs": [
                {"id": 1, "title": "Data Engineer", "location": "Paris, TX", "link": "u1"},
                {"id": 2, "title": "Data Engineer", "location": "France", "link": "u2"},
            ]
        },
    )
    assert [j["external_id"] for j in ats_clients.list_jooble(["x"], pages=1)] == ["2"]
