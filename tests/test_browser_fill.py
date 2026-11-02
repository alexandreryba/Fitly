"""Real Chromium (headless) against local HTML forms: what gets filled, what is left to the human, never submitted."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


sync_api = pytest.importorskip("playwright.sync_api")
from fitly.apply.browser_session import BrowserApplicationSession

FORM = """<!doctype html><html><body><h1>Apply: Data Engineer</h1>
<form id="f" onsubmit="window.SUBMITTED=true; return false;">
<input type="hidden" name="token" value="abc">
<label for="fn">First Name *</label><input id="fn" name="job_application[first_name]" required>
<label for="ln">Last Name *</label><input id="ln" name="job_application[last_name]" required>
<label for="em">Email *</label><input id="em" type="email" name="job_application[email]" required>
<label for="ph">Phone</label><input id="ph" name="job_application[phone]">
<label for="cv">Resume/CV *</label><input id="cv" type="file" name="resume" required style="opacity:0;width:1px">
<label for="li">LinkedIn Profile</label><input id="li" name="q_linkedin">
<label for="hear">How did you hear about us?</label><input id="hear" name="q_hear">
<label><input type="checkbox" id="consent" name="gdpr" required> I agree to the privacy policy</label>
<div style="display:none"><input name="honeypot"></div>
<button type="submit" id="submit">Submit application</button>
</form></body></html>"""

ANSWERS = {
    "first_name": "Jane",
    "last_name": "Test",
    "email": "jane@example.com",
    "phone": "0600000000",
    "linkedin": "https://www.linkedin.com/in/example",
}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        from fitly.apply.browser_launcher import launch

        try:
            b = launch(pw, headless=True)
        except Exception as exc:  # no browser at all
            pytest.skip(f"No browser available: {exc}")
        yield b
        b.close()


def open_form(browser, tmp_path, html):
    page = browser.new_page()
    f = tmp_path / "form.html"
    f.write_text(html, encoding="utf-8")
    s = BrowserApplicationSession(page)
    s.open(f.as_uri())
    return page, s


def test_prepares_form_but_never_submits(browser, tmp_path):
    cv = tmp_path / "cv_tech_fr.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    page, s = open_form(browser, tmp_path, FORM)
    labels = [f["label"] for f in s.inspect()["fields"]]
    assert "token" not in labels and "honeypot" not in labels and not any("Submit" in x for x in labels)
    gate = s.gate({**ANSWERS, "cv_file": str(cv)}, tick_consents=True)  # opt-in rule (off by default)
    assert gate["status"] == "READY_FOR_HUMAN_REVIEW", gate
    plan = gate["fill_plan"]
    assert [f["label"] for f in plan["skipped"]] == ["How did you hear about us?"]
    assert any(x["key"] == "consent" and x["field"]["name"] == "gdpr" for x in plan["plan"])  # required consent: ticked
    result = s.fill()
    assert result["submit_clicked"] is False and result["filled"] == 7  # 6 fields + required consent
    assert page.input_value("#fn") == "Jane" and page.input_value("#em") == "jane@example.com"
    assert page.eval_on_selector("#cv", "e => e.files.length") == 1
    assert page.is_checked("#consent") is True
    assert page.evaluate("window.SUBMITTED === true") is False
    page.close()


def test_unknown_required_question_is_left_to_human_or_stops_in_strict_mode(browser, tmp_path):
    html = FORM.replace(
        '<label for="hear">How did you hear about us?</label><input id="hear" name="q_hear">',
        '<label for="w">Why do you want to join us? *</label><textarea id="w" name="q_why" required></textarea>',
    )
    page, s = open_form(browser, tmp_path, html)
    gate = s.gate({**ANSWERS, "cv_file": "x.pdf"}, unknown_required="leave_to_human")
    assert gate["status"] == "READY_FOR_HUMAN_REVIEW"
    assert gate["remaining_required"] == ["Why do you want to join us? *"]
    s.fill()
    assert page.input_value("#fn") == "Jane" and page.input_value("#w") == ""  # never invents an answer
    assert page.evaluate("window.SUBMITTED === true") is False
    strict = s.gate({**ANSWERS, "cv_file": "x.pdf"}, unknown_required="stop")
    assert strict["status"] == "STOP"
    with pytest.raises(RuntimeError):
        s.fill()
    page.close()


def test_full_name_derived_and_weak_file_label_resolved(browser, tmp_path):
    html = """<html><body><form>
    <label for="n">Full name *</label><input id="n" name="name" required>
    <div class="field"><div class="label">Resume/CV *</div><input type="file" name="resume" required><span>Attach</span></div>
    </form></body></html>"""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    page, s = open_form(browser, tmp_path, html)
    gate = s.gate({"first_name": "Jane", "last_name": "Test", "cv_file": str(cv)}, unknown_required="stop")
    assert gate["status"] == "READY_FOR_HUMAN_REVIEW", gate
    s.fill()
    assert page.input_value("#n") == "Jane Test"
    page.close()


def test_stops_on_captcha(browser, tmp_path):
    page, s = open_form(browser, tmp_path, FORM.replace("<h1>", "<p>Please verify you are human</p><h1>"))
    assert s.gate({**ANSWERS, "cv_file": "x.pdf"})["reason"] == "captcha"
    page.close()


def test_missing_cv_file_is_reported_not_faked(browser, tmp_path):
    page, s = open_form(browser, tmp_path, FORM)
    s.gate({**ANSWERS, "cv_file": str(tmp_path / "missing.pdf")})
    result = s.fill()
    assert any("file not found" in x["reason"] for x in result["skipped"])
    assert page.eval_on_selector("#cv", "e => e.files.length") == 0
    page.close()


def test_practice_forms_shipped_with_the_project(browser, tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    for name, expected in (
        ("formulaire_entrainement.html", "READY_FOR_HUMAN_REVIEW"),
        ("formulaire_entrainement_stop.html", "STOP"),
    ):
        page = browser.new_page()
        s = BrowserApplicationSession(page)
        s.open((ROOT / "demo" / name).resolve().as_uri())
        gate = s.gate({**ANSWERS, "cv_file": str(cv)}, unknown_required="stop")
        assert gate["status"] == expected, (name, gate.get("reason"))
        if expected == "READY_FOR_HUMAN_REVIEW":
            assert s.fill()["submit_clicked"] is False
            assert page.is_visible("#done") is False  # the practice "Submit" was never clicked
        page.close()


def test_form_url_points_to_the_application_page():
    from fitly.apply.launch_review import form_url

    assert form_url("https://jobs.lever.co/acme/abc-123") == "https://jobs.lever.co/acme/abc-123/apply"
    assert form_url("https://jobs.lever.co/acme/abc-123/apply") == "https://jobs.lever.co/acme/abc-123/apply"
    assert form_url("https://jobs.ashbyhq.com/globex/xyz") == "https://jobs.ashbyhq.com/globex/xyz/application"
    assert (
        form_url("https://job-boards.greenhouse.io/initech/jobs/1") == "https://job-boards.greenhouse.io/initech/jobs/1"
    )


def test_launcher_end_to_end_on_practice_form(tmp_path, monkeypatch):
    """Full launch_review.main() in a headless browser: summary written, nothing submitted."""
    import builtins
    import json as _json

    from fitly.apply import launch_review

    monkeypatch.setenv("FITLY_HEADLESS", "1")
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    monkeypatch.setattr(
        launch_review, "load_answers", lambda path=None: {"first_name": "Testeur", "email": "t@example.com"}
    )
    monkeypatch.setenv("FITLY_DB", str(tmp_path / "fitly.db"))
    import threading

    box = {}
    # Own thread: the module's `browser` fixture already runs a sync Playwright loop in this one.
    t = threading.Thread(target=lambda: box.setdefault("rc", launch_review.main(["--demo", "ok", "--sans-cv"])))
    t.start()
    t.join(120)
    assert box.get("rc") == 0
    report = _json.loads((tmp_path / "prefill" / "last_run.json").read_text(encoding="utf-8"))
    res = report["results"][0]
    assert res["status"] == "prefilled" and res["filled"] >= 1
    assert any("Resume" in x for x in res["remaining_required"])


def test_description_page_opens_the_form_but_never_submits(browser, tmp_path):
    """Teamtailor-like page: the form appears after clicking 'Postuler' (an opener, not a submit)."""
    from fitly.apply import launch_review

    html = """<html><body><h1>Data Engineer</h1>
    <button id="open" onclick="document.getElementById('f').style.display='block'">Postuler</button>
    <form id="f" style="display:none" onsubmit="window.SUBMITTED=true;return false;">
      <label for="fn">Prénom*</label><input id="fn" required>
      <label for="em">E-mail*</label><input id="em" type="email" required>
      <button type="submit">Postuler</button>
    </form></body></html>"""
    f = tmp_path / "desc.html"
    f.write_text(html, encoding="utf-8")
    page = browser.new_page()
    res = launch_review.prefill_page(
        page,
        {"id": None, "url": f.as_uri(), "recommended_cv": None},
        {"first_name": "Jane", "email": "a@example.com"},
        no_cv=True,
    )
    assert res["status"] == "prefilled" and res["filled"] == 2, res
    assert page.input_value("#fn") == "Jane"
    assert page.evaluate("window.SUBMITTED === true") is False  # the in-form 'Postuler' submit is never clicked
    page.close()


def test_page_without_any_form_is_reported_honestly(browser, tmp_path):
    from fitly.apply import launch_review

    f = tmp_path / "noform.html"
    f.write_text("<html><body><h1>Offre</h1><p>Connectez-vous pour postuler.</p></body></html>", encoding="utf-8")
    page = browser.new_page()
    res = launch_review.prefill_page(page, {"id": None, "url": f.as_uri(), "recommended_cv": None}, {}, no_cv=True)
    assert res["status"] == "no_form" and "aucun formulaire" in res["reason"]
    page.close()


def test_teamtailor_like_labels_questions_and_yes_no(browser, tmp_path):
    """'Nom*\nRequis', salary/availability questions and a visa Oui/Non radio, answered from validated values only."""
    html = """<html><body><form onsubmit="window.SUBMITTED=true;return false;">
    <label for="sal">Quelles sont vos prétentions salariales en brut annuel ? (réponse attendue)</label><input id="sal">
    <label for="dispo">Sous quel délai serez-vous disponible pour une prise de poste ?*<br>Requis</label><input id="dispo" required>
    <fieldset><legend>Avez-vous des démarches administratives à réaliser pour pouvoir travailler chez nous ?*<br>Requis<br>Obtention d'un visa salarié par exemple</legend>
      <input type="radio" name="visa" id="v1" value="yes"><label for="v1">Oui</label>
      <input type="radio" name="visa" id="v2" value="no"><label for="v2">Non</label></fieldset>
    <label for="side">Exercez-vous actuellement une activité professionnelle secondaire ? Si oui laquelle</label><textarea id="side"></textarea>
    <label for="fn">Prénom*<br>Requis</label><input id="fn" required>
    <label for="ln">Nom*<br>Requis</label><input id="ln" required>
    <label><input type="checkbox" id="ok" required> Requis. En envoyant ma candidature, je déclare avoir lu la Politique</label>
    <button type="submit">Envoyer</button></form></body></html>"""
    page, s = open_form(browser, tmp_path, html)
    s.gate(
        {
            "first_name": "Jane",
            "last_name": "Test",
            "salary_expectation": "45 000 €",
            "notice_period": "1 mois",
            "sponsorship": "Non",
        },
        unknown_required="leave_to_human",
        tick_consents=True,
    )
    s.fill()
    assert page.input_value("#ln") == "Test" and page.input_value("#fn") == "Jane"
    assert page.input_value("#sal") == "45 000 €" and page.input_value("#dispo") == "1 mois"
    assert page.is_checked("#v2") and not page.is_checked("#v1")
    assert page.input_value("#side") == ""  # specific question left to the user
    assert page.is_checked("#ok")  # required acknowledgement ticked (opt-in rule)
    assert page.evaluate("window.SUBMITTED === true") is False
    page.close()


def test_required_consent_ticked_marketing_never(browser, tmp_path):
    html = """<html><body><form onsubmit="window.SUBMITTED=true;return false;">
    <label for="fn">First name *</label><input id="fn" required>
    <label><input type="checkbox" id="policy" required> I have read the privacy policy</label>
    <label><input type="checkbox" id="news" required> Subscribe to our newsletter *</label>
    <label><input type="checkbox" id="future"> Yes, you can contact me about future opportunities</label>
    <label><input type="checkbox" id="optional"> Remember me</label>
    <button type="submit">Submit</button></form></body></html>"""
    page, s = open_form(browser, tmp_path, html)
    s.gate({"first_name": "Jane"}, unknown_required="leave_to_human", tick_consents=True)
    s.fill()
    assert page.is_checked("#policy")
    assert not page.is_checked("#news") and not page.is_checked("#future") and not page.is_checked("#optional")
    assert page.evaluate("window.SUBMITTED === true") is False
    page.close()
    page, s = open_form(browser, tmp_path, html)
    s.gate({"first_name": "Jane"}, unknown_required="leave_to_human", tick_consents=False)
    s.fill()
    assert not page.is_checked("#policy")  # setting off: nothing ticked
    page.close()


def test_one_stubborn_field_does_not_stop_the_others():
    from fitly.apply.playwright_adapter import fill_validated

    class Loc:
        def __init__(self, sel, log):
            self.sel, self.log = sel, log

        def fill(self, value, timeout=None):
            if self.sel == "#bad":
                raise TimeoutError("Locator.fill: Timeout 5000ms exceeded.")
            self.log[self.sel] = value

    class Page:
        def __init__(self):
            self.log = {}

        def locator(self, sel):
            return Loc(sel, self.log)

    page = Page()
    plan = {
        "plan": [
            {"field": {"selector": "#bad", "label": "Nom", "type": "text"}, "value": "Test"},
            {"field": {"selector": "#ok", "label": "E-mail", "type": "email"}, "value": "a@example.com"},
        ]
    }
    out = fill_validated(page, plan)
    assert out["filled"] == 1 and page.log == {"#ok": "a@example.com"} and out["skipped"][0]["field"] == "Nom"
