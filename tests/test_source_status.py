"""Per-source health recorded at each collection and shown in the sidebar."""

from pathlib import Path

from fitly.sources import collector_registry, source_status

ROOT = Path(__file__).resolve().parents[1]


def test_collect_records_each_source_status(monkeypatch):
    monkeypatch.setitem(
        collector_registry.LISTERS,
        "ashby",
        lambda board, company=None: (
            [{"id": f"ashby:{board}:1", "source": "ashby", "title": "Data Engineer", "company": company}]
            if board == "full"
            else []
        ),
    )

    def boom(board, company=None):
        raise RuntimeError("HTTP 404")

    monkeypatch.setitem(collector_registry.LISTERS, "workable", boom)
    monkeypatch.setitem(
        collector_registry.SEARCHERS,
        "jooble",
        lambda keywords: (_ for _ in ()).throw(RuntimeError("JOOBLE_API_KEY not set")),
    )
    cfg = {
        "sources": [
            {"type": "ashby", "board": "full", "company": "Full"},
            {"type": "ashby", "board": "empty", "company": "Empty"},
            {"type": "workable", "board": "gone", "company": "Gone"},
            {"type": "jooble", "enabled": "auto", "keywords": ["data"]},
            {"type": "ashby", "board": "off", "enabled": False},
        ]
    }
    stats = []
    jobs, errors = collector_registry.collect_from_config(cfg, stats=stats)
    assert [s["status"] for s in stats] == ["ok", "empty", "error", "no_key", "disabled"]
    assert stats[0]["collected"] == 1 and "404" in stats[2]["error"] and len(errors) == 1
    report = source_status.build(stats, [{**jobs[0], "score": 80}])
    assert report["sources"][0]["relevant"] == 1 and report["sources"][1]["relevant"] == 0


def test_sources_panel_in_sidebar(tmp_path, monkeypatch):
    import pytest

    st_testing = pytest.importorskip("streamlit.testing.v1")
    from fitly.storage import upsert_jobs

    db = tmp_path / "s.db"
    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Acme",
                "location": "Paris",
                "description": "",
                "url": "https://jobs.lever.co/acme/1",
                "application_route": "external_ats",
                "score": 70,
            }
        ],
        str(db),
    )
    source_status.save(
        {
            "at": "2026-10-15T09:00:00+00:00",
            "sources": [
                {
                    "type": "lever",
                    "name": "Acme",
                    "target": "acme",
                    "label": "Lever",
                    "status": "ok",
                    "collected": 12,
                    "relevant": 3,
                    "error": "",
                },
                {
                    "type": "greenhouse",
                    "name": "Gone",
                    "target": "gone",
                    "label": "Greenhouse",
                    "status": "error",
                    "collected": 0,
                    "relevant": 0,
                    "error": "HTTP 404",
                },
                {
                    "type": "adzuna",
                    "name": "adzuna",
                    "target": None,
                    "label": "Adzuna",
                    "status": "no_key",
                    "collected": 0,
                    "relevant": 0,
                    "error": "",
                },
            ],
        },
        str(db),
    )
    monkeypatch.setenv("FITLY_DB", str(db))
    monkeypatch.setenv("FITLY_NO_CAPTURE", "1")
    at = st_testing.AppTest.from_file(str(ROOT / "src" / "fitly" / "ui" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any(e.label.startswith("📡 Sources : 1/3 OK") for e in at.sidebar.expander)
    assert any("HTTP 404" in e.value for e in at.sidebar.error)
    assert any("Adzuna" in i.value for i in at.sidebar.info)


def test_sources_panel_shows_manual_entries_and_morning_read(tmp_path, monkeypatch):
    import pytest

    st_testing = pytest.importorskip("streamlit.testing.v1")
    from openpyxl import load_workbook

    from fitly.sources import manual_offers
    from fitly.storage import upsert_jobs

    xlsx = manual_offers.create_template(tmp_path / "Mes offres.xlsx")
    wb = load_workbook(xlsx)
    wb.active.append(["2026-10-15", "Acme", "Data Engineer", "Paris", "today", "https://jobs.acme.fr/1", ""])
    wb.save(xlsx)
    monkeypatch.setattr(manual_offers, "FILE", xlsx)
    db = tmp_path / "s.db"
    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "AI Engineer",
                "company": "Beta",
                "location": "Paris",
                "description": "",
                "url": "https://jobs.lever.co/beta/1",
                "application_route": "external_ats",
                "score": 70,
            }
        ],
        str(db),
    )
    manual_offers.import_file(xlsx, str(db))
    source_status.save(
        {
            "at": "2026-10-15T09:00:00+00:00",
            "sources": [
                {
                    "type": "lever",
                    "name": "Beta",
                    "target": "beta",
                    "label": "Lever",
                    "status": "ok",
                    "collected": 1,
                    "relevant": 1,
                    "error": "",
                },
                {
                    "type": "manual",
                    "name": "Mes offres.xlsx",
                    "target": None,
                    "label": "Ajout manuel",
                    "status": "ok",
                    "collected": 1,
                    "relevant": 1,
                    "error": "",
                },
            ],
        },
        str(db),
    )
    status = manual_offers.live_status(str(db), source_status.load(str(db)))
    assert (status["lines"], status["in_fitly"], status["morning_read"]) == (1, 1, 1)
    monkeypatch.setenv("FITLY_DB", str(db))
    monkeypatch.setenv("FITLY_NO_CAPTURE", "1")
    at = st_testing.AppTest.from_file(str(ROOT / "src" / "fitly" / "ui" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("📥 1 manuelle(s)" in e.label for e in at.sidebar.expander)
    assert any("Ajout manuel" in m.value and "lu par la collecte" in m.value for m in at.sidebar.markdown)
