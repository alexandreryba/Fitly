"""LinkedIn / job-board alert e-mails: parsing, inbox folder, read-only IMAP."""

import json
from email.message import EmailMessage
from pathlib import Path

from fitly.scoring.pipeline import run_pipeline
from fitly.sources import email_alerts

ROOT = Path(__file__).resolve().parents[1]

TEXT = """Your job alert for data engineer in Paris
New jobs match your preferences.

Data Engineer GCP
Acme Data
Paris, Île-de-France, France
View job: https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc%3D&refId=xyz

Senior Analytics Engineer
Beta Corp
Paris (Hybrid)
Be an early applicant
View job: https://www.linkedin.com/comm/jobs/view/4087654321/?trackingId=def

See all jobs: https://www.linkedin.com/comm/jobs/search?keywords=data
"""

HTML = """<html><body><table>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc">Data Engineer GCP</a>
<p>Acme Data · Paris, Île-de-France, France</p></td></tr>
<tr><td><table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/?x=1">Ingénieur Data (H/F)</a></td></tr>
<tr><td>Gamma SA</td></tr><tr><td>Courbevoie</td></tr><tr><td>Actively recruiting</td></tr></table></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/">View job</a></td></tr>
<tr><td><a href="https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/178123456W?xtor=1">Data Architect – Apec</a></td></tr>
</table></body></html>"""


def alert_bytes(text=TEXT, html=HTML, mid="<m1@linkedin.com>"):
    msg = EmailMessage()
    msg["From"] = "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = "Data Engineer: Acme Data and more"
    msg["Message-ID"] = mid
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg.as_bytes()


def by_id(jobs):
    return {j["id"]: j for j in jobs}


def test_linkedin_alert_is_parsed_into_complete_offers():
    jobs = by_id(email_alerts.parse_eml_bytes(alert_bytes()))
    a = jobs["linkedin:4012345678"]
    assert (a["title"], a["company"], a["url"]) == (
        "Data Engineer GCP",
        "Acme Data",
        "https://www.linkedin.com/jobs/view/4012345678/",
    )
    assert "Paris" in a["location"] or "Acme Data" in a["company"]
    b = jobs["linkedin:4087654321"]  # text-only block with LinkedIn noise line removed
    assert (b["title"], b["company"], b["location"]) == ("Senior Analytics Engineer", "Beta Corp", "Paris (Hybrid)")
    c = jobs["linkedin:4099999999"]  # html-only nested block, 'View job' link does not override the title
    assert (c["title"], c["company"], c["location"]) == ("Ingénieur Data (H/F)", "Gamma SA", "Courbevoie")
    assert all(j["application_route"] == "linkedin_check" for j in (a, b, c))
    apec = jobs["apec:178123456W"]
    assert apec["title"] == "Data Architect – Apec" and apec["application_route"] == "external_or_manual"
    assert not any("search" in j["url"] for j in jobs.values())


def test_parsed_alerts_flow_through_scoring():
    prefs, profile, cvs = (
        json.loads((ROOT / "tests" / "fixtures" / n).read_text(encoding="utf-8"))
        for n in ("preferences.json", "profile.json", "cv_profiles.json")
    )
    out = by_id(run_pipeline(email_alerts.parse_eml_bytes(alert_bytes()), prefs, profile, cvs))
    assert out["linkedin:4012345678"]["score"] >= 55
    assert out["linkedin:4099999999"]["match_explanation"]["matched_role"] == "Data Engineer"
    assert out["linkedin:4087654321"]["match_explanation"]["score_breakdown"]["seniority"] == -20


def test_inbox_folder_is_processed_once(tmp_path):
    (tmp_path / "alert.eml").write_bytes(alert_bytes())
    (tmp_path / "broken.eml").write_bytes(b"\x00\x01 not an email")
    jobs, _errors = email_alerts.collect_inbox(tmp_path)
    assert len({j["id"] for j in jobs}) == 4
    assert (tmp_path / "processed" / "alert.eml").exists()
    jobs2, _ = email_alerts.collect_inbox(tmp_path)
    assert not any(j["source"] == "linkedin" for j in jobs2)


class FakeIMAP:
    def __init__(self, host, port):
        self.selected = None
        self.fetched = []
        FakeIMAP.last = self

    def login(self, user, password):
        assert password == "app-pass"

    def select(self, folder, readonly=False):
        self.selected = (folder, readonly)
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        return ("OK", [b"1 2"]) if "jobalerts-noreply@linkedin.com" in criteria[-1] else ("OK", [b""])

    def fetch(self, num, what):
        self.fetched.append(what)
        return "OK", [(b"1 (BODY[] {n}", alert_bytes(mid=f"<m{num.decode()}@x>"))]

    def logout(self):
        pass


def test_imap_is_read_only_and_deduplicated(tmp_path):
    cfg = {
        "enabled": True,
        "user": "me@gmail.com",
        "senders": ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com"],
    }
    seen = tmp_path / "seen.json"
    jobs, errors = email_alerts.collect_imap(cfg, password="app-pass", seen_path=seen, imap_factory=FakeIMAP)
    assert not errors and len(jobs) == 8  # 4 offers x 2 messages (merged later by the pipeline)
    assert FakeIMAP.last.selected == ("INBOX", True)
    assert all(w == "(BODY.PEEK[])" for w in FakeIMAP.last.fetched)
    jobs, _ = email_alerts.collect_imap(cfg, password="app-pass", seen_path=seen, imap_factory=FakeIMAP)
    assert jobs == []  # same Message-IDs are not parsed twice


def test_imap_without_password_reports_clear_error(monkeypatch):
    monkeypatch.delenv("FITLY_IMAP_PASSWORD", raising=False)
    jobs, errors = email_alerts.collect_imap({"enabled": True, "user": "me"})
    assert jobs == [] and "password" in errors[0]["error"]
