"""Employer-site cross-check, excluded companies, captcha notices, generic CV upload zones."""

import json
from pathlib import Path

from fitly.review import review_queue
from fitly.sources import employer_match
from fitly.storage import connect, upsert_jobs

ROOT = Path(__file__).resolve().parents[1]


def _seed(db, jobs):
    base = {"location": "Paris", "description": "Python SQL", "application_route": "external_ats", "score": 85}
    upsert_jobs([{**base, **j} for j in jobs], str(db))
    con = connect(str(db))
    out = {r["company"]: r["id"] for r in con.execute("SELECT id, company FROM jobs")}
    con.close()
    return out


def _row(db, job_id):
    con = connect(str(db))
    r = dict(con.execute("SELECT direct_url, availability FROM jobs WHERE id=?", (job_id,)).fetchone())
    con.close()
    return r


def test_relayed_offer_is_matched_on_the_employer_board(tmp_path):
    db = tmp_path / "m.db"
    ids = _seed(
        db,
        [
            {
                "source": "google_jobs",
                "external_id": "1",
                "title": "Data Engineer (H/F) - CDI",
                "company": "Acme Data",
                "url": "https://fr.linkedin.com/jobs/view/1",
            },
            {
                "source": "google_jobs",
                "external_id": "2",
                "title": "ML Engineer",
                "company": "Gone Corp",
                "url": "https://fr.indeed.com/viewjob?jk=2",
            },
            {
                "source": "google_jobs",
                "external_id": "3",
                "title": "Data Analyst",
                "company": "Global Homonym",
                "url": "https://www.apec.fr/x/3",
            },
            {
                "source": "lever",
                "external_id": "4",
                "title": "Analytics Engineer",
                "company": "Direct Co",
                "url": "https://jobs.lever.co/direct/4",
            },
        ],
    )
    boards = {
        "Acme Data": [{"type": "lever", "board": "acmedata"}],
        "Gone Corp": [{"type": "ashby", "board": "gonecorp"}],
        "Global Homonym": [{"type": "ashby", "board": "globalhomonym"}],
    }
    postings = {
        "acmedata": [
            {"title": "Data Engineer", "location": "Paris, France", "url": "https://jobs.lever.co/acmedata/9"}
        ],
        "gonecorp": [{"title": "Sales Manager", "location": "Paris", "url": "https://x/s"}],
        "globalhomonym": [{"title": "Data Analyst", "location": "New York", "url": "https://x/ny"}],
    }
    fetched = []
    stats = employer_match.check(
        db_path=str(db),
        prober=lambda c: boards.get(c, []),
        lister=lambda b: postings[b["board"]],
        fetch=lambda url: fetched.append(url) or True,
    )
    assert _row(db, ids["Acme Data"]) == {"direct_url": "https://jobs.lever.co/acmedata/9", "availability": "open"}
    assert _row(db, ids["Gone Corp"])["availability"] == "not_on_employer_site"
    assert _row(db, ids["Global Homonym"])["availability"] == "no_board"  # US homonym: proves nothing
    assert _row(db, ids["Direct Co"])["availability"] == "closed"  # employer page says expired
    assert fetched == ["https://jobs.lever.co/direct/4"]  # job boards are never fetched
    assert stats["direct_found"] == 1 and stats["closed"] == 1
    queue = review_queue.build(db)
    assert ids["Direct Co"] not in {r["id"] for r in queue}  # expired offers leave the list
    acme = next(r for r in queue if r["id"] == ids["Acme Data"])
    assert acme["url"] == "https://jobs.lever.co/acmedata/9"  # opened on the employer's form
    import os

    from fitly.apply import launch_review

    os.environ["FITLY_DB"] = str(db)
    try:
        assert launch_review.job_targets([ids["Acme Data"]])[0]["url"] == "https://jobs.lever.co/acmedata/9"
    finally:
        del os.environ["FITLY_DB"]


def test_first_word_board_of_a_multi_word_company_is_ignored(tmp_path):
    cache = {}
    found = employer_match.boards_for(
        "Capital Fund Management",
        cache,
        prober=lambda c: [{"type": "lever", "board": "capital"}, {"type": "lever", "board": "capitalfundmanagement"}],
    )
    assert found == [{"type": "lever", "board": "capitalfundmanagement"}]


def test_title_matching():
    assert (
        employer_match.best_match(
            {"title": "Data Engineer H/F", "location": "Paris"},
            [{"title": "Senior Data Engineer", "location": "Paris"}, {"title": "Data Engineer", "location": "Paris"}],
        )["title"]
        == "Data Engineer"
    )
    assert employer_match.best_match({"title": "Data Engineer"}, [{"title": "Product Designer"}]) is None


def test_page_closed_reads_expired_pages(monkeypatch):
    class Resp:
        def __init__(self, body, url):
            self.body, self.url = body, url

        def read(self, n):
            return self.body.encode()

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pages = {
        "https://c.com/jobs/1": Resp("<h1>Cette offre n'est plus disponible</h1>", "https://c.com/jobs/1"),
        "https://c.com/jobs/2": Resp("<h1>Data Engineer</h1><button>Postuler</button>", "https://c.com/jobs/2"),
    }
    monkeypatch.setattr(employer_match, "urlopen", lambda req, timeout, context: pages[req.full_url])
    assert employer_match.page_closed("https://c.com/jobs/1") is True
    assert employer_match.page_closed("https://c.com/jobs/2") is False


def test_excluded_companies_leave_the_queue(tmp_path, monkeypatch):
    db = tmp_path / "e.db"
    _seed(
        db,
        [
            {"source": "lever", "external_id": str(i), "title": "Data Engineer", "company": c, "url": f"https://x/{i}"}
            for i, c in enumerate(["Acme Partners", "Acme", "BluePoint France", "Globex", "Globex Consulting", "Hooli"])
        ],
    )
    monkeypatch.setattr(
        review_queue,
        "excluded_companies",
        lambda: ["Acme Partners", "Acme", "Globex Consulting", "Blue Point", "Umbrella"],
    )
    assert {r["company"] for r in review_queue.build(db)} == {"Globex", "Hooli"}


def test_shared_defaults_stay_generic_and_cautious():
    """The versioned defaults carry nothing personal (the user's own files live in config/local/), tick no
    consent box, call no paid API and target no employer list."""
    shared = lambda n: json.loads((ROOT / "config" / n).read_text(encoding="utf-8"))
    prefs = shared("preferences.json")
    assert prefs["excluded_companies"] == [] and prefs["salary"] == {"minimum": None, "target": None}
    assert review_queue.is_excluded("Acme Partners", ["Acme Partners"])
    assert shared("application_rules.json")["tick_required_consents"] is False
    assert shared("ai.json")["enabled"] is False
    assert all(
        s["type"] in ("adzuna", "jooble", "google_jobs", "france_travail")
        for s in shared("ats_sources.json")["sources"]
    )


def test_recaptcha_legal_notice_is_not_a_blocker():
    from fitly.apply.application_session import detect_blockers

    assert detect_blockers("Submit. This site is protected by reCAPTCHA and the Google Privacy Policy apply.") is None
    assert detect_blockers("Ce site est protégé par reCAPTCHA") is None
    assert detect_blockers("Please complete the CAPTCHA to continue") == "captcha"


def test_generic_upload_zone_receives_the_cv():
    """SmartRecruiters: two 'Choose a file or drop it here' zones; the last one is the resume."""
    from fitly.apply.application_session import build_fill_plan

    fields = [
        {"label": "Choose a file or drop it here", "type": "file", "selector": "auto"},
        {"label": "First name*", "type": "text", "selector": "fn"},
        {"label": "Confirm your email*", "type": "email", "selector": "em2"},
        {"label": "Choose a file or drop it here", "type": "file", "selector": "resume"},
    ]
    plan = build_fill_plan(fields, {"first_name": "A", "email": "a@example.com", "cv_file": "cv.pdf"})["plan"]
    assert [(i["field"]["selector"], i["key"]) for i in plan] == [
        ("fn", "first_name"),
        ("em2", "email"),
        ("resume", "cv_file"),
    ]
    labelled = [
        {"label": "CV", "type": "file", "selector": "cv"},
        {"label": "Choose a file", "type": "file", "selector": "other"},
    ]
    assert [i["field"]["selector"] for i in build_fill_plan(labelled, {"cv_file": "cv.pdf"})["plan"]] == ["cv"]


def test_extension_reloads_itself_when_its_files_change(tmp_path, monkeypatch):
    from fitly.apply import capture_server

    fp = capture_server.extension_fingerprint()
    assert len(fp) == 16 and fp == capture_server.extension_fingerprint()
    ext = tmp_path / "extension"
    ext.mkdir()
    (ext / "content.js").write_text("a", encoding="utf-8")
    monkeypatch.setattr(capture_server.paths, "EXTENSION_DIR", ext)
    before = capture_server.extension_fingerprint()
    (ext / "content.js").write_text("b", encoding="utf-8")
    assert capture_server.extension_fingerprint() != before
    bg = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "chrome.runtime.reload()" in bg and "/ping" in bg


def test_google_field_types_drive_personal_fields_only():
    from fitly.apply.application_session import build_fill_plan

    fields = [
        {"label": "Champ 1", "type": "text", "prediction": "NAME_FIRST", "selector": "a"},
        {"label": "", "type": "text", "autocomplete": "family-name", "selector": "b"},
        {"label": "Adresse", "type": "text", "prediction": "EMAIL_ADDRESS", "selector": "c"},
        {"label": "Prétentions salariales", "type": "text", "prediction": "NAME_FIRST", "selector": "d"},
    ]
    plan = build_fill_plan(
        fields, {"first_name": "A", "last_name": "B", "email": "e@example.com", "salary_expectation": "50 k€"}
    )["plan"]
    assert [(i["field"]["selector"], i["key"]) for i in plan] == [
        ("a", "first_name"),
        ("b", "last_name"),
        ("c", "email"),
        ("d", "salary_expectation"),
    ]


def test_expired_offers_are_not_opened(tmp_path):
    db = tmp_path / "v.db"
    ids = _seed(
        db,
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Live",
                "url": "https://jobs.lever.co/live/1",
            },
            {
                "source": "lever",
                "external_id": "2",
                "title": "Data Engineer",
                "company": "Gone",
                "url": "https://jobs.lever.co/gone/2",
            },
            {
                "source": "google_jobs",
                "external_id": "3",
                "title": "Data Engineer",
                "company": "Relay",
                "url": "https://fr.indeed.com/viewjob?jk=3",
            },
        ],
    )
    asked = []
    closed = employer_match.verify_before_opening(
        list(ids.values()), str(db), fetch=lambda u: asked.append(u) or ("gone" in u)
    )
    assert closed == [ids["Gone"]] and _row(db, ids["Gone"])["availability"] == "closed"
    assert not any("indeed" in u for u in asked)


def test_search_results_not_seen_for_two_weeks_leave_the_queue(tmp_path):
    db = tmp_path / "s.db"
    ids = _seed(
        db,
        [
            {
                "source": "google_jobs",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Old",
                "url": "https://fr.indeed.com/viewjob?jk=1",
            },
            {
                "source": "google_jobs",
                "external_id": "2",
                "title": "Data Engineer",
                "company": "Fresh",
                "url": "https://fr.indeed.com/viewjob?jk=2",
            },
        ],
    )
    con = connect(str(db))
    con.execute("UPDATE jobs SET last_seen_at='2020-01-01T00:00:00+00:00' WHERE id=?", (ids["Old"],))
    con.commit()
    con.close()
    assert {r["company"] for r in review_queue.build(db)} == {"Fresh"}


def test_standard_questions_seen_in_the_50_offer_audit():
    from fitly.apply.application_session import classify_field

    expected = {
        "Compensation Expectations": "salary_expectation",
        "When would you be able to start ?": "notice_period",
        "Link to your Github profile": "github",
        "Where did you see or hear about this job?*": "how_heard",
        "What is your level of English (written and spoken)?": "english_level",
        "Are you based in Paris or ready to relocate?": "relocation",
        "En quelle année as-tu/vas-tu recevoir ton diplôme ?": "graduation_year",
        "Dans quelle école as-tu étudié ?": "school",
        "*\nNom d'usage": "last_name",
        "Why do you want to join Dust?": None,
        "Gender*": None,
    }
    assert {k: classify_field(k) for k in expected} == expected


def test_workable_form_url():
    from fitly.apply.launch_review import form_url

    assert (
        form_url("https://apply.workable.com/withings/j/4A85465BC9/")
        == "https://apply.workable.com/withings/j/4A85465BC9/apply/"
    )
    assert form_url("https://careers.exotec.com/_/j/A4F78B5D7C") == "https://careers.exotec.com/_/j/A4F78B5D7C/apply/"


def test_cv_family_and_language():
    import json as _json

    from fitly.scoring.pipeline import _select_cv

    cvs = _json.loads((ROOT / "config" / "cv_profiles.json").read_text(encoding="utf-8"))
    fr = "Nous recherchons pour notre équipe des données une personne avec vous dans les projets"
    en = "We are looking for you to join our team and work with the data and the product"
    cases = {
        ("AI Engineer", fr): "ai_fr",
        ("Machine Learning Engineer", en): "ai_en",
        ("Forward Deployed Engineer", en): "ai_en",
        ("Data Scientist", fr): "datascience_fr",
        ("Data Engineer", fr): "tech_fr",
        ("AI Architect", en): "tech_en",
        ("Analytics Engineer", fr): "tech_fr",
        ("Consultant Data & IA", fr): "business_fr",
        ("Product Owner Data", en): "business_en",
        ("Business Analyst", fr): "business_fr",
    }
    for (title, desc), cv in cases.items():
        assert _select_cv({"title": title, "description": desc, "location": "Paris"}, cvs) == cv, title
    old = {"cvs": [c for c in cvs["cvs"] if c["track"] in ("tech", "business")]}  # 4-CV setup still works
    assert _select_cv({"title": "AI Engineer", "description": fr, "location": "Paris"}, old) == "tech_fr"


def test_offer_technologies_can_move_to_a_neighbouring_technical_cv():
    import json as _json

    from fitly.scoring.pipeline import _select_cv

    cvs = _json.loads((ROOT / "config" / "cv_profiles.json").read_text(encoding="utf-8"))
    fr = " nous vous les des pour avec "
    job = lambda t, d: {"title": t, "description": fr + d, "location": "Paris"}
    assert _select_cv(job("AI Engineer", "Spark Databricks Airflow dbt pipelines ETL Snowflake SQL"), cvs) == "tech_fr"
    assert _select_cv(job("AI Engineer", "LLM RAG agents LangGraph Spark"), cvs) == "ai_fr"  # title wins
    assert _select_cv(job("Consultant Data", "LLM RAG agents LangGraph embeddings genai"), cvs) == "business_fr"
