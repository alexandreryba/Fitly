"""Open application forms in a visible browser, fill ONLY validated fields, attach the CV, then hand over.

  python -m fitly.apply.launch_review --job ID          # one offer (terminal: Enter closes the browser)
  python -m fitly.apply.launch_review --jobs 12,23,45 --wait-close --mark-prepared
                                                            # several offers, one tab each (used by the interface)
  python -m fitly.apply.launch_review --demo ok|stop     # local practice form, no internet

Fitly never clicks a submit button. CAPTCHA / anti-bot = that tab is left untouched.
A summary is written to data/prefill/last_run.json (read by the interface).
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from fitly import config, paths

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def current_db():
    """Database in use, read at call time (sandbox/tests set FITLY_DB after this module may be imported)."""
    return paths.db_path()


def out_dir():
    """Pre-fill reports and screenshots, next to the database in use."""
    return paths.work_dir() / "prefill"


PERSONAL = {"first_name", "last_name", "full_name", "email", "phone", "linkedin", "location", "country", "cv_file"}


def cv_path(cv_id):
    cvs = config.load("cv_profiles.json").get("cvs", [])
    for c in cvs:
        if c.get("id") == cv_id:
            return paths.project_path(c.get("path", "")).resolve()
    return None


def form_url(url):
    """Application form page for job-board URLs that put the form on a sub-page."""
    from urllib.parse import urlparse

    u = urlparse(url)
    path = u.path.rstrip("/")
    if u.netloc.endswith("jobs.lever.co") and not path.endswith("/apply"):
        return f"{u.scheme}://{u.netloc}{path}/apply"
    if u.netloc.endswith("jobs.ashbyhq.com") and not path.endswith("/application"):
        return f"{u.scheme}://{u.netloc}{path}/application"
    # Workable (apply.workable.com/<co>/j/<id>/ or a company domain .../_/j/<id>): the form is on .../apply/
    if ("workable.com" in u.netloc or "/_/j/" in path) and "/j/" in path and not path.endswith("/apply"):
        return f"{u.scheme}://{u.netloc}{path}/apply/"
    return url


def load_answers(path=None):
    """Validated answers for forms: `path`, else the user's answer_library.json ({} when there is none)."""
    if path:
        p = paths.project_path(path)
        answers = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    else:
        answers = config.load("answer_library.json", default={})
    return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in answers.items() if not k.startswith("_")}


def job_targets(job_ids):
    from fitly.storage import connect

    con = connect(current_db())
    try:
        out = []
        for jid in job_ids:
            row = con.execute(
                "SELECT j.id,j.title,j.company,COALESCE(j.direct_url,j.url) AS url,"
                "COALESCE(a.cv_type,j.recommended_cv) AS recommended_cv "
                "FROM jobs j LEFT JOIN applications a ON a.job_id=j.id WHERE j.id=?",
                (jid,),
            ).fetchone()
            if row:
                out.append(dict(row))
            else:
                print(f"Offre inconnue : id {jid}")
        return out
    finally:
        con.close()


# Buttons/links that OPEN an application form (never a submit button: excluded below).
OPENER = (
    r"^(postuler|je postule|postuler maintenant|postuler a cette offre|postuler sur le site( de l.?entreprise)?|"
    r"candidater|je candidate|candidater sur le site|apply|apply now|apply for this job|apply for this position|"
    r"apply to this job|apply on (the )?(company|employer) (site|website)|i.?m interested|i am interested|"
    r"je suis interess(e|ee)?( e)?)$"
)
# Anti-robot checks: Fitly never solves or bypasses them; it stops and lets the user act.
CAPTCHA_FRAMES = ("captcha-delivery.com", "recaptcha", "hcaptcha", "challenges.cloudflare.com", "turnstile")


def captcha_present(page):
    """A challenge frame actually shown (invisible reCAPTCHA/hCaptcha badges of Lever, Greenhouse... are ignored)."""
    for f in page.frames:
        if f is page.main_frame or not any(k in (f.url or "").lower() for k in CAPTCHA_FRAMES):
            continue
        try:
            box = f.frame_element().bounding_box()
        except Exception:
            box = None
        if box and box["width"] >= 250 and box["height"] >= 250:
            return True
    return False


def usable_fields(fields):
    """How many inspected fields Fitly knows how to fill (identity, contact, CV)."""
    from fitly.apply.application_session import field_key

    return sum(1 for f in fields if f.get("type") == "file" or field_key(f) in PERSONAL)


def open_application_form(page):
    """On a job-description page, open the application form via its 'Postuler/Apply' opener.
    Only short opener labels; never a type=submit button and never anything inside a <form>."""
    import re as _re

    from fitly.scoring.pipeline import _norm

    rx = _re.compile(OPENER)
    candidates = page.locator("a, button, [role=button]")
    for i in range(min(candidates.count(), 300)):
        el = candidates.nth(i)
        try:
            text = _norm(el.inner_text(timeout=500))
            if not text or len(text) > 40 or not rx.match(text) or not el.is_visible():
                continue
            if el.evaluate("e => (e.getAttribute('type') || '').toLowerCase() === 'submit' || !!e.closest('form')"):
                continue
            href = el.get_attribute("href") or ""
            if href and not href.startswith(("#", "javascript")):
                page.goto(el.evaluate("e => e.href"), wait_until="domcontentloaded")
            else:
                el.evaluate("e => e.click()")  # DOM click: a cookie banner overlay is left for the user
            page.wait_for_timeout(2500)
            return True
        except Exception:
            continue
    return False


def prefill_page(page, target, base_answers, no_cv):
    """Fill one tab. Returns a summary dict; never clicks submit."""
    from fitly.apply.browser_session import BrowserApplicationSession

    answers = dict(base_answers)
    summary = {
        "job_id": target.get("id"),
        "company": target.get("company"),
        "title": target.get("title"),
        "url": target["url"],
        "filled": 0,
        "remaining_required": [],
        "your_decision": [],
        "warnings": [],
    }
    cv = target.get("recommended_cv")
    if no_cv:
        summary["warnings"].append("CV non joint (option sans CV)")
    elif cv:
        path = cv_path(cv)
        if path and path.is_file():
            answers["cv_file"] = str(path)
        else:
            summary["warnings"].append(f"CV {cv} absent du dossier cvs\\")
    session = BrowserApplicationSession(page)
    from fitly.sources.employer_match import is_relay

    if is_relay(target["url"], target.get("company")):
        try:
            session.open(target["url"])
        except Exception:
            pass
        summary.update(
            status="platform",
            reason="offre relayée par un site d'emploi : Fitly n'y automatise rien — clique sur Postuler "
            "dans l'onglet ; avec l'extension, le formulaire de l'entreprise sera pré-rempli",
        )
        return summary
    try:
        session.open(target["url"])
        page.wait_for_timeout(2500)  # let JavaScript forms render
        fields = session.inspect()["fields"]
        if usable_fields(fields) < 2 and open_application_form(page):
            if captcha_present(page):
                summary.update(
                    status="captcha",
                    reason="vérification anti-robot (CAPTCHA) : résous-la toi-même dans l'onglet, puis remplis "
                    "ou relance le pré-remplissage — Fitly ne la contourne jamais",
                )
                return summary  # description page: open the form first
            fields = session.inspect()["fields"]
            summary["warnings"].append("formulaire ouvert via le bouton « Postuler »")
        if usable_fields(fields) == 0 and captcha_present(page):
            summary.update(
                status="captcha", reason="vérification anti-robot (CAPTCHA) : à résoudre par toi dans l'onglet"
            )
            return summary
        if usable_fields(fields) == 0:
            summary.update(
                status="no_form",
                reason="aucun formulaire trouvé sur la page (connexion au site requise, "
                "formulaire sur un autre site, ou bouton non reconnu) — l'onglet reste ouvert pour toi",
            )
            return summary
        gate = session.gate(answers)
    except Exception as exc:
        summary.update(status="error", reason=str(exc).splitlines()[0][:200])
        return summary
    plan = gate.get("fill_plan", {})
    if gate["status"] != "READY_FOR_HUMAN_REVIEW":
        reasons = {
            "captcha": "CAPTCHA détecté",
            "anti_bot": "protection anti-robot détectée",
            "unknown_or_unvalidated_required_field": "question obligatoire inconnue (mode strict)",
        }
        summary.update(
            status="stopped",
            reason=reasons.get(gate.get("reason"), gate.get("reason")),
            remaining_required=[f.get("label") for f in plan.get("unknown", [])],
        )
        return summary
    result = session.fill()
    summary.update(
        status="prefilled",
        filled=result["filled"],
        remaining_required=gate.get("remaining_required", []),
        your_decision=[f.get("label") for f in plan.get("human", [])],
    )
    summary["warnings"] += [f"{x['field']} : {x['reason']}" for x in result["skipped"]]
    if target.get("id") is not None:
        out_dir().mkdir(parents=True, exist_ok=True)
        try:
            summary["screenshot"] = session.snapshot(str(out_dir() / f"job_{target['id']}.png"))
        except Exception:
            pass
    return summary


def open_tabs_for_extension(job_ids, no_cv=False):
    """Default mode: open each offer's form in a new tab of the user's Chrome; the Fitly extension
    (installed in that Chrome) pre-fills it using /fill/plan. Returns the list of opened URLs."""
    from datetime import datetime

    from fitly.apply.browser_launcher import open_in_user_chrome

    targets = job_targets(job_ids)
    prefill_dir = out_dir()
    prefill_dir.mkdir(parents=True, exist_ok=True)

    def _read(name, default):
        try:
            return json.loads((prefill_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    pending = _read("pending.json", {})
    pending.update(
        {
            str(t["id"]): {
                "no_cv": bool(no_cv),
                "cv": t.get("recommended_cv"),
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            for t in targets
        }
    )
    (prefill_dir / "pending.json").write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep the results of earlier launches; only the offers opened again start over.
    report = _read("last_run.json", {"at": "", "results": []})
    again = {str(t["id"]) for t in targets}
    report.update(
        mode="chrome_tab", results=[r for r in report.get("results", []) if str(r.get("job_id")) not in again]
    )
    (prefill_dir / "last_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Relay page on Fitly's local server: the extension marks the tab there before any redirection.
    from fitly.apply.capture_server import PORT

    urls = [f"http://127.0.0.1:{PORT}/go?job={t['id']}#fitly-fill={t['id']}" for t in targets]
    if urls:
        open_in_user_chrome(urls)
    return urls


def print_summary(s):
    head = f"[{s.get('company') or ''}] {s.get('title') or s['url']}"
    if s.get("status") == "prefilled":
        print(f"\n✓ {head} : {s['filled']} champ(s) pré-rempli(s)")
    elif s.get("status") in ("no_form", "captcha", "platform"):
        print(f"\n○ {head} : {s.get('reason')}")
    elif s.get("status") == "stopped":
        print(f"\n■ {head} : ARRÊT — {s.get('reason')}")
    else:
        print(f"\n✗ {head} : erreur — {s.get('reason')}")
    for label in s.get("remaining_required", []):
        print("    OBLIGATOIRE, À REMPLIR PAR TOI :", label)
    for label in s.get("your_decision", []):
        print("    TA DÉCISION (case à cocher) :", label)
    for w in s.get("warnings", []):
        print("    attention :", w)


def wait_until_closed(browser, context):
    """Keep the browser alive until the user closes every tab (or the window)."""
    while True:
        try:
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages or not browser.is_connected():
                return
            pages[0].wait_for_timeout(1000)
        except Exception:
            return


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="application form URL")
    p.add_argument("--job", type=int, help="one job id from the database")
    p.add_argument("--jobs", help="several job ids, comma-separated: one tab each")
    p.add_argument("--cv", help="CV id to attach (e.g. tech_fr), single offer only")
    p.add_argument("--answers", help="answers file (default: your answer_library.json)")
    p.add_argument("--demo", choices=["ok", "stop"], help="practice on a local fake form (no internet, nothing sent)")
    p.add_argument(
        "--sans-cv",
        "--no-cv",
        dest="no_cv",
        action="store_true",
        help="do not attach the CV (some ATS upload the file for parsing as soon as it is attached)",
    )
    p.add_argument(
        "--wait-close", action="store_true", help="no terminal prompt: stay open until the browser is closed"
    )
    p.add_argument("--mark-prepared", action="store_true", help="mark successfully pre-filled GO offers as 'prepared'")
    p.add_argument("--max-tabs", type=int, default=15)
    p.add_argument(
        "--tabs", action="store_true", help="open in new tabs of your Chrome; the Fitly extension fills them"
    )
    args = p.parse_args(argv)

    targets = []
    if args.demo:
        name = "formulaire_entrainement.html" if args.demo == "ok" else "formulaire_entrainement_stop.html"
        targets = [
            {
                "id": None,
                "title": "EXERCICE DemoCorp",
                "company": "DemoCorp",
                "url": (paths.DEMO_DIR / name).resolve().as_uri(),
                "recommended_cv": args.cv or "tech_fr",
            }
        ]
        print("MODE EXERCICE : formulaire fictif local, aucune entreprise, aucun envoi possible.")
    ids = [int(x) for x in (args.jobs or "").split(",") if x.strip()] + ([args.job] if args.job else [])
    if ids:
        targets += job_targets(ids)
        if args.cv and len(targets) == 1:
            targets[0]["recommended_cv"] = args.cv
    if args.url:
        targets.append({"id": None, "title": None, "company": None, "url": args.url, "recommended_cv": args.cv})
    if not targets:
        raise SystemExit("Give --job, --jobs, --url or --demo ok")
    if args.tabs and all(t.get("id") is not None for t in targets):
        urls = open_tabs_for_extension([t["id"] for t in targets][: args.max_tabs], no_cv=args.no_cv)
        print(f"{len(urls)} onglet(s) ouvert(s) dans Chrome : l'extension Fitly les pré-remplit.")
        return 0
    if len(targets) > args.max_tabs:
        print(f"{len(targets)} offres demandées : seules les {args.max_tabs} premières sont ouvertes.")
        targets = targets[: args.max_tabs]
    for t in targets:
        t["url"] = form_url(t["url"])

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit('Playwright is not installed. Run: pip install -e ".[browser]"') from None
    from fitly.apply.browser_launcher import launch, open_user_window

    answers = load_answers(args.answers)
    summaries = []
    with sync_playwright() as pw:
        attached = False
        if os.environ.get("FITLY_HEADLESS") == "1":  # automated tests
            browser = launch(pw, headless=True)
            context = browser.new_context()
        else:
            try:
                browser, context = open_user_window(pw)
                attached = True  # Chrome window "Fitly", new tabs
            except Exception as exc:
                print("Fenêtre Chrome Fitly indisponible :", exc, "→ fenêtre temporaire")
                browser = launch(pw, headless=False)
                context = browser.new_context(no_viewport=True)
        for t in targets:
            page = context.new_page()
            s = prefill_page(page, t, answers, args.no_cv)
            summaries.append(s)
            print_summary(s)
            if args.mark_prepared and s.get("status") == "prefilled" and t.get("id") is not None:
                try:
                    from fitly.tracking import tracker

                    tracker.set_status(t["id"], "prepared", note="formulaire pré-rempli", db_path=current_db())
                except Exception as exc:
                    print("    (statut non mis à jour :", exc, ")")
        out_dir().mkdir(parents=True, exist_ok=True)
        (out_dir() / "last_run.json").write_text(
            json.dumps(
                {"at": datetime.now(UTC).isoformat(timespec="seconds"), "results": summaries},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            "\nRIEN N'A ÉTÉ ENVOYÉ. Un onglet par offre : relis, complète, puis clique TOI-MÊME sur le bouton d'envoi du site."
        )
        print("Ensuite, dans Fitly : « J'ai envoyé » pour chaque candidature réellement envoyée.")
        if attached:
            for pg in list(context.pages):  # drop the empty start-up tab, keep everything else
                try:
                    if pg.url in ("about:blank", "chrome://newtab/") and len(context.pages) > 1:
                        pg.close()
                except Exception:
                    pass
            print("Les onglets restent ouverts dans la fenêtre Chrome « Fitly ». Tu peux fermer ce terminal.")
            return 0  # the Chrome window lives on its own; just disconnect
        if args.wait_close:
            print("Le navigateur reste ouvert jusqu'à ce que tu le fermes.")
            wait_until_closed(browser, context)
        else:
            input("\nAppuie sur Entrée pour fermer le navigateur...")
        try:
            browser.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
