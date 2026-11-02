"""Sidebar: collection and maintenance actions, state of the sources, manual offers, exports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st

from fitly import config, paths
from fitly.review import kpis, review_queue
from fitly.tracking import tracker
from fitly.ui.shared import flash

SOURCE_ICONS = {"ok": "✅", "empty": "⚠️", "error": "❌", "no_key": "🔑", "disabled": "⏸️"}


@st.cache_resource(show_spinner=False)
def capture_receiver() -> Any:
    """Receiver for the 'Ajouter à Fitly' browser extension; started once with the interface."""
    from fitly.apply import capture_server

    return capture_server.start_in_background()


def render_sources_panel() -> None:
    """Every data source, offers collected / relevant at the last collection, and its health."""
    from fitly.sources import source_status

    db = paths.db_path()
    report = source_status.load(db)
    if report is None:
        try:
            report = source_status.rebuild_from_last_run(db_path=db)
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

    man = manual_offers.live_status(db, report)
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


def render() -> None:
    db = paths.db_path()
    out = paths.work_dir()  # review files go next to the database (sandbox copy stays separate)
    st.header("🧭 Fitly")
    if paths.is_sandbox():
        st.warning(f"BAC À SABLE — copie : {db}. Rien n'affecte ta vraie base.")
    if os.environ.get("FITLY_NO_CAPTURE") != "1":
        rx = capture_receiver()
        st.caption(
            "🧩 Extension « Ajouter à Fitly » : "
            + (
                "prête (127.0.0.1:8765)"
                if rx
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
                cwd=paths.ROOT,
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
            r = employer_match.check(db_path=db)
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
        r = tracker.rescore(
            config.load("preferences.json"), config.load("profile.json"), config.load("cv_profiles.json"), db_path=db
        )
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
            imported = manual_offers.import_file(db_path=db)
            flash(
                f"{imported['read']} offre(s) lue(s), {imported['new']} nouvelle(s) dans Fitly"
                + (f" · {len(imported['problems'])} ligne(s) incomplète(s)" if imported["problems"] else ""),
                "📥",
            )
            st.session_state["manual_problems"] = imported["problems"]
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

        xlsx, n = export_offers.write(db_path=db)
        st.session_state["offers_xlsx"] = str(xlsx)
        flash(f"{n} offres exportées dans {xlsx.name}", "📊")
        st.rerun()
    xlsx_path = Path(st.session_state.get("offers_xlsx") or (Path(db).parent / "Fitly_offres.xlsx"))
    if xlsx_path.exists():
        st.download_button(
            "⬇️ Télécharger Fitly_offres.xlsx",
            xlsx_path.read_bytes(),
            file_name=xlsx_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    if st.button("📄 Exporter CSV + rapport HTML", width="stretch"):
        from fitly.review import export_review, report_html

        review_queue.main(db=db, out=out / "review_queue.json")
        export_review.main(inp=out / "review_queue.json", out=out / "review_queue.csv")
        report_html.main(db=db, out=out / "morning_review.html")
        flash(f"review_queue.csv et morning_review.html régénérés dans {out}")
        st.rerun()
    if st.session_state.get("last_output"):
        with st.expander("Journal de la dernière collecte"):
            st.code(st.session_state["last_output"], language=None)
    st.divider()
    st.caption("Aucune candidature n'est envoyée automatiquement. La soumission finale reste manuelle.")
