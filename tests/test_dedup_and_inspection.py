from pathlib import Path

from fitly.apply.playwright_adapter import inspect_page
from fitly.scoring.novelty import same_job
from fitly.storage import connect, upsert_jobs

ROOT = Path(__file__).resolve().parents[1]


def test_incomplete_records_are_not_deduplicated():
    a = {"company": "", "title": "", "location": "", "description": "A"}
    b = {"company": "", "title": "", "location": "", "description": "B"}
    assert same_job(a, b) is False


def test_storage_deduplicates_across_sources(tmp_path):
    db = tmp_path / "cross_source.db"
    a = {
        "source": "apec",
        "external_id": "a1",
        "company": "Acme",
        "title": "Data Engineer",
        "location": "Paris",
        "score": 80,
    }
    b = {
        "source": "greenhouse",
        "external_id": "g9",
        "company": "Acme",
        "title": "Data Engineer",
        "location": "Paris",
        "score": 82,
    }
    assert upsert_jobs([a], str(db)) == {"inserted": 1, "updated": 0}
    assert upsert_jobs([b], str(db)) == {"inserted": 0, "updated": 1}
    con = connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0] == 2
    con.close()


class FakeElement:
    def get_attribute(self, name):
        return {"name": "email", "required": ""}.get(name)

    def evaluate(self, _script):
        # the adapter describes each field in one browser call
        return {
            "label": "email",
            "name": "email",
            "type": "email",
            "tag": "input",
            "required": True,
            "visible": True,
            "selector": "#email",
        }


class FakeLocator:
    def __init__(self, value=None):
        self.value = value

    def inner_text(self):
        return "Application form"

    def all(self):
        return [FakeElement()]


class FakePage:
    def locator(self, _selector):
        return FakeLocator()


def test_inspection_returns_selector_for_form_fill():
    result = inspect_page(FakePage())
    assert result["fields"][0]["selector"] == "#email"
