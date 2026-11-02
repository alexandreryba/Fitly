"""Assisted capture: pages the user opens and sends with the extension button (no automated browsing)."""

import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from fitly.apply import capture_server
from fitly.storage import connect

ROOT = Path(__file__).resolve().parents[1]
# Upper bound for what content.js needs to fill a page: generous for slow CI runners, free when it is fast.
BROWSER_WAIT_MS = 60_000

POSTING_PAGE = {
    "url": "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-engineer_paris",
    "postings": [
        {
            "@type": "JobPosting",
            "title": "Data Engineer (H/F)",
            "datePosted": "2026-10-10",
            "hiringOrganization": {"@type": "Organization", "name": "Acme"},
            "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Paris", "addressCountry": "FR"}}],
            "description": "<p>Python, SQL, BigQuery, dbt, Airflow</p>",
            "employmentType": "FULL_TIME",
        }
    ],
    "links": [],
}

RESULTS_PAGE = {
    "url": "https://www.linkedin.com/jobs/search/?keywords=data%20engineer",
    "postings": [],
    "links": [
        {
            "href": "https://www.linkedin.com/jobs/view/4012345678/?refId=x",
            "text": "Data Engineer GCP",
            "card": "Data Engineer GCP\nBeta Corp\nParis, Île-de-France, France (Hybride)\nPromu\nIl y a 2 jours",
        },
        {"href": "https://www.linkedin.com/jobs/view/4012345678/", "text": "", "card": ""},
        {
            "href": "https://fr.indeed.com/viewjob?jk=0123456789abcdef",
            "text": "Analytics Engineer",
            "card": "Analytics Engineer\nGamma\nParis (75)",
        },
        {"href": "https://www.linkedin.com/feed/", "text": "Accueil", "card": ""},
    ],
}


def test_structured_job_page_is_normalised():
    jobs = capture_server.jobs_from_payload(POSTING_PAGE)
    assert len(jobs) == 1
    j = jobs[0]
    assert (j["source"], j["title"], j["company"], j["posted_at"]) == (
        "wttj",
        "Data Engineer (H/F)",
        "Acme",
        "2026-10-10",
    )
    assert j["location"].startswith("Paris") and "BigQuery" in j["description"] and "<p>" not in j["description"]


def test_results_page_links_and_cards():
    jobs = {j["source"]: j for j in capture_server.jobs_from_payload(RESULTS_PAGE)}
    assert set(jobs) == {"linkedin", "indeed"}
    li = jobs["linkedin"]
    assert (li["title"], li["company"]) == ("Data Engineer GCP", "Beta Corp")
    assert li["location"].startswith("Paris") and li["application_route"] == "linkedin_check"
    assert jobs["indeed"]["company"] == "Gamma"


def test_stable_id_when_page_has_no_known_pattern():
    page = {"url": "https://careers.example.com/jobs/42", "postings": [dict(POSTING_PAGE["postings"][0], url=None)]}
    a = capture_server.jobs_from_payload(page)[0]["external_id"]
    b = capture_server.jobs_from_payload(page)[0]["external_id"]
    assert a == b


def test_ingest_scores_and_stores_once(tmp_path):
    db = str(tmp_path / "c.db")
    r1 = capture_server.ingest(RESULTS_PAGE, db_path=db)
    r2 = capture_server.ingest(RESULTS_PAGE, db_path=db)
    assert r1["saved"] == 2 and r1["inserted"] == 2 and r2["inserted"] == 0
    con = connect(db)
    assert con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    assert con.execute("SELECT via FROM jobs LIMIT 1").fetchone()[0] == "capture"
    con.close()
    assert capture_server.ingest({"url": "x", "postings": [], "links": []}, db_path=db)["saved"] == 0


def test_http_receiver_accepts_extension_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FITLY_DB", str(tmp_path / "h.db"))
    server = capture_server.make_server(0)  # free port
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        body = json.dumps(POSTING_PAGE).encode("utf-8")

        def post(origin):
            headers = {"Content-Type": "application/json"}
            if origin:
                headers["Origin"] = origin
            req = Request(f"http://127.0.0.1:{port}/capture", data=body, headers=headers, method="POST")
            try:
                with urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except HTTPError as e:
                return e.code, json.loads(e.read())

        assert post("https://evil.example")[0] == 403  # a web page cannot push data
        assert post(None)[0] == 403
        assert post("chrome-extension://abcdefghijklmnopabcdefghijklmnop")[0] == 403  # nor another extension
        code, data = post(capture_server.EXTENSION_ORIGIN)
        assert code == 200 and data["saved"] == 1
        assert server.server_address[0] == "127.0.0.1"  # local only
        for origin in (None, "https://evil.example", "chrome-extension://abcdefghijklmnopabcdefghijklmnop"):
            # the CV is served to the Fitly extension only
            headers = {"Origin": origin} if origin else {}
            try:
                urlopen(Request(f"http://127.0.0.1:{port}/fill/cv?job_id=1", headers=headers), timeout=10)
                pytest.fail("CV served to a non-extension origin")
            except HTTPError as e:
                assert e.code == 403
    finally:
        server.shutdown()


def test_extension_extractor_in_a_real_browser(tmp_path):
    """Run the extension's extractPage() on a local page in a headless browser, then ingest the result."""
    import re as _re

    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    js = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
    fn = _re.search(r"function extractPage\(\) \{.*?\n\}", js, _re.S).group(0)
    html = """<html><head><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"JobPosting","title":"Junior Data Engineer",
     "hiringOrganization":{"name":"Delta"},"jobLocation":{"address":{"addressLocality":"Paris"}},
     "datePosted":"2026-10-11","description":"Python SQL"}]}</script></head>
    <body><ul><li><a href="https://www.linkedin.com/jobs/view/4099999999/">Data Architect</a><div>Epsilon</div><div>Paris</div></li></ul></body></html>"""
    page_file = tmp_path / "offer.html"
    page_file.write_text(html, encoding="utf-8")
    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page()
        page.goto(page_file.as_uri())
        result = page.evaluate(f"({fn})()")
        browser.close()
    assert result["postings"][0]["title"] == "Junior Data Engineer"
    assert any("4099999999" in x["href"] and "Epsilon" in x["card"] for x in result["links"])
    report = capture_server.ingest(result, db_path=str(tmp_path / "e.db"))
    assert report["saved"] == 1 and report["best"][0]["company"] == "Delta"


def test_extension_prefill_script_end_to_end(tmp_path, monkeypatch):
    """Run extension/content.js in a real (headless) browser; chrome.runtime is bridged to Fitly's real
    /fill/plan logic. Checks: opener click, fields filled, required consent ticked, newsletter untouched,
    nothing submitted, report stored."""
    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(
        capture_server,
        "_rules",
        lambda: {"unknown_required_question": "leave_to_human", "tick_required_consents": True},
    )
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {
            "first_name": "Jane",
            "last_name": "Test",
            "email": "a@example.com",
            "notice_period": "1 mois",
        },
    )
    html = """<html><body><h1>Data Engineer</h1>
    <button id="open" onclick="document.getElementById('f').style.display='block'">Postuler</button>
    <form id="f" style="display:none" onsubmit="window.SUBMITTED=true;return false;">
      <label for="fn">Prénom*<br>Requis</label><input id="fn" required>
      <label for="ln">Nom*<br>Requis</label><input id="ln" required>
      <label for="em">E-mail*</label><input id="em" type="email" required>
      <label for="d">Sous quel délai serez-vous disponible pour une prise de poste ?*</label><input id="d" required>
      <label><input type="checkbox" id="pol"> Requis. J'ai lu la politique de confidentialité</label>
      <label><input type="checkbox" id="news"> Recevoir la newsletter</label>
      <button type="submit">Postuler</button></form></body></html>"""
    page_file = tmp_path / "job.html"
    page_file.write_text(html, encoding="utf-8")
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    store = {}

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": True, "jobId": "0"}
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "x"})
        if msg["type"] == "report":
            store["report"] = msg["payload"]
            return {"ok": True}
        return {"ok": False}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page()
        page.expose_function("fitlyBridge", bridge)
        page.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page.goto(page_file.as_uri() + "#fitly-fill=0")
        page.add_script_tag(content=js)
        page.wait_for_function(
            "() => !!document.querySelector('#fn') && document.querySelector('#fn').value === 'Jane'",
            timeout=BROWSER_WAIT_MS,
        )
        page.wait_for_timeout(500)
        assert page.input_value("#ln") == "Test" and page.input_value("#em") == "a@example.com"
        assert page.input_value("#d") == "1 mois"
        assert page.is_checked("#pol") and not page.is_checked("#news")
        assert page.evaluate("window.SUBMITTED === true") is False
        assert "Fitly" in page.inner_text("body") and "Envoyer" in page.inner_text("body")  # review banner
        browser.close()
    assert store["report"]["status"] == "prefilled" and store["report"]["filled"] == 5


OPENER_LABELS = [
    "Je suis intéressé(e)",
    "Je suis intéressée",
    "Postuler",
    "Postuler sur le site de l'entreprise",
    "Apply on company site",
    "I'm interested",
    "Candidater",
]


def test_opener_labels_are_recognised_in_python_and_extension():
    import re as _re

    from fitly.apply import launch_review
    from fitly.scoring.pipeline import _norm

    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    js_rx = _re.compile(_re.search(r"const OPENER = /(.*)/;", js).group(1))

    def js_fold(t):  # same folding as content.js
        import unicodedata

        t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c)).lower()
        return " ".join(_re.sub(r"[^a-z0-9+#.' ]+", " ", t).split())

    for label in OPENER_LABELS:
        assert _re.match(launch_review.OPENER, _norm(label)), label
        assert js_rx.match(js_fold(label)), label
    for label in ("Envoyer", "Envoyer ma candidature", "Submit application", "Se connecter"):
        assert not _re.match(launch_review.OPENER, _norm(label)) and not js_rx.match(js_fold(label))


def test_prefer_employer_link_over_job_boards():
    from fitly.sources.ats_clients import best_apply_link

    opts = [
        {"link": "https://fr.linkedin.com/jobs/view/1"},
        {"link": "https://www.apec.fr/x"},
        {"link": "https://jobs.lever.co/acme/123"},
    ]
    assert best_apply_link(opts) == "https://jobs.lever.co/acme/123"
    assert best_apply_link(opts[:1]) == "https://fr.linkedin.com/jobs/view/1"
    assert best_apply_link([]) is None


def test_fill_report_keeps_prefilled_result_and_names_the_offer(tmp_path, monkeypatch):
    import json as _json

    db = tmp_path / "r.db"
    from fitly.storage import upsert_jobs

    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Acme",
                "location": "Paris",
                "url": "https://x/1",
                "score": 90,
            }
        ],
        str(db),
    )
    monkeypatch.setenv("FITLY_DB", str(db))
    capture_server.fill_report({"job_id": "1", "status": "platform", "filled": 0})
    capture_server.fill_report({"job_id": "1", "status": "prefilled", "filled": 4})  # employer's form, child tab
    capture_server.fill_report({"job_id": "1", "status": "no_form", "filled": 0})  # late report of the job board tab
    res = _json.loads((tmp_path / "prefill" / "last_run.json").read_text(encoding="utf-8"))["results"]
    assert len(res) == 1 and res[0]["status"] == "prefilled" and res[0]["company"] == "Acme"


def test_extension_waits_for_captcha_solved_by_user_then_fills(tmp_path, monkeypatch):
    """A CAPTCHA frame is never touched: Fitly reports it, waits, and fills once the user has solved it."""
    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(capture_server, "_rules", lambda: {"unknown_required_question": "leave_to_human"})
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {"first_name": "Jane", "last_name": "Test", "email": "a@example.com"},
    )
    html = """<html><body><iframe id="cap" style="width:400px;height:400px"></iframe><iframe id="badge" style="width:256px;height:60px" src="about:blank#www.google.com/recaptcha/api2/anchor"></iframe>
    <form><label for="fn">Prénom</label><input id="fn"><label for="ln">Nom</label><input id="ln">
    <label for="em">E-mail</label><input id="em"></form>
    <script>document.getElementById('cap').setAttribute('src', 'about:blank#geo.captcha-delivery.com/captcha');</script>
    </body></html>"""
    page_file = tmp_path / "cap.html"
    page_file.write_text(html, encoding="utf-8")
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    reports = []

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": True, "jobId": "0"}
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "x"})
        if msg["type"] == "report":
            reports.append(msg["payload"])
            return {"ok": True}
        return {}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page()
        page.expose_function("fitlyBridge", bridge)
        page.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page.goto(page_file.as_uri())
        page.add_script_tag(content=js)
        page.wait_for_timeout(2500)
        assert [r["status"] for r in reports] == ["captcha"] and page.input_value("#fn") == ""
        page.evaluate("document.getElementById('cap').remove()")  # the user solved it
        page.wait_for_function("() => document.querySelector('#fn').value === 'Jane'", timeout=BROWSER_WAIT_MS)
        browser.close()
    assert reports[-1]["status"] == "prefilled"


def test_extension_hybrid_google_types_shadow_dom_and_no_overwrite(tmp_path, monkeypatch):
    """SmartRecruiters-like form: fields in a shadow root, weak labels classified by Google (autofill-prediction),
    a generic upload zone, and an email already filled by Chrome autofill that must be left untouched."""
    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(capture_server, "_rules", lambda: {"unknown_required_question": "leave_to_human"})
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {"first_name": "Jane", "last_name": "Test", "email": "a@example.com", "phone": "0600000000"},
    )
    html = """<html><body><div id="host"></div><script>
    const root = document.getElementById('host').attachShadow({mode: 'open'});
    root.innerHTML = `<div><span>Champ A</span><input id="f1" autofill-prediction="NAME_FIRST"></div>
      <div><span>Champ B</span><input id="f2" autofill-prediction="NAME_LAST"></div>
      <div><span>Contact</span><input id="f3" type="email" autofill-prediction="EMAIL_ADDRESS" value="chrome@autofill.fr"></div>
      <div><span>Mobile</span><input id="f4" type="tel" autocomplete="tel"></div>`;
    </script></body></html>"""
    page_file = tmp_path / "sr.html"
    page_file.write_text(html, encoding="utf-8")
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    reports = []

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": True, "jobId": "0"}
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "x"})
        if msg["type"] == "report":
            reports.append(msg["payload"])
            return {"ok": True}
        return {}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page()
        page.expose_function("fitlyBridge", bridge)
        page.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            getManifest: () => ({version: "9.9.9"}),
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page.goto(page_file.as_uri())
        page.add_script_tag(content=js)
        page.wait_for_function(
            "() => document.getElementById('host').shadowRoot.getElementById('f4').value !== ''",
            timeout=BROWSER_WAIT_MS,
        )
        vals = page.evaluate(
            "() => ['f1','f2','f3','f4'].map(i => document.getElementById('host').shadowRoot.getElementById(i).value)"
        )
        browser.close()
    assert vals == ["Jane", "Test", "chrome@autofill.fr", "0600000000"]
    assert reports[-1]["status"] == "prefilled" and reports[-1]["filled"] == 3 and reports[-1]["ext_version"] == "9.9.9"


def _run_content(tmp_path, monkeypatch, html, dry=False, wait_for=None, timeout=20, picked=None):
    import time

    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(capture_server, "_rules", lambda: {"unknown_required_question": "leave_to_human"})
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {"first_name": "Jane", "last_name": "Test", "email": "a@example.com", "location": "Paris"},
    )
    page_file = tmp_path / "p.html"
    page_file.write_text(html, encoding="utf-8")
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    reports = []

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": True, "jobId": "0", "dry": dry}
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "x"})
        if msg["type"] == "report":
            reports.append(msg["payload"])
            return {"ok": True}
        return {}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page()
        page.expose_function("fitlyBridge", bridge)
        page.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page.goto(page_file.as_uri())
        page.add_script_tag(content=js)
        end = time.time() + timeout
        while time.time() < end and not reports:
            page.wait_for_timeout(300)
        values = page.evaluate("() => [...document.querySelectorAll('input')].map(i => i.value)")
        if picked is not None:
            picked.append(page.evaluate("() => window.PICKED || null"))
        browser.close()
    return reports, values


def test_dry_run_types_nothing(tmp_path, monkeypatch):
    html = """<form><label for=a>Prénom</label><input id=a><label for=b>Nom</label><input id=b>
              <label for=c>E-mail</label><input id=c></form>"""
    reports, values = _run_content(tmp_path, monkeypatch, html, dry=True)
    assert reports[-1]["status"] == "audit" and reports[-1]["would_fill"] == 3
    assert values == ["", "", ""]


def test_login_wall_is_reported(tmp_path, monkeypatch):
    html = """<h1>Connectez-vous pour continuer</h1><form><label>Mot de passe <input type=password></label>
              <button>Se connecter</button></form>"""
    reports, _ = _run_content(tmp_path, monkeypatch, html, timeout=30)
    assert reports[-1]["status"] == "login"


def test_combobox_option_is_picked(tmp_path, monkeypatch):
    """Greenhouse-style searchable list: type the validated value, then click the matching option."""
    html = """<form><label for=a>Prénom</label><input id=a><label for=b>Nom</label><input id=b>
      <label for=c>Country*</label><input id=c role=combobox aria-autocomplete=list>
      <div id=list></div></form><script>
      document.getElementById('c').addEventListener('input', () => {
        document.getElementById('list').innerHTML = '<div role=option>Finland</div><div role=option>France</div>';
        document.querySelectorAll('[role=option]').forEach(o => o.onclick = () => { window.PICKED = o.textContent; });
      });</script>"""
    picked = []
    reports, values = _run_content(tmp_path, monkeypatch, html, picked=picked)
    assert reports[-1]["status"] == "prefilled"
    assert values[2] == "France" and picked == ["France"]


def test_country_derived_from_french_city():
    from fitly.apply.application_session import with_derived

    assert with_derived({"location": "Paris"})["country"] == "France"
    assert with_derived({"location": "Paris", "country": "Belgique"})["country"] == "Belgique"
    assert "country" not in with_derived({"location": "Bruxelles"})


def test_cookies_refused_and_missing_fields_guided(tmp_path, monkeypatch):
    html = """<div id=cmp><p>Nous utilisons des cookies pour mesurer l'audience.</p>
      <button onclick="window.COOKIES='accepted';this.parentNode.remove()">Tout accepter</button>
      <button onclick="window.COOKIES='refused';this.parentNode.remove()">Tout refuser</button></div>
      <form><label for=a>Prénom</label><input id=a><label for=b>Nom</label><input id=b>
      <label for=c>E-mail</label><input id=c>
      <div style="height:3000px"></div>
      <label for=q>Pourquoi nous ?*</label><textarea id=q required></textarea>
      <p>Statut : <button type=button onclick="window.OTHER=1">Refuser</button></p></form>"""

    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(capture_server, "_rules", lambda: {"unknown_required_question": "leave_to_human"})
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {"first_name": "Jane", "last_name": "Test", "email": "a@example.com"},
    )
    page_file = tmp_path / "c.html"
    page_file.write_text(html, encoding="utf-8")
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    reports = []

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": True, "jobId": "0"}
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "x"})
        if msg["type"] == "report":
            reports.append(msg["payload"])
            return {"ok": True}
        return {}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        page = browser.new_page(viewport={"width": 1200, "height": 700})
        page.expose_function("fitlyBridge", bridge)
        page.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page.goto(page_file.as_uri())
        page.add_script_tag(content=js)
        page.wait_for_function("() => document.querySelector('#a').value === 'Jane'", timeout=BROWSER_WAIT_MS)
        page.wait_for_timeout(2000)
        state = page.evaluate("""() => ({cookies: window.COOKIES || null, other: window.OTHER || null,
            scrolled: window.scrollY, outline: getComputedStyle(document.querySelector('#q')).outlineColor,
            button: [...document.querySelectorAll('button')].some(b => b.textContent.includes('À compléter 1/1'))})""")
        browser.close()
    assert state["cookies"] == "refused" and state["other"] is None
    assert state["scrolled"] > 1000 and state["button"] and "245, 158, 11" in state["outline"]


def test_relay_page_and_url_resolution(tmp_path, monkeypatch):
    """Tabs open on Fitly's relay page first (marked before any redirection), then on the offer."""
    from fitly.storage import upsert_jobs

    db = tmp_path / "r.db"
    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Acme",
                "location": "Paris",
                "description": "",
                "url": "https://jobs.lever.co/acme/abc",
                "application_route": "external_ats",
                "score": 80,
            }
        ],
        str(db),
    )
    monkeypatch.setenv("FITLY_DB", str(db))
    page = capture_server.go_page(1)
    assert 'data-fitly-target="https://jobs.lever.co/acme/abc/apply"' in page
    assert capture_server.go_page(999) is None and capture_server.go_page("x") is None
    assert capture_server.resolve_job("https://jobs.lever.co/acme/abc/apply?lever-source=x#top") == 1
    assert capture_server.resolve_job("https://jobs.lever.co/acme/other") is None
    from fitly.apply import launch_review

    monkeypatch.setattr("fitly.apply.browser_launcher.open_in_user_chrome", lambda urls: urls)
    urls = launch_review.open_tabs_for_extension([1])
    assert urls == ["http://127.0.0.1:8765/go?job=1#fitly-fill=1"]


def test_one_click_fill_on_any_page_and_inside_frames(tmp_path, monkeypatch):
    """Alt+Shift+P / popup: the background sends 'manual_fill'; every frame fills what it recognises."""
    import time

    import pytest

    sync_api = pytest.importorskip("playwright.sync_api")
    from fitly.apply.browser_launcher import launch

    monkeypatch.setenv("FITLY_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(capture_server, "_rules", lambda: {"unknown_required_question": "leave_to_human"})
    from fitly.apply import launch_review

    monkeypatch.setattr(
        launch_review,
        "load_answers",
        lambda path=None: {"first_name": "Jane", "last_name": "Test", "email": "a@example.com"},
    )
    (tmp_path / "frame.html").write_text(
        """<form><label for=a>Prénom</label><input id=a>
        <label for=b>Nom</label><input id=b><label for=c>E-mail</label><input id=c></form>""",
        encoding="utf-8",
    )
    (tmp_path / "page.html").write_text(
        """<h1>Careers</h1><iframe id=f src="frame.html" style="width:600px;height:400px"></iframe>
        <form><label for=n>E-mail newsletter</label><input id=n></form>""",
        encoding="utf-8",
    )
    js = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")
    reports = []

    def bridge(msg):
        if msg["type"] == "hello":
            return {"fill": False}  # not a Fitly tab: nothing happens on its own
        if msg["type"] == "plan":
            return capture_server.fill_plan({**msg["payload"], "job_id": "manual"})
        if msg["type"] == "report":
            reports.append(msg["payload"])
            return {"ok": True}
        if msg["type"] == "status":
            return {"filled": sum(r.get("filled") or 0 for r in reports)}
        return {}

    with sync_api.sync_playwright() as pw:
        try:
            browser = launch(pw, headless=True)
        except Exception as exc:
            pytest.skip(f"No browser: {exc}")
        ctx = browser.new_context()
        ctx.expose_function("fitlyBridge", bridge)
        ctx.add_init_script("""window.chrome = window.chrome || {}; chrome.runtime = {
            onMessage: {addListener: (f) => { window.__jpListener = f; }},
            sendMessage: (msg, cb) => window.fitlyBridge(msg).then(r => cb && cb(r)) };""")
        page = ctx.new_page()
        page.goto((tmp_path / "page.html").as_uri())
        for fr in page.frames:
            fr.add_script_tag(content=js)
        page.wait_for_timeout(500)
        assert page.frames[1].evaluate("document.querySelector('#a').value") == ""
        for fr in page.frames:  # what chrome.tabs.sendMessage does: every frame
            fr.evaluate("window.__jpListener({type: 'manual_fill', jobId: 'manual'})")
        frame = page.frames[1]
        end = time.time() + 20
        while time.time() < end and frame.evaluate("document.querySelector('#a').value") != "Jane":
            page.wait_for_timeout(300)
        vals = frame.evaluate("['#a','#b','#c'].map(s => document.querySelector(s).value)")
        newsletter = page.evaluate("document.querySelector('#n').value")
        browser.close()
    assert vals == ["Jane", "Test", "a@example.com"]
    assert newsletter == ""  # a lone e-mail box outside the form is left alone


def test_tabs_are_shown_in_turn_then_back_to_the_first():
    """background.js 'préparation': Fitly tabs become visible one after another (throttled background tabs
    do not draw their forms), tabs already finished are skipped, and the first offer is shown at the end."""
    import json as _json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js absent")
    out = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "prep_test.js")], capture_output=True, text=True, timeout=60
    )
    res = _json.loads(out.stdout.strip().splitlines()[-1])
    assert res["afterHello"] == [11] and res["activated"] == [11, 13, 11]


def test_real_extension_end_to_end_in_edge():
    """Opt-in (FITLY_E2E=1, opens an Edge window ~15 s): the REAL extension, tabs opened in the background
    with forms drawn only when visible, preparation, then the one-click button on a page opened by hand."""
    import os
    import subprocess

    import pytest

    if os.environ.get("FITLY_E2E") != "1":
        pytest.skip("set FITLY_E2E=1 to run the Edge end-to-end test")
    out = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "e2e" / "extension_e2e.py")],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=300,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert "RESULT prefilled 5 of 5" in out.stdout and "RESULT button ok" in out.stdout, (
        out.stdout[-2000:] + out.stderr[-2000:]
    )
