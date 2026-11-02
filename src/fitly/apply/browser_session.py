"""Playwright session for one application form: inspect, gate, fill validated fields. Never submits."""

from __future__ import annotations

from pathlib import Path

from fitly import config
from fitly.apply.application_session import gate_session
from fitly.apply.playwright_adapter import fill_validated, inspect_page


class BrowserApplicationSession:
    """Human-in-the-loop Playwright wrapper. Never submits the application."""

    def __init__(self, page):
        self.page = page
        self.last_inspection = None
        self.last_gate = None

    def open(self, url):
        self.page.goto(url, wait_until="domcontentloaded")
        return self.page.url

    def inspect(self):
        self.last_inspection = inspect_page(self.page)
        return self.last_inspection

    def gate(self, answers, unknown_required=None, tick_consents=None):
        if self.last_inspection is None:
            self.inspect()
        rules = config.load("application_rules.json", default={})
        if unknown_required is None:
            unknown_required = rules.get("unknown_required_question", "stop")
        if tick_consents is None:
            tick_consents = bool(rules.get("tick_required_consents", False))
        fields = self.last_inspection["fields"]
        self.last_gate = gate_session(
            self.last_inspection["text"],
            fields,
            answers,
            unknown_required=unknown_required,
            tick_consents=tick_consents,
        )
        return self.last_gate

    def fill(self):
        if not self.last_gate or self.last_gate.get("status") != "READY_FOR_HUMAN_REVIEW":
            raise RuntimeError("Application is not cleared for filling.")
        return fill_validated(self.page, self.last_gate["fill_plan"])

    def snapshot(self, path="data/application_review.png"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=path, full_page=True)
        return path
