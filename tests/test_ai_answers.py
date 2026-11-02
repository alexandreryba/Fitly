"""AI answer drafts: budget cap, grounding prompt, fallbacks to a template, no real API call."""

import json
from pathlib import Path
from types import SimpleNamespace

from fitly.apply import ai_answers

ROOT = Path(__file__).resolve().parents[1]

JOB = {
    "title": "Data Engineer",
    "company": "Acme",
    "location": "Paris",
    "description": "Python, BigQuery, dbt. 3 ans d'expérience.",
}
PROFILE = {"name": "Jane", "experience": "Data engineer", "skills": ["Python", "BigQuery", "dbt"]}
DRAFT = {
    "language": "fr",
    "cover_note": "Bonjour…",
    "why_role": "…",
    "why_company": "…",
    "key_matches": ["BigQuery -> BigQuery"],
    "answers": [{"question": "Pourquoi ?", "answer": "Parce que…"}],
    "gaps": [],
}


class FakeClient:
    def __init__(self, stop="end_turn", text=json.dumps(DRAFT), model="claude-opus-5-5"):
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                return SimpleNamespace(
                    stop_reason=stop,
                    model=model,
                    content=[SimpleNamespace(type="text", text=text)],
                    usage=SimpleNamespace(
                        input_tokens=3000, output_tokens=1500, cache_read_input_tokens=0, cache_creation_input_tokens=0
                    ),
                )

        self.beta = SimpleNamespace(messages=Messages())


def cfg(**kw):
    c = ai_answers.load_config(path="nonexistent.json")
    c.update(kw)
    return c


def test_draft_is_grounded_and_cost_recorded(tmp_path):
    facts = tmp_path / "facts.md"
    facts.write_text("- Double diplôme d'ingénieur et d'école de commerce", encoding="utf-8")
    client = FakeClient()
    ledger = tmp_path / "usage.json"
    draft = ai_answers.generate(
        JOB, PROFILE, ["Pourquoi ?"], "fr", cfg=cfg(), client=client, ledger=ledger, facts_path=facts
    )
    assert draft["cover_note"] == "Bonjour…" and draft["source"] == "claude-opus-5-5"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "Never invent" in call["system"]
    assert "Double diplôme" in call["messages"][0]["content"] and "Pourquoi ?" in call["messages"][0]["content"]
    # 3000 in * $4 + 1500 out * $20 = $0.042 -> EUR
    assert abs(ai_answers.spent_eur(ledger) - 0.042 * 0.92) < 1e-6


def test_budget_cap_blocks_the_call(tmp_path):
    ledger = tmp_path / "usage.json"
    ledger.write_text(json.dumps({ai_answers._month(): {"eur": 9.95, "calls": 1, "by_model": {}}}), encoding="utf-8")
    client = FakeClient()
    draft = ai_answers.generate(JOB, PROFILE, [], "fr", cfg=cfg(monthly_cap_eur=10.0), client=client, ledger=ledger)
    assert client.calls == [] and draft["source"] == "template" and "budget" in draft["note"]


def test_refusal_and_bad_json_fall_back_to_template(tmp_path):
    for client in (FakeClient(stop="refusal"), FakeClient(text="not json")):
        draft = ai_answers.generate(JOB, PROFILE, ["Q1"], "en", cfg=cfg(), client=client, ledger=tmp_path / "u.json")
        assert draft["source"] == "template"
        assert draft["answers"][0]["answer"].startswith("[TO COMPLETE")


def test_disabled_never_calls_api_and_markdown_is_saved(tmp_path):
    client = FakeClient()
    draft = ai_answers.generate(
        JOB, PROFILE, ["Disponibilité ?"], "fr", cfg=cfg(enabled=False), client=client, ledger=tmp_path / "u.json"
    )
    assert client.calls == [] and "[À COMPLÉTER" in draft["cover_note"]
    path = ai_answers.save(7, JOB, draft, out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Message de candidature" in text and "Disponibilité ?" in text
    assert json.loads((tmp_path / "answers_7.json").read_text(encoding="utf-8"))["source"] == "template"


def test_unknown_served_model_is_priced_conservatively():
    c = cfg()
    usage = SimpleNamespace(
        input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    assert ai_answers.usage_eur(c, usage, "some-future-model") == 10.0 * 0.92
