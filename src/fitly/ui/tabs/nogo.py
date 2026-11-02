"""⛔ NO-GO: rejected offers, back to review or to GO in one click."""

from __future__ import annotations

import streamlit as st

from fitly.ui.shared import applications, apply_decisions, flash, undo_many


def render() -> None:
    nogo_rows = applications(("skipped",))
    if not nogo_rows:
        st.info("Aucune offre en NO-GO.")
        return
    st.caption(f"{len(nogo_rows)} offre(s) écartée(s). Coche, puis remets-les à trier ou en GO si tu changes d'avis.")
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
