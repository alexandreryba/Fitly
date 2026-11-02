"""End-to-end check of the REAL extension (copy on test ports) in Edge: tabs opened in the background,
forms drawn only when visible, preparation showing tabs in turn. Isolated: test DB, dummy identity, local pages."""

import json
import os
import shutil
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(tempfile.mkdtemp(prefix="fitly_e2e_"))  # everything (DB, extension copy, profile) is temporary
API_PORT, WEB_PORT = 8766, 8767
os.environ["FITLY_DB"] = str(HERE / "e2e.db")
os.environ["FITLY_CAPTURE_PORT"] = str(API_PORT)
for f in HERE.glob("e2e.db*"):
    f.unlink()
shutil.rmtree(HERE / "prefill", ignore_errors=True)
# 1. extension copy talking to the test server
ext = HERE / "ext"
shutil.rmtree(ext, ignore_errors=True)
shutil.copytree(ROOT / "extension", ext)
for name in ("background.js", "content.js", "manifest.json", "popup.js"):
    p = ext / name
    p.write_text(
        p.read_text(encoding="utf-8")
        .replace("127.0.0.1:8765", f"127.0.0.1:{API_PORT}")
        .replace(r"127\.0\.0\.1:8765", rf"127\.0\.0\.1:{API_PORT}"),
        encoding="utf-8",
    )
# 2. local application pages: the form is drawn on requestAnimationFrame (paused while a tab is hidden)
web = HERE / "web"
web.mkdir(exist_ok=True)
for i in range(1, 6):
    (web / f"job{i}.html").write_text(
        f"""<!doctype html><meta charset=utf-8><title>Offre {i}</title><h1>Offre {i}</h1>
<div id=app>chargement…</div><script>
requestAnimationFrame(() => setTimeout(() => {{ document.getElementById('app').innerHTML =
 '<form><label for=a>Prénom</label><input id=a><label for=b>Nom</label><input id=b>' +
 '<label for=c>E-mail</label><input id=c type=email></form>'; }}, 300));
</script>""",
        encoding="utf-8",
    )


class Quiet(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(web), **k)

    def log_message(self, *a):
        pass


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", WEB_PORT), Quiet).serve_forever, daemon=True).start()
# 3. test DB + Fitly server copy (dummy identity, no CV)
from fitly.storage import connect, upsert_jobs

upsert_jobs(
    [
        {
            "source": "lever",
            "external_id": str(i),
            "title": f"Data Engineer {i}",
            "company": f"Test{i}",
            "location": "Paris",
            "description": "",
            "url": f"http://127.0.0.1:{WEB_PORT}/job{i}.html",
            "application_route": "external_ats",
            "score": 80,
        }
        for i in range(1, 6)
    ],
    os.environ["FITLY_DB"],
)
from fitly.apply import capture_server, launch_review

launch_review.load_answers = lambda path=None: {
    "first_name": "Testeur",
    "last_name": "Fictif",
    "email": "test@example.com",
}
capture_server._rules = lambda: {"unknown_required_question": "leave_to_human"}
threading.Thread(target=capture_server.make_server(API_PORT).serve_forever, daemon=True).start()
ids = [r[0] for r in connect(os.environ["FITLY_DB"]).execute("SELECT id FROM jobs ORDER BY id")]
# 4. Edge with the extension; tabs opened in the BACKGROUND like Chrome's command line does
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        str(HERE / "profile"),
        channel="msedge",
        headless=False,
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--window-position=0,0"],
        ignore_default_args=[
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-extensions",
        ],
    )
    time.sleep(3)
    print("service workers:", [w.url for w in ctx.service_workers][:2])
    first = ctx.pages[0]
    cdp = ctx.new_cdp_session(first)
    for jid in ids:
        cdp.send(
            "Target.createTarget",
            {"url": f"http://127.0.0.1:{API_PORT}/go?job={jid}#fitly-fill={jid}", "background": True},
        )
    t0 = time.time()
    while time.time() - t0 < 120:
        rep = HERE / "prefill" / "last_run.json"
        done = json.loads(rep.read_text(encoding="utf-8"))["results"] if rep.exists() else []
        if sum(r["status"] == "prefilled" for r in done) >= len(ids):
            break
        time.sleep(2)
    print(f"elapsed {time.time() - t0:.0f}s")
    print("RESULT prefilled", sum(r["status"] == "prefilled" for r in done), "of", len(ids))
    for r in done:
        print(r["job_id"], r["status"], r["filled"])
    vals = {}
    for p in ctx.pages:
        if "job" in p.url:
            try:
                vals[p.url.rsplit("/", 1)[-1]] = p.evaluate("[...document.querySelectorAll('input')].map(i=>i.value)")
            except Exception as e:
                vals[p.url] = str(e)[:60]
    print(vals)
    print("visible order:", [p.evaluate("document.title") for p in ctx.pages if "job" in p.url])
    # Stage 2: a page the user opens himself (not a Fitly tab): one click on the floating button.
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:{WEB_PORT}/job3.html")
    page.bring_to_front()
    btn = page.wait_for_selector("#fitly-fill-btn button", timeout=20000)
    print(
        "button:",
        btn.inner_text(),
        "| fields before:",
        page.evaluate("[...document.querySelectorAll('input')].map(i=>i.value)"),
    )
    btn.click()
    page.wait_for_function(
        "() => document.querySelector('#a') && document.querySelector('#a').value === 'Testeur'", timeout=20000
    )
    after = page.evaluate("[...document.querySelectorAll('input')].map(i=>i.value)")
    print("after one click:", after)
    if after == ["Testeur", "Fictif", "test@example.com"]:
        print("RESULT button ok")
    ctx.close()
