"""🗂️ Réserve: relevant offers set aside by the per-company quota (they come back on their own)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from fitly.review import kpis
from fitly.ui.labels import source_label
from fitly.ui.offer_rows import Row, published
from fitly.ui.shared import apply_decisions, flash


def render(queue_all: list[Row], quota: dict[str, Any]) -> None:
    reserve = [
        r
        for r in queue_all
        if r["application_status"] == "not_started" and r["quota"] == "reserve" and (r["score"] or 0) >= kpis.RELEVANT
    ]
    st.caption(
        "Offres pertinentes (score ≥ 55) mises de côté par la règle par entreprise "
        f"({quota['max_offers_per_company']} offres d'un même poste, {quota['max_distinct_roles_per_company']} "
        "métiers différents au plus). Elles remontent seules dans 📋 Offres quand une place se libère. "
        "Tu peux aussi en forcer une si elle vaut mieux qu'une offre déjà retenue."
    )
    if not reserve:
        st.info("Réserve vide.")
        return
    per_company: dict[str, list[Row]] = {}
    for r in reserve:
        per_company.setdefault(r["company"], []).append(r)
    st.markdown(f"**{len(reserve)} offre(s)** dans **{len(per_company)} entreprise(s)**")
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
        for _, rs in sorted(per_company.items(), key=lambda kv: -len(kv[1]))
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
