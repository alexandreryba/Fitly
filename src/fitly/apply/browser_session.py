"""Playwright session for one application form: inspect, gate, fill validated fields. Never submits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fitly import config
from fitly.apply.application_session import gate_session
from fitly.apply.playwright_adapter import fill_validated, inspect_page


class BrowserApplicationSession:
    """Human-in-the-loop Playwright wrapper. Never submits the application."""

    def __init__(self, page):
        self.page = page
        self.last_inspection: dict[str, Any] | None = None
        self.last_gate: dict[str, Any] | None = None

    def open(self, url) -> str:
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page.url

    def inspect(self) -> dict[str, Any]:
        self.last_inspection = inspect_page(self.page)
        return self.last_inspection

    def gate(self, answers, unknown_required=None, tick_consents=None) -> dict[str, Any]:
        inspection = self.last_inspection if self.last_inspection is not None else self.inspect()
        rules = config.load("application_rules.json", default={})
        if unknown_required is None:
            unknown_required = rules.get("unknown_required_question", "stop")
        if tick_consents is None:
            tick_consents = bool(rules.get("tick_required_consents", False))
        fields = inspection["fields"]
        self.last_gate = gate_session(
            inspection["text"],
            fields,
            answers,
            unknown_required=unknown_required,
            tick_consents=tick_consents,
        )
        return self.last_gate

    def fill(self) -> dict[str, Any]:
        if not self.last_gate or self.last_gate.get("status") != "READY_FOR_HUMAN_REVIEW":
            raise RuntimeError("Application is not cleared for filling.")
        return fill_validated(self.page, self.last_gate["fill_plan"])

    def snapshot(self, path="data/application_review.png") -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
        return path
