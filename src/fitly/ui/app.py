"""Fitly — interface locale (Streamlit).

Lancer :  .\\scripts\\start_ui.ps1   (ou : python -m streamlit run src/fitly/ui/app.py)
Tout reste local. Aucune candidature n'est envoyée par l'application : tu cliques toi-même sur « Envoyer »
sur le site de l'entreprise, puis tu l'enregistres ici.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

from fitly import config, paths
from fitly.apply import ai_answers
from fitly.apply.candidate_pack import render as _render_pack
from fitly.review import kpis, review_queue
from fitly.scoring import company_quota
from fitly.storage import connect
from fitly.tracking import tracker
from fitly.ui.labels import source_label

ROOT = paths.ROOT
DB = paths.db_path()
SANDBOX = paths.is_sandbox()
OUT = paths.work_dir()  # review files go next to the database (sandbox copy stays separate)
STATUS_FR = {
    "not_started": "à trier",
    "go": "GO",
    "skipped": "NO-GO",
    "prepared": "préparée",
    "submitted": "envoyée",
    "followed_up": "relancée",
    "interview": "entretien",
    "offer": "offre",
    "rejected": "refus",
    "withdrawn": "abandon",
    "no_response": "sans réponse",
}
TRACKED = ("submitted", "followed_up", "interview", "offer", "rejected", "withdrawn", "no_response")

st.set_page_config(page_title="Fitly", page_icon="🧭", layout="wide")


def _db_stamp():
    """Changes whenever the database file is written: used to invalidate the cached queue."""
    try:
        st_ = os.stat(DB)
        prefs = os.stat(config.path("preferences.json")).st_mtime_ns  # excluded companies live there
        return (st_.st_mtime_ns, st_.st_size, prefs)
    except OSError:
        return (0, 0, 0)


@st.cache_resource(show_spinner=False)
def capture_receiver():
    """Receiver for the 'Ajouter à Fitly' browser extension; started once with the interface."""
    from fitly.apply import capture_server

    return capture_server.start_in_background()


@st.cache_data(show_spinner=False, max_entries=4)
def cached_queue(db, stamp):
    return review_queue.build(db)


cfg = config.load


def job_row(job_id):
    con = connect(DB)
    try:
        j = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        a = con.execute("SELECT * FROM applications WHERE job_id=?", (job_id,)).fetchone()
        ev = [
            dict(e)
            for e in con.execute("SELECT event_type,event_at,details FROM events WHERE job_id=? ORDER BY id", (job_id,))
        ]
    finally:
        con.close()
    return (dict(j) if j else None), (dict(a) if a else {}), ev


def applications(statuses):
    con = connect(DB)
    try:
        marks = ",".join("?" * len(statuses))
        rows = con.execute(
            f"""SELECT a.*, j.title, j.company, j.location, j.url, j.score, j.recommended_cv
                               FROM applications a JOIN jobs j ON j.id=a.job_id
                               WHERE a.status IN ({marks}) ORDER BY a.last_action_at DESC""",
            statuses,
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def render_pack(job, app, events):
    """Candidate pack with headings shrunk to fit inside an expander."""
    return re.sub(
        r"^(#+) ", lambda m: "#" * min(6, len(m.group(1)) + 3) + " ", _render_pack(job, app, events), flags=re.M
    )


def apply_decisions(job_ids, decision, note=None, cv=None):
    """GO/NO-GO for several offers at once; returns how many were recorded. `cv` (GO only): the CV chosen
    for these applications, used by the pre-fill instead of each offer's recommended CV."""
    for jid in job_ids:
        if cv and decision == "go":
            tracker.set_status(jid, "go", note=note or None, cv=cv, db_path=DB)
        else:
            tracker.decide(jid, decision, reason=note or None, db_path=DB)
    return len(job_ids)


def track_lang(cv_id):
    """('Tech'|'Business', 'FR'|'EN') from the recommended CV id, e.g. tech_fr."""
    track, _, lang = str(cv_id or "tech_fr").partition("_")
    return ("Business" if track == "business" else "Tech"), (lang or "fr").upper()


def published(r):
    """Posting date (YYYY-MM-DD), else the day Fitly first saw the offer."""
    return (r.get("posted_at") or str(r.get("first_seen_at") or "")[:10]) or None


def undo_many(job_ids):
    for jid in job_ids:
        tracker.undo(jid, db_path=DB)
    return len(job_ids)


PREFILL_DIR = OUT / "prefill"


def launch_prefill(job_ids, attach_cv=True):
    """Open one tab per offer, pre-filled. Default: new tabs in the user's own Chrome, filled by the Fitly
    extension ("prefill_mode": "chrome_tab"); fallback "window": separate Chrome window driven by Playwright."""
    from fitly.sources import employer_match

    closed = employer_match.verify_before_opening(job_ids, DB)  # offer page gone: not opened, back out of GO
    for jid in closed:
        tracker.undo(jid, db_path=DB, backup=False)
    job_ids = [j for j in job_ids if j not in closed]
    st.session_state["prefill_closed"] = len(closed)
    if not job_ids:
        return
    if QUOTA_RULES().get("prefill_mode", "chrome_tab") == "chrome_tab":
        from fitly.apply import launch_review

        launch_review.open_tabs_for_extension(job_ids, no_cv=not attach_cv)
        return
    PREFILL_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "-u",
        "-m",
        "fitly.apply.launch_review",
        "--jobs",
        ",".join(str(i) for i in job_ids),
        "--wait-close",
        "--mark-prepared",
    ] + ([] if attach_cv else ["--sans-cv"])
    (PREFILL_DIR / "last_run.json").unlink(missing_ok=True)
    with open(PREFILL_DIR / "last_run.log", "w", encoding="utf-8") as log:  # the child keeps its own handle
        subprocess.Popen(
            args,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "FITLY_DB": DB},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def QUOTA_RULES():
    return config.load("application_rules.json", default={})


def prefill_report():
    p = PREFILL_DIR / "last_run.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


PREFILL_ICONS = {
    "prefilled": "✅",
    "stopped": "⛔",
    "no_form": "⚠️",
    "captcha": "🧩",
    "platform": "🧭",
    "closed": "🚫",
    "login": "🔐",
}


def render_prefill_results(results):
    for res in results:
        icon = PREFILL_ICONS.get(res.get("status"), "❌")
        extra = ""
        if res.get("remaining_required"):
            extra += " · à compléter : " + ", ".join(x.split(chr(10))[0][:40] for x in res["remaining_required"][:4])
        if res.get("reason"):
            extra += f" · {res['reason']}"
        st.markdown(
            f"{icon} **{res.get('company') or ''}** — {res.get('title') or ''} : "
            f"{res.get('filled', 0)} champ(s) rempli(s){extra}"
        )


SOURCE_ICONS = {"ok": "✅", "empty": "⚠️", "error": "❌", "no_key": "🔑", "disabled": "⏸️"}


def render_sources_panel():
    """Sidebar: every data source, offers collected / relevant at the last collection, and its health."""
    from fitly.sources import source_status

    report = source_status.load(DB)
    if report is None:
        try:
            report = source_status.rebuild_from_last_run(db_path=DB)
        except (OSError, ValueError, KeyError):
            report = None
    if not report:
        st.caption("📡 Sources : état disponible après la prochaine collecte.")
        return
    rows = report["sources"]
    bad = [r for r in rows if r["status"] in ("error", "empty", "no_key")]
    ok = sum(r["status"] == "ok" for r in rows)
    title = f"📡 Sources : {ok}/{len(rows)} OK" + (f" · {len(bad)} à regarder" if bad else "")
    from fitly.sources import manual_offers

    man = manual_offers.live_status(DB, report)
    title += f" · 📥 {man['in_fitly'] + man['merged']} manuelle(s)"
    with st.expander(title, expanded=any(r["status"] == "error" for r in rows)):
        st.caption(
            f"Collecte du {report['at'][:16].replace('T', ' ')} UTC · "
            f"{sum(r['collected'] for r in rows)} offres collectées · "
            f"**{sum(r['relevant'] for r in rows)} pertinentes** (score ≥ 55)"
        )
        # Manual entries: live figures (not the morning snapshot), plus whether the morning run read the file.
        if not man["file"]:
            read_txt = "fichier Mes offres.xlsx absent"
        elif man["morning_read"] is None:
            read_txt = "⚠️ pas lu par la dernière collecte du matin"
        else:
            read_txt = (
                f"✅ lu par la collecte du {man['morning_at'][:16].replace('T', ' ')} UTC "
                f"({man['morning_read']} ligne(s))"
            )
        st.markdown(
            f"**📥 Ajout manuel** · {man['lines']} ligne(s) dans Mes offres.xlsx · "
            f"**{man['in_fitly']}** offre(s) dans Fitly (**{man['relevant']}** pertinentes)"
            + (f" · {man['merged']} déjà connue(s) par une autre source (fusionnées)" if man["merged"] else "")
            + f" · {read_txt}"
        )
        for pb in man["problems"][:3]:
            st.caption(f"⚠️ {pb}")
        for r in rows:
            if r["status"] == "error":
                st.error(f"{r['label']} · {r['name']} : {r['error'][:120]}")
        missing = [r["label"] for r in rows if r["status"] == "no_key"]
        if missing:
            st.info(
                "🔑 Non configurées (clé API manquante) : "
                + ", ".join(missing)
                + ". Assistant : « scripts/windows/4 - Configurer les sources.cmd »."
            )
        types = sorted({r["label"] for r in rows})
        pick = st.selectbox("Plateforme", ["(toutes)", *types], key="src_type")
        shown = [r for r in rows if pick == "(toutes)" or r["label"] == pick]
        st.dataframe(
            [
                {
                    "": SOURCE_ICONS.get(r["status"], "?"),
                    "source": r["name"] if r["type"] not in source_status.SEARCH_KEYS else r["label"],
                    "plateforme": r["label"],
                    "collectées": r["collected"],
                    "pertinentes": r["relevant"],
                }
                for r in sorted(shown, key=lambda r: (-r["relevant"], -r["collected"]))
            ],
            hide_index=True,
            height=min(420, 38 + 35 * len(shown)),
        )
        st.caption("✅ offres lues · ⚠️ aucune offre (site vide ou adresse changée) · ❌ erreur · 🔑 clé API manquante")


# Daily selection, one section per job family (from the role each offer matched in preferences.json).
CATEGORIES = [
    ("🤖 AI / GenAI Engineer", ("ai engineer", "genai engineer", "applied ai engineer", "llm engineer")),
    ("🛠️ Data Engineer", ("data engineer", "ai data engineer")),
    ("🧠 Machine Learning Engineer", ("machine learning engineer", "mlops engineer")),
    ("🔬 Data Scientist", ("data scientist",)),
    ("🏛️ Architecte data / IA", ("architect",)),
    ("📐 Analytics Engineer", ("analytics engineer",)),
    ("📊 Data Analyst", ("data analyst",)),
    ("💼 Consultant data / IA", ("consultant",)),
    ("🧩 Produit data", ("product owner", "product manager")),
    ("📋 Business Analyst", ("business analyst",)),
]
OTHER_CATEGORY = "🗂️ Autres"
PREP_NOTE = (
    " · préparation : les onglets s'affichent tour à tour le temps d'être remplis, puis retour au premier "
    "(laisse Chrome faire, ~10 s par offre)"
)
# Every filter of the Offres tab (daily selection included), cleared by "Réinitialiser les filtres".
FILTER_KEYS = (
    "flt_score",
    "flt_new",
    "flt_company",
    "flt_text",
    "flt_period",
    "flt_tracks",
    "flt_langs",
    "flt_sources",
    "cat_score",
    "cat_age",
)


def category_of(r):
    role = str(r.get("matched_role") or "").lower()
    for name, keys in CATEGORIES:
        if any(k in role for k in keys):
            return name
    return OTHER_CATEGORY


def by_company(rows):
    """Offers of one family grouped by company (companies ordered by their best score, offers by score):
    all the offers of a company follow each other, so you see at a glance how many you apply to there."""
    groups = {}
    for r in sorted(rows, key=lambda r: -(r["score"] or 0)):
        groups.setdefault(r["company"], []).append(r)

    def rank(company):  # best offer and overall level both count: 95/90/85 beats 96/60
        scores = [r["score"] or 0 for r in groups[company]]
        return -(max(scores) + sum(scores) / len(scores)) / 2

    return [r for company in sorted(groups, key=rank) for r in groups[company]]


def first_batch(rows, size=10):
    """The first `size` offers, extended so a company is never split between two batches (10 -> 13 when the
    11th-13th are from the same company as the 10th)."""
    batch = list(rows[:size])
    for r in rows[size:]:
        if batch and r["company"] == batch[-1]["company"]:
            batch.append(r)
        else:
            break
    return batch


def render_daily_by_category(queue):
    """Morning view: every offer above the chosen score, grouped by job family; GO (and pre-fill) a whole
    family in one click, with one CV checked per family instead of offer by offer."""
    sel_cfg = QUOTA.get("daily_selection", {"min_score": 55, "max_age_days": 14})
    cvs = [c["id"] for c in cfg("cv_profiles.json")["cvs"]]
    with st.expander("⭐ Sélection du jour, par famille de postes", expanded=True):
        top = st.columns([2, 2, 2])
        floor = top[0].slider(
            "Score minimum de la sélection", 0, 100, int(sel_cfg.get("min_score", 55)), 5, key="cat_score"
        )
        ages = {"Toutes": None, "7 derniers jours": 7, "14 derniers jours": 14, "30 derniers jours": 30}
        age = top[1].selectbox("Publiées", list(ages), index=0, key="cat_age")
        cv_ok = top[2].checkbox("Joindre le CV au pré-remplissage", value=True, key="cat_cv_attach")
        since = (date.today() - timedelta(days=ages[age])).isoformat() if ages[age] else None
        pool = [r for r in queue if (r["score"] or 0) >= floor and (not since or (published(r) or "") >= since)]
        groups = {}
        for r in sorted(pool, key=lambda r: -(r["score"] or 0)):
            groups.setdefault(category_of(r), []).append(r)
        order = [n for n, _ in CATEGORIES if n in groups] + ([OTHER_CATEGORY] if OTHER_CATEGORY in groups else [])
        if not order:
            st.caption(f"Aucune offre à trier avec un score ≥ {floor}.")
            return
        size = int(sel_cfg.get("batch_size", 10))
        st.caption(
            f"**{len(pool)} offre(s) à trier** avec un score ≥ {floor}, en {len(order)} famille(s). "
            "Dans chaque famille, les offres d'une même entreprise se suivent. Décoche ce que tu ne veux pas, "
            f"vérifie le CV, puis lance par lots d'environ {size} (une entreprise n'est jamais coupée entre "
            "deux lots) : les offres lancées passent en GO et sortent de la liste, le lot suivant remonte. "
            "Rien n'est envoyé : tu relis et tu cliques toi-même sur « Envoyer »."
        )
        for i, name in enumerate(order):
            rows = by_company(groups[name])
            per_company = {}
            for r in rows:
                per_company[r["company"]] = per_company.get(r["company"], 0) + 1
            used = {}
            for r in rows:
                used[r["recommended_cv"] or "?"] = used.get(r["recommended_cv"] or "?", 0) + 1
            mix = " · ".join(f"{k} ×{v}" for k, v in sorted(used.items(), key=lambda x: -x[1]))
            with st.expander(
                f"{name} — {len(rows)} offre(s) dans {len(per_company)} entreprise(s) · CV conseillés : {mix}",
                expanded=False,
            ):
                edited = st.data_editor(
                    [
                        {
                            "✓": True,
                            "#": n + 1,
                            "entreprise": f"{r['company']} ({per_company[r['company']]})",
                            "poste": r["title"],
                            "score": int(r["score"] or 0),
                            "lieu": r["location"],
                            "publiée": published(r),
                            "CV conseillé": r["recommended_cv"],
                            "lien": r["url"],
                        }
                        for n, r in enumerate(rows)
                    ],
                    key=f"cat_rows_{i}",
                    hide_index=True,
                    width="stretch",
                    disabled=["#", "score", "entreprise", "poste", "lieu", "publiée", "CV conseillé", "lien"],
                    column_config={
                        "lien": st.column_config.LinkColumn("lien", display_text="ouvrir"),
                        "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d"),
                    },
                )
                chosen = [r for r, e in zip(rows, edited, strict=False) if e["✓"]]
                c = st.columns([2, 1, 1])
                main_cv = max(used, key=used.get)
                cv_choice = c[0].selectbox(
                    "CV pour toute la famille",
                    ["(CV conseillé de chaque offre)", *cvs],
                    index=(cvs.index(main_cv) + 1) if len(used) == 1 and main_cv in cvs else 0,
                    key=f"cat_cv_{i}",
                    help="Un seul CV pour toute la famille : plus besoin de vérifier offre par offre.",
                )
                cv = None if cv_choice.startswith("(") else cv_choice
                batch = first_batch(chosen, size)
                companies = len({r["company"] for r in batch})
                if c[1].button(
                    f"🚀 GO + pré-remplir les {len(batch)} premières",
                    key=f"cat_batch_{i}",
                    type="primary",
                    disabled=not chosen,
                    width="stretch",
                    help=f"{len(batch)} offre(s) de {companies} entreprise(s) : lot de {size}, complété pour ne "
                    "pas couper la dernière entreprise. Recommence pour le lot suivant.",
                ):
                    ids = [r["id"] for r in batch]
                    apply_decisions(ids, "go", f"sélection du jour · {name}", cv=cv)
                    launch_prefill(ids, attach_cv=cv_ok)
                    flash(
                        f"{len(ids)} offre(s) {name} ({companies} entreprise(s)) en GO · onglets ouverts dans ton Chrome"
                        + PREP_NOTE,
                        "🚀",
                    )
                    st.rerun()
                if c[2].button(
                    f"🚀 GO + pré-remplir tout ({len(chosen)})",
                    key=f"cat_fill_{i}",
                    disabled=not chosen,
                    width="stretch",
                    help="Tout passe en GO ; seul le premier lot s'ouvre maintenant (des dizaines d'onglets "
                    "à la fois saturent Chrome). Les suivants : ✍️ Candidatures → Ouvrir et pré-remplir.",
                ):
                    ids = [r["id"] for r in chosen]
                    apply_decisions(ids, "go", f"sélection du jour · {name}", cv=cv)
                    launch_prefill([r["id"] for r in batch], attach_cv=cv_ok)
                    flash(
                        f"{len(ids)} offre(s) {name} en GO · {len(batch)} onglet(s) ouvert(s) maintenant, les autres "
                        "dans ✍️ Candidatures" + PREP_NOTE,
                        "🚀",
                    )
                    st.rerun()


def daily_selection(rows, size, min_score, max_age_days):
    """Best fresh offers inside the per-company rule: the morning proposal."""
    since = (date.today() - timedelta(days=max_age_days)).isoformat()
    picks = [
        r
        for r in rows
        if (r["score"] or 0) >= min_score
        and (published(r) or "") >= since
        and r.get("availability") != "not_on_employer_site"
    ]
    # Offers with a direct employer form first (pre-fillable), then the ones relayed by a job board.
    from fitly.sources import employer_match

    return sorted(picks, key=lambda r: (employer_match.is_relay(r["url"], r["company"]), -(r["score"] or 0)))[:size]


def availability_label(r):
    a = r.get("availability")
    if a == "not_on_employer_site":
        return "⚠️ absente du site carrière"
    if a == "closed":
        return "🚫 expirée"
    from fitly.sources import employer_match

    if r.get("direct"):
        return "✅ lien employeur trouvé"
    return "🧭 via site d'emploi" if employer_match.is_relay(r["url"], r["company"]) else ""


def flash(msg, icon="✅"):
    closed = st.session_state.pop("prefill_closed", 0)
    if closed:
        msg += f" · {closed} offre(s) expirée(s) écartée(s) avant ouverture"
    st.session_state["flash"] = (msg, icon)


if "flash" in st.session_state:
    msg, icon = st.session_state.pop("flash")
    st.toast(msg, icon=icon)

# ---------------------------------------------------------------- sidebar: actions
with st.sidebar:
    st.header("🧭 Fitly")
    if SANDBOX:
        st.warning(f"BAC À SABLE — copie : {DB}. Rien n'affecte ta vraie base.")
    if os.environ.get("FITLY_NO_CAPTURE") != "1":
        _rx = capture_receiver()
        st.caption(
            "🧩 Extension « Ajouter à Fitly » : "
            + (
                "prête (127.0.0.1:8765)"
                if _rx
                else "réception déjà active dans une autre fenêtre Fitly, ou port 8765 occupé"
            )
        )
    run = kpis.last_run_summary()
    if run:
        st.caption(
            f"Dernière collecte : {run.get('collected', 0)} offres lues, {run.get('new', 0)} nouvelles, "
            f"{run.get('errors', 0)} erreur(s) de source."
        )
    render_sources_panel()
    if st.button(
        "🔄 Collecter les offres maintenant",
        width="stretch",
        help="Lit les sites d'offres publics configurés (lecture seule), puis met à jour la base.",
    ):
        with st.spinner("Collecte en cours…"):
            # Same environment: in the sandbox, FITLY_DB sends the collection to the copy.
            res = subprocess.run(
                [sys.executable, "-m", "fitly.morning"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        if res.returncode == 0:
            flash("Collecte terminée")
        elif res.returncode == 3:
            flash("Aucune source n'a répondu (réseau ?) — base inchangée", "⚠️")
        else:
            flash("Erreur pendant la collecte (voir le détail)", "❌")
        st.session_state["last_output"] = res.stdout[-4000:] + res.stderr[-2000:]
        st.rerun()
    if st.button(
        "🔎 Vérifier les offres (sites employeurs)",
        width="stretch",
        help="Cherche chaque offre relayée (LinkedIn, Indeed…) sur le site carrière de l'entreprise et "
        "écarte les offres expirées. Fait aussi chaque matin. 1 à 3 min.",
    ):
        from fitly.sources import employer_match

        with st.spinner("Vérification sur les sites carrière…"):
            r = employer_match.check(db_path=DB)
        flash(
            f"{r['checked']} offres vérifiées · {r['direct_found']} lien(s) employeur trouvé(s) · "
            f"{r['closed']} expirée(s) · {r['not_on_employer_site']} absente(s) du site carrière",
            "🔎",
        )
        st.rerun()
    if st.button(
        "🧮 Recalculer les scores",
        width="stretch",
        help="Après modification de preferences.json ou profile.json. Hors ligne ; base sauvegardée avant.",
    ):
        r = tracker.rescore(cfg("preferences.json"), cfg("profile.json"), cfg("cv_profiles.json"), db_path=DB)
        flash(f"{r['jobs']} offres recalculées ({r['score_changed']} scores modifiés)")
        st.rerun()
    from fitly.sources import manual_offers

    with st.expander("📥 Mes offres (ajout manuel)"):
        st.caption(
            "Une offre vue en naviguant (Welcome to the Jungle, site d'entreprise…) ? Ajoute une ligne dans "
            "**Mes offres.xlsx** (dossier Fitly) : Date collecte, Entreprise, Poste, Localisation, "
            "Publication, Lien. Importé chaque matin, ou tout de suite ici."
        )
        mo = st.columns(2)
        if mo[0].button("📂 Ouvrir le fichier", key="manual_open", width="stretch"):
            if not manual_offers.FILE.exists():
                manual_offers.create_template()
            os.startfile(str(manual_offers.FILE)) if hasattr(os, "startfile") else None
        if mo[1].button("📥 Importer", key="manual_import", width="stretch", disabled=not manual_offers.FILE.exists()):
            res = manual_offers.import_file(db_path=DB)
            flash(
                f"{res['read']} offre(s) lue(s), {res['new']} nouvelle(s) dans Fitly"
                + (f" · {len(res['problems'])} ligne(s) incomplète(s)" if res["problems"] else ""),
                "📥",
            )
            st.session_state["manual_problems"] = res["problems"]
            st.rerun()
        for pb in st.session_state.get("manual_problems", []):
            st.warning(pb)
    if st.button(
        "📊 Générer le fichier Excel des offres",
        width="stretch",
        help="Fitly_offres.xlsx : date de collecte, entreprise, poste, lieu, publication, lien, score… "
        "Régénéré aussi à chaque collecte.",
    ):
        from fitly.review import export_offers

        _xlsx, _n = export_offers.write(db_path=DB)
        st.session_state["offers_xlsx"] = str(_xlsx)
        flash(f"{_n} offres exportées dans {_xlsx.name}", "📊")
        st.rerun()
    _xlsx_path = Path(st.session_state.get("offers_xlsx") or (Path(DB).parent / "Fitly_offres.xlsx"))
    if _xlsx_path.exists():
        st.download_button(
            "⬇️ Télécharger Fitly_offres.xlsx",
            _xlsx_path.read_bytes(),
            file_name=_xlsx_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    if st.button("📄 Exporter CSV + rapport HTML", width="stretch"):
        from fitly.review import export_review, report_html

        review_queue.main(db=DB, out=OUT / "review_queue.json")
        export_review.main(inp=OUT / "review_queue.json", out=OUT / "review_queue.csv")
        report_html.main(db=DB, out=OUT / "morning_review.html")
        flash(f"review_queue.csv et morning_review.html régénérés dans {OUT}")
        st.rerun()
    if st.session_state.get("last_output"):
        with st.expander("Journal de la dernière collecte"):
            st.code(st.session_state["last_output"], language=None)
    st.divider()
    st.caption("Aucune candidature n'est envoyée automatiquement. La soumission finale reste manuelle.")

# ---------------------------------------------------------------- dashboard
_queue_all = cached_queue(DB, _db_stamp())
k = kpis.compute(DB, queue=_queue_all)
st.title("Tableau de bord")
c = st.columns(6)
c[0].metric(
    "Nouvelles offres pertinentes",
    k["new_offers"],
    help=f"Score ≥ 55, vues pour la première fois lors de la dernière collecte ({k['new_all']} nouvelles au total, tous métiers confondus)",
)
c[1].metric("Offres pertinentes", k["relevant_offers"], help="Score ≥ 55")
c[2].metric("Doublons écartés", k["duplicates_last_run"], help="Lors de la dernière collecte")
c[3].metric(
    "Via ATS (à trier)",
    k["automatable_to_review"],
    help="Offres pertinentes à trier dont le formulaire passe par un ATS",
)
c[4].metric(
    "Easy Apply manuels",
    k["manual_easy_apply_to_review"],
    help="Offres LinkedIn (alertes e-mail) : Easy Apply ou site carrière à vérifier",
)
c[5].metric("Relances dues", k["followups_due"])
_to_review = [
    r
    for r in _queue_all
    if r["application_status"] == "not_started" and r["quota"] != "reserve" and (r["score"] or 0) >= kpis.RELEVANT
]
_tl = [track_lang(r["recommended_cv"]) for r in _to_review]
c2 = st.columns(6)
c2[0].metric(
    "Tech à trier", sum(t == "Tech" for t, _ in _tl), help="Offres pertinentes à trier, CV technique recommandé"
)
c2[1].metric(
    "Business à trier", sum(t == "Business" for t, _ in _tl), help="Offres pertinentes à trier, CV business recommandé"
)
c2[2].metric("FR à trier", sum(lg == "FR" for _, lg in _tl), help="Annonce en français (CV FR recommandé)")
c2[3].metric("EN à trier", sum(lg == "EN" for _, lg in _tl), help="Annonce en anglais (CV EN recommandé)")
c2[4].metric(
    "En réserve",
    k["reserve_relevant"],
    help="Offres pertinentes mises de côté par le quota par entreprise (onglet Réserve)",
)
c2[5].metric("GO / NO-GO", f"{len(applications(('go', 'prepared')))} / {len(applications(('skipped',)))}")

tab_offres, tab_reserve, tab_cand, tab_nogo, tab_suivi, tab_stats, tab_infos = st.tabs(
    ["📋 Offres", "🗂️ Réserve", "✍️ Candidatures (GO)", "⛔ NO-GO", "📬 Suivi", "📈 Statistiques", "⚙️ Mes infos"]
)
QUOTA = company_quota.rules()

# ---------------------------------------------------------------- 1. offers + GO/NO-GO
with tab_offres:
    queue = [r for r in _queue_all if r["application_status"] == "not_started" and r["quota"] != "reserve"]
    st.caption(
        f"Par entreprise : jusqu'à **{QUOTA['max_offers_per_company']} offres** si ce sont des variantes du "
        f"même poste (senior, secteur, ville…), et au plus **{QUOTA['max_distinct_roles_per_company']} métiers "
        f"différents**. Tes GO et candidatures des {QUOTA['company_window_days']} derniers jours comptent. "
        "Le surplus est dans 🗂️ Réserve et remonte ici dès qu'une place se libère (un NO-GO ne prend pas de place)."
    )
    render_daily_by_category(queue)
    h = st.columns([5, 1])
    h[0].markdown("**Toutes les offres à trier**")
    if h[1].button(
        "↺ Réinitialiser les filtres",
        key="reset_filters",
        width="stretch",
        help="Remet le score, l'entreprise, la recherche, les dates, types, langues et sources par défaut.",
    ):
        for k_ in FILTER_KEYS:
            st.session_state.pop(k_, None)
        st.rerun()
    f = st.columns([1, 1, 1, 2])
    min_score = f[0].slider("Score minimum", 0, 100, 55, 5, key="flt_score")
    only_new = f[1].checkbox("Nouvelles uniquement", key="flt_new")
    companies = sorted({r["company"] for r in queue})
    company = f[2].selectbox("Entreprise", ["(toutes)", *companies], key="flt_company")
    text = f[3].text_input("Recherche (titre, lieu, raison du score)", key="flt_text")
    g = st.columns([1, 1, 1, 2])
    all_sources = sorted({source_label(r.get("source"), r.get("via")) for r in queue})
    periods = {
        "Toutes": None,
        "Dernières 24 h": 1,
        "3 derniers jours": 3,
        "7 derniers jours": 7,
        "14 derniers jours": 14,
        "30 derniers jours": 30,
    }
    period = g[0].selectbox(
        "Publiée",
        list(periods),
        key="flt_period",
        help="Date de publication de l'offre ; à défaut, date à laquelle Fitly l'a vue.",
    )
    tracks = g[1].multiselect("Type", ["Tech", "Business"], default=["Tech", "Business"], key="flt_tracks")
    langs = g[2].multiselect("Langue", ["FR", "EN"], default=["FR", "EN"], key="flt_langs")
    sources = g[3].multiselect(
        "Source (d'où vient l'offre)",
        all_sources,
        default=all_sources,
        key="flt_sources",
        help="Site carrière de l'entreprise (ATS), alerte e-mail ou agrégateur.",
    )
    since = (date.today() - timedelta(days=periods[period])).isoformat() if periods[period] else None
    rows = [
        r
        for r in queue
        if (r["score"] or 0) >= min_score
        and (not only_new or r.get("freshness") == "new")
        and (company == "(toutes)" or r["company"] == company)
        and (not since or (published(r) or "") >= since)
        and track_lang(r["recommended_cv"])[0] in tracks
        and track_lang(r["recommended_cv"])[1] in langs
        and source_label(r.get("source"), r.get("via")) in sources
        and (not text or text.lower() in f"{r['title']} {r['location']} {r['why']}".lower())
    ]
    tl = [track_lang(r["recommended_cv"]) for r in rows]
    st.caption(
        f"{len(rows)} offre(s) à trier · Tech {sum(t == 'Tech' for t, _ in tl)} · Business "
        f"{sum(t == 'Business' for t, _ in tl)} · FR {sum(lg == 'FR' for _, lg in tl)} · "
        f"EN {sum(lg == 'EN' for _, lg in tl)}. Coche une ou plusieurs lignes, puis GO ou NO-GO "
        "(réversible depuis les onglets GO et NO-GO)."
    )
    _in_reserve = sum(
        1
        for r in _queue_all
        if r["application_status"] == "not_started" and r["quota"] == "reserve" and (r["score"] or 0) >= min_score
    )
    if _in_reserve:
        st.caption(
            f"➕ {_in_reserve} autre(s) offre(s) avec ce score sont dans l'onglet **🗂️ Réserve** : règle « 5 offres "
            "et 3 métiers maximum par entreprise » (les grands groupes publient souvent des centaines d'offres)."
        )
    table = [
        {
            "id": r["id"],
            "score": int(r["score"] or 0),
            "new": "🆕" if r.get("freshness") == "new" else "",
            "publiée": published(r),
            "entreprise": r["company"],
            "poste": r["title"],
            "lieu": r["location"],
            "type": track_lang(r["recommended_cv"])[0],
            "langue": track_lang(r["recommended_cv"])[1],
            "métier": r["role_core"],
            "famille": r["family"],
            "CV": r["recommended_cv"],
            "pourquoi": r["why"],
            "source": source_label(r.get("source"), r.get("via")),
            "dispo": availability_label(r),
            "lien": r["url"],
        }
        for r in rows
    ]
    event = st.dataframe(
        table,
        key="offers_table",
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        height=min(560, 38 + 35 * max(1, len(table))),
        column_config={
            "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d"),
            "new": st.column_config.TextColumn("new", width="small"),
            "publiée": st.column_config.DateColumn("publiée", format="DD/MM/YYYY", width="small"),
            "lien": st.column_config.LinkColumn("lien", display_text="ouvrir"),
            "pourquoi": st.column_config.TextColumn("pourquoi", width="large"),
        },
    )
    selected = [table[i]["id"] for i in (event.selection.rows if event else []) if i < len(table)]
    d = st.columns([3, 1, 1])
    note = d[0].text_input("Note (optionnelle, appliquée à la sélection)", key="decision_note")
    go = d[1].button(
        f"✅ GO ({len(selected)})", key="offers_go", type="primary", disabled=not selected, width="stretch"
    )
    nogo = d[2].button(f"⛔ NO-GO ({len(selected)})", key="offers_nogo", disabled=not selected, width="stretch")
    if go or nogo:
        n = apply_decisions(selected, "go" if go else "no", note)
        flash(f"{n} offre(s) passée(s) en {'GO' if go else 'NO-GO'}")
        st.session_state.pop("offers_table", None)
        st.rerun()

    if rows:
        with st.expander("🔎 Détail d'une offre"):
            pick = st.selectbox(
                "Offre",
                rows,
                format_func=lambda r: f"[{int(r['score'] or 0)}] {r['title']} — {r['company']} ({r['location']})",
            )
            j, a, ev = job_row(pick["id"])
            st.markdown(render_pack(j, a, ev))

# ---------------------------------------------------------------- 1b. reserve (company quota overflow)
with tab_reserve:
    reserve = [
        r
        for r in _queue_all
        if r["application_status"] == "not_started" and r["quota"] == "reserve" and (r["score"] or 0) >= kpis.RELEVANT
    ]
    st.caption(
        "Offres pertinentes (score ≥ 55) mises de côté par la règle par entreprise "
        f"({QUOTA['max_offers_per_company']} offres d'un même poste, {QUOTA['max_distinct_roles_per_company']} "
        "métiers différents au plus). Elles remontent seules dans 📋 Offres quand une place se libère. "
        "Tu peux aussi en forcer une si elle vaut mieux qu'une offre déjà retenue."
    )
    if not reserve:
        st.info("Réserve vide.")
    else:
        by_company = {}
        for r in reserve:
            by_company.setdefault(r["company"], []).append(r)
        st.markdown(f"**{len(reserve)} offre(s)** dans **{len(by_company)} entreprise(s)**")
        rtable = [
            {
                "id": r["id"],
                "score": int(r["score"] or 0),
                "entreprise": r["company"],
                "poste": r["title"],
                "famille": r["family"],
                "lieu": r["location"],
                "publiée": published(r),
                "raison": r["quota_reason"],
                "source": source_label(r.get("source"), r.get("via")),
                "lien": r["url"],
            }
            for c_, rs in sorted(by_company.items(), key=lambda kv: -len(kv[1]))
            for r in rs
        ]
        rev = st.dataframe(
            rtable,
            key="reserve_table",
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="multi-row",
            column_config={
                "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d"),
                "publiée": st.column_config.DateColumn("publiée", format="DD/MM/YYYY"),
                "lien": st.column_config.LinkColumn("lien", display_text="ouvrir"),
            },
        )
        rsel = [rtable[i]["id"] for i in (rev.selection.rows if rev else []) if i < len(rtable)]
        rb = st.columns([2, 1, 1])
        force = rb[0].checkbox("Je dépasse volontairement le quota pour cette entreprise", key="force_quota")
        if rb[1].button(
            f"✅ GO hors quota ({len(rsel)})", key="reserve_go", disabled=not (rsel and force), width="stretch"
        ):
            flash(f"{apply_decisions(rsel, 'go', 'GO hors quota')} offre(s) en GO hors quota")
            st.session_state.pop("reserve_table", None)
            st.rerun()
        if rb[2].button(f"⛔ NO-GO ({len(rsel)})", key="reserve_nogo", disabled=not rsel, width="stretch"):
            flash(f"{apply_decisions(rsel, 'no', 'écartée depuis la réserve')} offre(s) en NO-GO")
            st.session_state.pop("reserve_table", None)
            st.rerun()

# ---------------------------------------------------------------- 2. applications (GO -> sent)
with tab_cand:
    with st.expander("🧪 Tester le pré-remplissage sur des offres variées (sans les passer en GO)"):
        from fitly.apply import prefill_audit

        test_set = prefill_audit.diverse_selection(_queue_all, 50)
        done = st.session_state.get("test_offset", 0)
        st.caption(
            f"{len(test_set)} offres de {len({r['company'] for r in test_set})} entreprises, sur "
            f"{len({r['source'] for r in test_set})} plateformes carrière. Ouvertes par lots de 10 dans ton Chrome, "
            "pré-remplies par l'extension. **Rien n'est envoyé et aucun statut ne change** : ferme les onglets après "
            "avoir regardé."
        )
        tc = st.columns(2)
        batch = test_set[done : done + 10]
        if tc[0].button(
            f"🧪 Ouvrir les offres {done + 1} à {done + len(batch)}" if batch else "Toutes ouvertes",
            disabled=not batch,
            key="test_open",
            type="primary",
            width="stretch",
        ):
            from fitly.apply import launch_review
            from fitly.sources import employer_match

            ids = [r["id"] for r in batch]
            closed = employer_match.verify_before_opening(ids, DB)
            launch_review.open_tabs_for_extension([i for i in ids if i not in closed])
            st.session_state["test_offset"] = done + len(batch)
            flash(
                f"{len(ids) - len(closed)} onglet(s) ouvert(s)"
                + (f", {len(closed)} offre(s) expirée(s) écartée(s)" if closed else ""),
                "🧪",
            )
            st.rerun()
        if tc[1].button("↺ Recommencer la série", key="test_reset", width="stretch"):
            st.session_state["test_offset"] = 0
            st.rerun()
        report = prefill_report()
        tested = {str(r["id"]) for r in test_set[:done]}
        if report and tested:
            results = [r for r in report.get("results", []) if str(r.get("job_id")) in tested]
            ok = sum(r.get("status") == "prefilled" for r in results)
            st.markdown(
                f"**Résultat : {ok} pré-remplie(s) sur {len(results)} onglet(s) ayant répondu** "
                "(🔄 recharge la page pour actualiser)"
            )
            render_prefill_results(results)
    todo = applications(("go", "prepared"))
    if not todo:
        st.info("Aucune offre en GO. Valide des offres dans l'onglet « Offres ».")
    else:
        with st.container(border=True):
            st.markdown(
                "**🚀 Pré-remplir mes candidatures** — chaque offre s'ouvre dans un nouvel onglet de ton Chrome, "
                "déjà rempli. Tu relis, tu complètes ce qui manque et **tu cliques toi-même sur « Envoyer »**."
            )
            to_fill = st.multiselect(
                "Offres à ouvrir (15 au plus)",
                todo,
                default=todo[:15],
                key="fill_pick",
                format_func=lambda r: f"{r['company']} — {r['title']} ({STATUS_FR[r['status']]})",
            )
            pc = st.columns([2, 1])
            attach = pc[0].checkbox(
                "Joindre le CV recommandé",
                value=True,
                key="fill_cv",
                help="Certains sites téléversent le CV dès qu'il est joint (sans envoyer la candidature).",
            )
            if pc[1].button(
                f"🚀 Ouvrir et pré-remplir ({len(to_fill[:15])})",
                type="primary",
                disabled=not to_fill,
                key="fill_go",
                width="stretch",
            ):
                launch_prefill([r["job_id"] for r in to_fill[:15]], attach_cv=attach)
                flash("Onglets en cours d'ouverture dans ton Chrome" + PREP_NOTE, "🚀")
                st.rerun()
            report = prefill_report()
            if report:
                st.caption(f"Dernier pré-remplissage : {report['at'][:16].replace('T', ' ')} UTC")
                expected = json.loads((paths.EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))["version"]
                seen = {r.get("ext_version") or "ancienne" for r in report["results"]}
                if report["results"] and seen != {expected}:
                    st.error(
                        f"⚠️ Ton Chrome utilise une ancienne version de l'extension Fitly ({', '.join(sorted(seen))} "
                        f"au lieu de {expected}) : les corrections ne s'appliquent pas. Ouvre chrome://extensions "
                        "et clique sur 🔄 sur la carte Fitly, puis relance le pré-remplissage."
                    )
                render_prefill_results(report["results"])
            elif (PREFILL_DIR / "last_run.log").exists():
                st.caption("Pré-remplissage en cours… (clique sur 🔄 pour actualiser)")
                if st.button("🔄 Actualiser", key="fill_refresh"):
                    st.rerun()
        with st.container(border=True):
            st.markdown("**✅ J'ai envoyé ces candidatures** (après avoir cliqué « Envoyer » sur les sites)")
            sent_pick = st.multiselect(
                "Candidatures envoyées", todo, key="sent_pick", format_func=lambda r: f"{r['company']} — {r['title']}"
            )
            sc = st.columns([1, 1, 1])
            sent_day = sc[0].date_input("Date d'envoi", value=date.today(), format="DD/MM/YYYY", key="sent_day")
            sent_min = sc[1].number_input("Temps passé par candidature (min)", 0, 600, 10, 5, key="sent_min")
            if sc[2].button(f"Enregistrer ({len(sent_pick)})", disabled=not sent_pick, key="sent_go", width="stretch"):
                for r in sent_pick:
                    tracker.set_status(r["job_id"], "submitted", when=sent_day.isoformat(), db_path=DB)
                    kpis.log_effort(r["job_id"], sent_min, db_path=DB)
                flash(f"{len(sent_pick)} envoi(s) enregistré(s) — relances planifiées")
                st.rerun()
    for app_row in todo:
        jid = app_row["job_id"]
        j, a, ev = job_row(jid)
        label = f"{'🟢' if app_row['status'] == 'go' else '🟡'} [{int(j['score'] or 0)}] {j['title']} — {j['company']} · {STATUS_FR[app_row['status']]}"
        with st.expander(label):
            warn = company_quota.coherence_warning(jid, DB)
            if warn:
                st.warning(warn)
            prior = tracker.prior_applications(jid, db_path=DB)
            if prior:
                st.error(
                    "Candidature déjà envoyée pour un poste très proche : "
                    + "; ".join(f"{p['title']} ({str(p['applied_at'])[:10]})" for p in prior)
                )
            st.markdown(render_pack(j, a, ev))
            answers_md = ai_answers.OUT_DIR / f"answers_{jid}.md"
            with st.container(border=True):
                ai_cfg = ai_answers.load_config()
                st.markdown(
                    f"**Réponses préparées** · budget IA du mois : {ai_answers.spent_eur():.2f} € / "
                    f"{float(ai_cfg['monthly_cap_eur']):.0f} €"
                )
                qs = st.text_area("Questions du formulaire (une par ligne, optionnel)", key=f"q{jid}", height=80)
                if st.button("✨ Préparer message et réponses", key=f"ai{jid}"):
                    lang = "en" if str(j["recommended_cv"] or "").endswith("_en") else "fr"
                    with st.spinner("Rédaction…"):
                        draft = ai_answers.generate(j, cfg("profile.json"), questions=qs.splitlines(), language=lang)
                    ai_answers.save(jid, j, draft)
                    flash(
                        f"Brouillon prêt ({draft['source']}) — {draft.get('note', '')}",
                        "✨" if draft["source"] != "template" else "ℹ️",
                    )
                    st.rerun()
                if answers_md.exists():
                    st.markdown(
                        re.sub(
                            r"^(#+) ",
                            lambda m: "#" * min(6, len(m.group(1)) + 3) + " ",
                            answers_md.read_text(encoding="utf-8"),
                            flags=re.M,
                        )
                    )
            if st.button("🚀 Ouvrir et pré-remplir ce formulaire", key=f"fill1_{jid}"):
                launch_prefill([jid])
                flash("Onglet en cours d'ouverture dans ton Chrome", "🚀")
                st.rerun()
            st.caption(
                "Remplit uniquement tes informations validées, joint le CV et te laisse les questions propres "
                "à l'entreprise. Ne clique jamais sur « Envoyer »."
            )
            b = st.columns(3)
            if app_row["status"] == "go" and b[0].button("Marquer « préparée »", key=f"prep{jid}"):
                tracker.set_status(jid, "prepared", db_path=DB)
                flash("Candidature préparée")
                st.rerun()
            if b[1].button("⛔ Passer en NO-GO", key=f"drop{jid}"):
                tracker.set_status(jid, "skipped", note="abandon avant envoi", db_path=DB)
                flash("Offre passée en NO-GO")
                st.rerun()
            if b[2].button(
                "↩️ Remettre à trier", key=f"undo{jid}", help="Annule le GO : l'offre revient dans l'onglet Offres."
            ):
                tracker.undo(jid, db_path=DB)
                flash("Décision annulée, offre remise à trier", "↩️")
                st.rerun()
            with st.form(f"sent{jid}"):
                st.markdown("**J'ai déjà envoyé ma candidature moi-même → l'enregistrer**")
                st.caption(
                    "Fitly n'envoie rien : ce formulaire sert seulement à noter un envoi que tu as fait "
                    "sur le site de l'entreprise (date, CV, temps passé), pour planifier la relance."
                )
                cvs = [c["id"] for c in cfg("cv_profiles.json")["cvs"]]
                default_cv = a.get("cv_type") or j["recommended_cv"]
                fc = st.columns(3)
                when = fc[0].date_input("Date d'envoi", value=date.today(), format="DD/MM/YYYY")
                cv = fc[1].selectbox("CV envoyé", cvs, index=cvs.index(default_cv) if default_cv in cvs else 0)
                minutes = fc[2].number_input("Temps passé (min)", min_value=0, max_value=600, value=20, step=5)
                note = st.text_input("Note (contact, canal…)")
                force = st.checkbox("Poste différent malgré l'alerte de doublon", value=False) if prior else False
                if st.form_submit_button("✅ Enregistrer l'envoi", type="primary"):
                    if prior and not force:
                        st.error("Envoi non enregistré : coche la case si c'est bien un poste différent.")
                    else:
                        tracker.set_status(
                            jid, "submitted", note=note or None, when=when.isoformat(), cv=cv, db_path=DB
                        )
                        kpis.log_effort(jid, minutes, db_path=DB)
                        flash("Envoi enregistré — relance planifiée")
                        st.rerun()

# ---------------------------------------------------------------- 2b. NO-GO (reversible)
with tab_nogo:
    nogo_rows = applications(("skipped",))
    if not nogo_rows:
        st.info("Aucune offre en NO-GO.")
    else:
        st.caption(
            f"{len(nogo_rows)} offre(s) écartée(s). Coche, puis remets-les à trier ou en GO si tu changes d'avis."
        )
        ntable = [
            {
                "id": r["job_id"],
                "score": int(r["score"] or 0),
                "entreprise": r["company"],
                "poste": r["title"],
                "lieu": r["location"],
                "écartée le": str(r["last_action_at"] or "")[:10],
                "note": (r["notes"] or "").split(": ", 1)[-1],
                "lien": r["url"],
            }
            for r in nogo_rows
        ]
        nev = st.dataframe(
            ntable,
            key="nogo_table",
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="multi-row",
            column_config={
                "lien": st.column_config.LinkColumn("lien", display_text="ouvrir"),
                "score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d"),
            },
        )
        nsel = [ntable[i]["id"] for i in (nev.selection.rows if nev else []) if i < len(ntable)]
        nb = st.columns([1, 1, 3])
        if nb[0].button(f"↩️ Remettre à trier ({len(nsel)})", key="nogo_undo", disabled=not nsel, width="stretch"):
            flash(f"{undo_many(nsel)} offre(s) remise(s) à trier", "↩️")
            st.session_state.pop("nogo_table", None)
            st.rerun()
        if nb[1].button(f"✅ Repasser en GO ({len(nsel)})", key="nogo_go", disabled=not nsel, width="stretch"):
            flash(f"{apply_decisions(nsel, 'go')} offre(s) repassée(s) en GO")
            st.session_state.pop("nogo_table", None)
            st.rerun()

# ---------------------------------------------------------------- 3. follow-up
with tab_suivi:
    from fitly.tracking import mail_tracker

    _mail_path = mail_tracker.proposals_path(DB)
    try:
        _mail_props = json.loads(_mail_path.read_text(encoding="utf-8")) if _mail_path.exists() else []
    except ValueError:
        _mail_props = []
    if _mail_props:
        st.subheader(f"📬 Depuis tes e-mails ({len(_mail_props)} mise(s) à jour proposée(s))")
        st.caption(
            "Déduit de tes e-mails (confirmations de candidature, refus, invitations à un entretien). "
            "Décoche ce qui est faux, puis applique. Rien n'est modifié avant."
        )
        _labels = {"submitted": "📤 envoyée", "interview": "🗓️ entretien", "rejected": "❌ refus"}
        edited = st.data_editor(
            [
                {
                    "appliquer": True,
                    "date": p["date"],
                    "statut": _labels.get(p["status"], p["status"]),
                    "entreprise": p["company"],
                    "poste": p["title"],
                    "offre": f"#{p['job_id']}" if p["job_id"] else "nouvelle",
                    "e-mail": p["subject"],
                }
                for p in _mail_props
            ],
            key="mail_props",
            hide_index=True,
            width="stretch",
            disabled=["date", "statut", "entreprise", "poste", "offre", "e-mail"],
        )
        mc = st.columns(2)
        if mc[0].button(
            f"✅ Appliquer la sélection ({sum(r['appliquer'] for r in edited)})", type="primary", key="mail_apply"
        ):
            chosen = [p for p, r in zip(_mail_props, edited, strict=False) if r["appliquer"]]
            n = mail_tracker.apply(chosen, DB)
            _mail_path.unlink(missing_ok=True)
            flash(f"{n} candidature(s) mise(s) à jour depuis tes e-mails (base sauvegardée avant)", "📬")
            st.rerun()
        if mc[1].button("🗑️ Ignorer ces propositions", key="mail_drop"):
            _mail_path.unlink(missing_ok=True)
            st.rerun()
        st.divider()
    due = tracker.due_followups(db_path=DB)
    st.subheader(f"Relances dues ({len(due)})")
    sender = cfg("profile.json").get("name", "")
    for item in due:
        jid = item["job_id"]
        with st.expander(
            f"⏰ {item['company']} — {item['title']} · envoyée le {str(item['applied_at'])[:10]} · "
            f"{item['follow_up_count']} relance(s)"
        ):
            if item["suggested_action"] == "close_as_no_response":
                st.warning("Nombre maximal de relances atteint : clôture conseillée.")
            else:
                st.code(tracker.followup_draft(item, sender), language=None)
                st.caption("Modèle à compléter et envoyer toi-même (e-mail ou LinkedIn).")
            b = st.columns(2)
            if item["suggested_action"] != "close_as_no_response" and b[0].button("Relance envoyée", key=f"fu{jid}"):
                tracker.set_status(jid, "followed_up", db_path=DB)
                flash("Relance enregistrée")
                st.rerun()
            if b[1].button("Clôturer : sans réponse", key=f"nr{jid}"):
                tracker.set_status(jid, "no_response", db_path=DB)
                flash("Candidature clôturée")
                st.rerun()
    if not due:
        st.caption("Aucune relance due aujourd'hui.")

    st.subheader("Candidatures envoyées")
    sent = applications(TRACKED)
    if sent:
        st.dataframe(
            [
                {
                    "id": r["job_id"],
                    "statut": STATUS_FR.get(r["status"], r["status"]),
                    "entreprise": r["company"],
                    "poste": r["title"],
                    "envoyée": str(r["applied_at"] or "")[:10],
                    "CV": r["cv_type"],
                    "prochaine relance": str(r["next_follow_up_at"] or "")[:10],
                    "lien": r["url"],
                }
                for r in sent
            ],
            hide_index=True,
            width="stretch",
            column_config={"lien": st.column_config.LinkColumn("lien", display_text="ouvrir")},
        )
        with st.form("status_update"):
            st.markdown("**Mettre à jour une candidature** (réponse reçue, entretien, refus…)")
            fc = st.columns([3, 2, 2])
            pick = fc[0].selectbox(
                "Candidature",
                sent,
                format_func=lambda r: f"{r['company']} — {r['title']} ({STATUS_FR.get(r['status'])})",
            )
            new_status = fc[1].selectbox(
                "Nouveau statut",
                ["interview", "offer", "rejected", "withdrawn", "no_response", "followed_up"],
                format_func=lambda s: STATUS_FR[s],
            )
            when = fc[2].date_input("Date", value=date.today(), format="DD/MM/YYYY")
            note = st.text_input("Note")
            if st.form_submit_button("Mettre à jour"):
                tracker.set_status(pick["job_id"], new_status, note=note or None, when=when.isoformat(), db_path=DB)
                flash(f"Statut mis à jour : {STATUS_FR[new_status]}")
                st.rerun()
        with st.expander("↩️ Annuler une candidature enregistrée par erreur"):
            st.caption(
                "Retire l'enregistrement (statut, date d'envoi, temps passé, relances) et remet l'offre à trier. "
                "Ta base est sauvegardée avant. Cela n'a aucun effet chez l'employeur : Fitly n'y a rien envoyé."
            )
            wrong = st.selectbox(
                "Candidature",
                sent,
                key="undo_pick",
                format_func=lambda r: f"{r['company']} — {r['title']} ({STATUS_FR.get(r['status'])})",
            )
            sure = st.checkbox("Oui, cette candidature a été enregistrée par erreur", key="undo_sure")
            if st.button("Annuler cet enregistrement", disabled=not sure):
                tracker.undo(wrong["job_id"], db_path=DB)
                flash("Enregistrement annulé, offre remise à trier", "↩️")
                st.rerun()
    else:
        st.caption("Aucune candidature envoyée pour l'instant.")

# ---------------------------------------------------------------- 4. statistics
with tab_stats:

    def pct(x):
        return "—" if x is None else f"{x * 100:.0f} %"

    s = st.columns(4)
    s[0].metric("Candidatures envoyées", k["submitted"])
    s[1].metric("Taux de réponse", pct(k["response_rate"]))
    s[2].metric("Taux d'entretien", pct(k["interview_rate"]))
    s[3].metric(
        "Entretiens / heure de travail",
        "—" if k["interviews_per_hour"] is None else f"{k['interviews_per_hour']:.2f}",
        help=f"{k['hours_logged']} h saisies via « Temps passé ».",
    )
    s = st.columns(4)
    s[0].metric("Couverture : offres pertinentes", k["relevant_offers"])
    s[1].metric(
        "Taux d'automatisation",
        pct(k["automation_rate"]),
        help="Part des offres pertinentes à trier qui passent par un ATS (formulaire préparable).",
    )
    s[2].metric("Offres vues sur plusieurs sources", k["multi_source_offers"])
    s[3].metric("Offres connues", k["jobs_known"])
    if k["coverage_by_source"]:
        st.markdown("**Offres pertinentes par source**")
        st.bar_chart({src: [n] for src, n in k["coverage_by_source"].items()}, horizontal=True, stack=False)
    if k["statuses"]:
        st.markdown("**Candidatures par statut**")
        st.dataframe(
            [{"statut": STATUS_FR.get(s_, s_), "nombre": n} for s_, n in sorted(k["statuses"].items())], hide_index=True
        )

# ---------------------------------------------------------------- 6. my information (local files)
ANSWER_FIELDS = [
    ("first_name", "Prénom"),
    ("last_name", "Nom"),
    ("full_name", "Nom complet"),
    ("email", "E-mail"),
    ("phone", "Téléphone"),
    ("linkedin", "URL du profil LinkedIn"),
    ("location", "Ville"),
    ("country", "Pays (ex. France)"),
    ("current_company", "Entreprise actuelle (facultatif)"),
    ("salary_expectation", "Prétentions salariales (ex. 45 000 € brut/an)"),
    ("notice_period", "Préavis / disponibilité"),
    ("years_experience", "Années d'expérience"),
    ("work_authorization", "Autorisation de travail (ex. Oui, citoyen UE)"),
    ("sponsorship", "Besoin de sponsoring visa (ex. Non)"),
    ("github", "URL GitHub (facultatif)"),
    ("website", "Site / portfolio (facultatif)"),
    ("school", "École / université du dernier diplôme"),
    ("degree", "Diplôme (ex. Master Data Science)"),
    ("graduation_year", "Année d'obtention du diplôme"),
    ("english_level", "Niveau d'anglais (ex. Courant / C1)"),
    ("how_heard", "Comment as-tu connu l'offre (ex. Site carrière de l'entreprise)"),
    ("relocation", "Basé à Paris / mobilité (ex. Oui, basé à Paris)"),
]
with tab_infos:
    st.markdown(
        "Ces informations restent **sur ton PC**. Elles servent uniquement à pré-remplir les formulaires "
        "que tu ouvres toi-même (`launch_review.py`) et à rédiger les brouillons IA. "
        "Laisse vide ce que tu ne veux pas voir rempli automatiquement."
    )
    current = config.load("answer_library.json", default={})
    with st.form("answer_library"):
        st.subheader("Coordonnées pour les formulaires")
        cols = st.columns(2)
        values = {
            key: cols[i % 2].text_input(label, value=str(current.get(key) or ""), key=f"lib_{key}")
            for i, (key, label) in enumerate(ANSWER_FIELDS)
        }
        if st.form_submit_button("💾 Enregistrer mes coordonnées", type="primary"):
            data = {k: v.strip() for k, v in values.items() if v.strip()}
            config.save("answer_library.json", data)
            flash(f"{len(data)} champ(s) enregistré(s) dans config/local/answer_library.json")
            st.rerun()

    st.subheader("Entreprises où ne pas postuler")
    prefs_now = cfg("preferences.json")
    excl = st.text_area(
        "Une entreprise par ligne. Leurs offres n'apparaissent plus (ni dans la sélection du jour). "
        "« Acme Partners » écarte aussi « Acme Partners France » ; liste chaque entité séparément "
        "(« Globex Consulting » n'écarte pas « Globex »).",
        value="\n".join(prefs_now.get("excluded_companies", [])),
        height=140,
        key="excluded_companies",
    )
    if st.button("💾 Enregistrer les exclusions"):
        prefs_now["excluded_companies"] = [line.strip() for line in excl.splitlines() if line.strip()]
        config.save("preferences.json", prefs_now)
        flash(f"{len(prefs_now['excluded_companies'])} entreprise(s) écartée(s)")
        st.rerun()

    st.subheader("Faits vérifiés pour l'IA")
    facts = st.text_area(
        "config/local/candidate_facts.md : l'IA n'utilise que ce texte et ton profil. "
        "Remplace les [À COMPLÉTER] par tes vraies expériences.",
        value=config.read_text("candidate_facts.md"),
        height=320,
    )
    if st.button("💾 Enregistrer les faits"):
        config.save("candidate_facts.md", facts)
        flash("Faits enregistrés")
        st.rerun()

    st.subheader("Mes CV")
    for c_ in cfg("cv_profiles.json")["cvs"]:
        p_ = paths.project_path(c_["path"])
        st.markdown(
            f"{'✅' if p_.is_file() else '❌'} `{c_['id']}` → `{c_['path']}`"
            + ("" if p_.is_file() else " — à déposer dans ce dossier")
        )
