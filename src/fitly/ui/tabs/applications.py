"""✍️ Candidatures (GO): pre-fill the forms, prepare the answers, record what you sent yourself."""

from __future__ import annotations

import json
from datetime import date

import streamlit as st

from fitly import config, paths
from fitly.apply import ai_answers
from fitly.review import kpis
from fitly.scoring import company_quota
from fitly.tracking import tracker
from fitly.ui.offer_rows import Row
from fitly.ui.shared import (
    PREP_NOTE,
    STATUS_FR,
    applications,
    flash,
    job_row,
    launch_prefill,
    prefill_dir,
    prefill_report,
    render_pack,
    render_prefill_results,
    shrink_headings,
)


def render_prefill_test(queue_all: list[Row]) -> None:
    """Pre-fill tried on a varied set of offers, without changing their status."""
    with st.expander("🧪 Tester le pré-remplissage sur des offres variées (sans les passer en GO)"):
        from fitly.apply import prefill_audit

        test_set = prefill_audit.diverse_selection(queue_all, 50)
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
            closed = employer_match.verify_before_opening(ids, paths.db_path())
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


def render_bulk_actions(todo: list[Row]) -> None:
    """Pre-fill several GO offers at once, then record several applications as sent."""
    db = paths.db_path()
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
        elif (prefill_dir() / "last_run.log").exists():
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
                tracker.set_status(r["job_id"], "submitted", when=sent_day.isoformat(), db_path=db)
                kpis.log_effort(r["job_id"], sent_min, db_path=db)
            flash(f"{len(sent_pick)} envoi(s) enregistré(s) — relances planifiées")
            st.rerun()


def render_application(app_row: Row) -> None:
    """One GO offer: candidate pack, prepared answers, pre-fill, status buttons and the 'sent' form."""
    db = paths.db_path()
    jid = app_row["job_id"]
    j, a, ev = job_row(jid)
    assert j is not None  # the applications row joins its job
    label = f"{'🟢' if app_row['status'] == 'go' else '🟡'} [{int(j['score'] or 0)}] {j['title']} — {j['company']} · {STATUS_FR[app_row['status']]}"
    with st.expander(label):
        warn = company_quota.coherence_warning(jid, db)
        if warn:
            st.warning(warn)
        prior = tracker.prior_applications(jid, db_path=db)
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
                    draft = ai_answers.generate(
                        j, config.load("profile.json"), questions=qs.splitlines(), language=lang
                    )
                ai_answers.save(jid, j, draft)
                flash(
                    f"Brouillon prêt ({draft['source']}) — {draft.get('note', '')}",
                    "✨" if draft["source"] != "template" else "ℹ️",
                )
                st.rerun()
            if answers_md.exists():
                st.markdown(shrink_headings(answers_md.read_text(encoding="utf-8")))
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
            tracker.set_status(jid, "prepared", db_path=db)
            flash("Candidature préparée")
            st.rerun()
        if b[1].button("⛔ Passer en NO-GO", key=f"drop{jid}"):
            tracker.set_status(jid, "skipped", note="abandon avant envoi", db_path=db)
            flash("Offre passée en NO-GO")
            st.rerun()
        if b[2].button(
            "↩️ Remettre à trier", key=f"undo{jid}", help="Annule le GO : l'offre revient dans l'onglet Offres."
        ):
            tracker.undo(jid, db_path=db)
            flash("Décision annulée, offre remise à trier", "↩️")
            st.rerun()
        with st.form(f"sent{jid}"):
            st.markdown("**J'ai déjà envoyé ma candidature moi-même → l'enregistrer**")
            st.caption(
                "Fitly n'envoie rien : ce formulaire sert seulement à noter un envoi que tu as fait "
                "sur le site de l'entreprise (date, CV, temps passé), pour planifier la relance."
            )
            cvs = [c["id"] for c in config.load("cv_profiles.json")["cvs"]]
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
                    tracker.set_status(jid, "submitted", note=note or None, when=when.isoformat(), cv=cv, db_path=db)
                    kpis.log_effort(jid, minutes, db_path=db)
                    flash("Envoi enregistré — relance planifiée")
                    st.rerun()


def render(queue_all: list[Row]) -> None:
    render_prefill_test(queue_all)
    todo = applications(("go", "prepared"))
    if not todo:
        st.info("Aucune offre en GO. Valide des offres dans l'onglet « Offres ».")
    else:
        render_bulk_actions(todo)
    for app_row in todo:
        render_application(app_row)
