"""Large-scale pre-fill audit (dry run): what WOULD the extension fill on N offers from different careers sites?

Each offer is opened in a real Chrome (window off screen, Google's autofill field classification enabled), the
extension's content.js runs in dry-run mode: it opens the form ("Postuler"...), classifies the fields and asks
Fitly for its fill plan -- but types NOTHING and never submits. Results: data/prefill/audit.json + a table.

    python -m fitly.apply.prefill_audit --n 50        # diverse selection: one offer per company / careers site
    python -m fitly.apply.prefill_audit --jobs 12,34  # given offers
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from fitly import paths


def diverse_selection(rows, n=50, min_score=40) -> list[dict[str, Any]]:
    """Best offer of each company, direct employer pages only, alternating careers platforms (Lever,
    Greenhouse, Ashby, SmartRecruiters, Teamtailor...) so every platform is represented."""
    from fitly.sources import employer_match

    best: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda r: -(r.get("score") or 0)):
        if (
            (r.get("score") or 0) < min_score
            or employer_match.is_relay(r["url"], r["company"])
            or r.get("availability") == "closed"
        ):
            continue
        best.setdefault(r["company"].lower(), r)
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for r in best.values():
        by_platform.setdefault(r.get("source") or urlparse(r["url"]).netloc, []).append(r)
    picked: list[dict[str, Any]] = []
    while len(picked) < n and any(by_platform.values()):
        for plat in sorted(by_platform, key=lambda p: -len(by_platform[p])):
            if by_platform[plat] and len(picked) < n:
                picked.append(by_platform[plat].pop(0))
    # Fewer companies than requested: complete with the next best offer of companies already picked.
    ids = {r["id"] for r in picked}
    for r in sorted(rows, key=lambda r: -(r.get("score") or 0)):
        if len(picked) >= n:
            break
        if (
            r["id"] not in ids
            and (r.get("score") or 0) >= min_score
            and r.get("availability") != "closed"
            and not employer_match.is_relay(r["url"], r["company"])
            and sum(p["company"] == r["company"] for p in picked) < 2
        ):
            picked.append(r)
            ids.add(r["id"])
    return picked


def audit_one(pw, target, wait=45) -> dict[str, Any]:
    """Open one offer, run content.js in dry-run mode, return its last report."""
    from playwright.sync_api import Error as PlaywrightError

    from fitly.apply import capture_server

    # The extension API is simulated under private names (some sites redefine window.chrome).
    js = (
        (paths.EXTENSION_DIR / "content.js")
        .read_text(encoding="utf-8")
        .replace("chrome.runtime.sendMessage", "window.__jpSend")
        .replace("chrome.runtime.getManifest", "window.__jpManifest")
    )
    reports = []
    state = {"done": False}

    def bridge(msg):
        t = msg.get("type")
        if t == "hello":
            return {"fill": not state["done"], "jobId": str(target["id"]), "dry": True}
        if t == "status":
            return {}
        if t == "plan":
            return capture_server.fill_plan(msg["payload"])
        if t == "report":
            reports.append(msg["payload"])
            if msg.get("final"):
                state["done"] = True
            return {"ok": True}
        return {}

    browser = pw.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--enable-features=AutofillShowTypePredictions", "--window-position=-2600,0"],
    )
    started = time.time()
    try:
        ctx = browser.new_context(locale="fr-FR", viewport={"width": 1300, "height": 900})
        # Messages are queued in the page and answered from here (a binding can be broken by site scripts).
        ctx.add_init_script("""window.__jpManifest = () => ({version: "audit"});
            window.__jpQueue = []; window.__jpCallbacks = {}; window.__jpNext = 0;
            window.__jpSend = (msg, cb) => { const id = ++window.__jpNext; window.__jpCallbacks[id] = cb;
                                             window.__jpQueue.push({id, msg}); };
            window.__jpResolve = (id, r) => { const cb = window.__jpCallbacks[id]; delete window.__jpCallbacks[id]; cb && cb(r); };""")
        # Like the extension's content script (document ready, top frame), on every page of every tab.
        ctx.add_init_script(
            "if (window === window.top) document.addEventListener('DOMContentLoaded', () => setTimeout(() => {\n"
            + js
            + "\n}, 1500));"
        )
        page = ctx.new_page()
        try:
            from fitly.apply.launch_review import form_url

            page.goto(form_url(target["url"]), wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as exc:
            return {"status": "error", "reason": str(exc).splitlines()[0][:120]}
        end = time.time() + wait
        while time.time() < end and not state["done"]:
            for p in list(ctx.pages):
                try:
                    pending = p.evaluate("() => (window.__jpQueue || []).splice(0)")
                except PlaywrightError:  # page closed or navigating
                    continue
                for item in pending:
                    answer = bridge(item["msg"])
                    try:
                        p.evaluate("([id, r]) => window.__jpResolve(id, r)", [item["id"], answer])
                    except PlaywrightError:
                        pass
            time.sleep(0.4)
            if (
                reports
                and reports[-1].get("status") in ("captcha", "login", "platform", "no_form")
                and time.time() - started > 25
            ):
                break
        shot = paths.work_dir() / "prefill" / "audit" / f"{target['id']}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            ctx.pages[-1].screenshot(path=str(shot))
        except (PlaywrightError, OSError, IndexError):  # IndexError: the site closed every page
            pass
        out = (
            dict(reports[-1])
            if reports
            else {"status": "no_report", "reason": "le script n'a rien renvoyé (page bloquée ?)"}
        )
        out["final_url"] = ctx.pages[-1].url if ctx.pages else ""
        return out
    finally:
        browser.close()


def run(targets, workers=3, wait=45, verbose=True) -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('Playwright is not installed. Run: pip install -e ".[browser]"') from None

    results, lock, queue = [], threading.Lock(), list(targets)

    def worker():
        with sync_playwright() as pw:
            while True:
                with lock:
                    if not queue:
                        return
                    t = queue.pop(0)
                try:
                    r = audit_one(pw, t, wait)
                except Exception as exc:  # deliberate: one offer's failure never stops the audit
                    r = {"status": "error", "reason": str(exc).splitlines()[0][:120]}
                row = {
                    "job_id": t["id"],
                    "company": t["company"],
                    "title": t["title"],
                    "url": t["url"],
                    "platform": urlparse(t["url"]).netloc,
                    **{
                        k: r.get(k)
                        for k in (
                            "status",
                            "would_fill",
                            "fields",
                            "google_types",
                            "remaining_required",
                            "your_decision",
                            "warnings",
                            "reason",
                            "final_url",
                        )
                    },
                }
                with lock:
                    results.append(row)
                    if verbose:
                        print(
                            f"[{len(results):>2}/{len(targets)}] {row['status']:<9} fill={row.get('would_fill') or 0:<2} "
                            f"todo={len(row.get('remaining_required') or [])} {t['company'][:22]:<22} {row['platform']}",
                            flush=True,
                        )

    threads = [threading.Thread(target=worker) for _ in range(max(1, workers))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--jobs", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-score", type=int, default=40)
    a = ap.parse_args()
    from fitly.apply import launch_review
    from fitly.review import review_queue

    if a.jobs:
        targets = [{**t, "id": t["id"]} for t in launch_review.job_targets([int(x) for x in a.jobs.split(",") if x])]
    else:
        targets = diverse_selection(review_queue.build(), a.n, a.min_score)
    print(
        f"Audit (aucune saisie, aucun envoi) de {len(targets)} offres sur "
        f"{len({urlparse(t['url']).netloc for t in targets})} sites différents…",
        flush=True,
    )
    results = run(targets, a.workers)
    out = paths.work_dir() / "prefill" / "audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nRésumé :", dict(Counter(r["status"] for r in results)), "→", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
