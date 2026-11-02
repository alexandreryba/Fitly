"""📈 Statistiques: results of the applications and coverage of the sources."""

from __future__ import annotations

from typing import Any

import streamlit as st

from fitly.ui.shared import STATUS_FR


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f} %"


def render(k: dict[str, Any]) -> None:
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
