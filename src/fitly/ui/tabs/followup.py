"""📬 Suivi: updates found in your e-mails, follow-ups due, applications sent."""

from __future__ import annotations

import json
from datetime import date

import streamlit as st

from fitly import config, paths
from fitly.tracking import tracker
from fitly.ui.shared import STATUS_FR, TRACKED, applications, flash


def render_mail_proposals() -> None:
    """Status changes read from the e-mails, applied only once you have checked them."""
    from fitly.tracking import mail_tracker

    db = paths.db_path()
    mail_path = mail_tracker.proposals_path(db)
    try:
        mail_props = json.loads(mail_path.read_text(encoding="utf-8")) if mail_path.exists() else []
    except ValueError:
        mail_props = []
    if not mail_props:
        return
    st.subheader(f"📬 Depuis tes e-mails ({len(mail_props)} mise(s) à jour proposée(s))")
    st.caption(
        "Déduit de tes e-mails (confirmations de candidature, refus, invitations à un entretien). "
        "Décoche ce qui est faux, puis applique. Rien n'est modifié avant."
    )
    labels = {"submitted": "📤 envoyée", "interview": "🗓️ entretien", "rejected": "❌ refus"}
    edited = st.data_editor(
        [
            {
                "appliquer": True,
                "date": p["date"],
                "statut": labels.get(p["status"], p["status"]),
                "entreprise": p["company"],
                "poste": p["title"],
                "offre": f"#{p['job_id']}" if p["job_id"] else "nouvelle",
                "e-mail": p["subject"],
            }
            for p in mail_props
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
        chosen = [p for p, r in zip(mail_props, edited, strict=False) if r["appliquer"]]
        n = mail_tracker.apply(chosen, db)
        mail_path.unlink(missing_ok=True)
        flash(f"{n} candidature(s) mise(s) à jour depuis tes e-mails (base sauvegardée avant)", "📬")
        st.rerun()
    if mc[1].button("🗑️ Ignorer ces propositions", key="mail_drop"):
        mail_path.unlink(missing_ok=True)
        st.rerun()
    st.divider()


def render_followups() -> None:
    db = paths.db_path()
    due = tracker.due_followups(db_path=db)
    st.subheader(f"Relances dues ({len(due)})")
    sender = config.load("profile.json").get("name", "")
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
                tracker.set_status(jid, "followed_up", db_path=db)
                flash("Relance enregistrée")
                st.rerun()
            if b[1].button("Clôturer : sans réponse", key=f"nr{jid}"):
                tracker.set_status(jid, "no_response", db_path=db)
                flash("Candidature clôturée")
                st.rerun()
    if not due:
        st.caption("Aucune relance due aujourd'hui.")


def render_sent() -> None:
    """Applications sent: table, status update, undo of a mistaken record."""
    db = paths.db_path()
    st.subheader("Candidatures envoyées")
    sent = applications(TRACKED)
    if not sent:
        st.caption("Aucune candidature envoyée pour l'instant.")
        return
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
            tracker.set_status(pick["job_id"], new_status, note=note or None, when=when.isoformat(), db_path=db)
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
            tracker.undo(wrong["job_id"], db_path=db)
            flash("Enregistrement annulé, offre remise à trier", "↩️")
            st.rerun()


def render() -> None:
    render_mail_proposals()
    render_followups()
    render_sent()
