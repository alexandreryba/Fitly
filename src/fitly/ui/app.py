"""Fitly — interface locale (Streamlit).

Lancer :  .\\scripts\\start_ui.ps1   (ou : python -m streamlit run src/fitly/ui/app.py)
Tout reste local. Aucune candidature n'est envoyée par l'application : tu cliques toi-même sur « Envoyer »
sur le site de l'entreprise, puis tu l'enregistres ici.

This script lays out the page (sidebar, dashboard, tabs); each tab lives in ``fitly/ui/tabs/``.
"""

from __future__ import annotations

import streamlit as st

from fitly import paths
from fitly.review import kpis
from fitly.scoring import company_quota
from fitly.ui import sidebar
from fitly.ui.offer_rows import track_lang
from fitly.ui.shared import applications, queue
from fitly.ui.tabs import applications as tab_applications
from fitly.ui.tabs import followup, my_info, nogo, offers, reserve, stats

st.set_page_config(page_title="Fitly", page_icon="🧭", layout="wide")

if "flash" in st.session_state:
    msg, icon = st.session_state.pop("flash")
    st.toast(msg, icon=icon)

with st.sidebar:
    sidebar.render()

# ---------------------------------------------------------------- dashboard
queue_all = queue()
k = kpis.compute(paths.db_path(), queue=queue_all)
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
to_review = [
    r
    for r in queue_all
    if r["application_status"] == "not_started" and r["quota"] != "reserve" and (r["score"] or 0) >= kpis.RELEVANT
]
tl = [track_lang(r["recommended_cv"]) for r in to_review]
c2 = st.columns(6)
c2[0].metric(
    "Tech à trier", sum(t == "Tech" for t, _ in tl), help="Offres pertinentes à trier, CV technique recommandé"
)
c2[1].metric(
    "Business à trier", sum(t == "Business" for t, _ in tl), help="Offres pertinentes à trier, CV business recommandé"
)
c2[2].metric("FR à trier", sum(lg == "FR" for _, lg in tl), help="Annonce en français (CV FR recommandé)")
c2[3].metric("EN à trier", sum(lg == "EN" for _, lg in tl), help="Annonce en anglais (CV EN recommandé)")
c2[4].metric(
    "En réserve",
    k["reserve_relevant"],
    help="Offres pertinentes mises de côté par le quota par entreprise (onglet Réserve)",
)
c2[5].metric("GO / NO-GO", f"{len(applications(('go', 'prepared')))} / {len(applications(('skipped',)))}")

tab_offres, tab_reserve, tab_cand, tab_nogo, tab_suivi, tab_stats, tab_infos = st.tabs(
    ["📋 Offres", "🗂️ Réserve", "✍️ Candidatures (GO)", "⛔ NO-GO", "📬 Suivi", "📈 Statistiques", "⚙️ Mes infos"]
)
quota = company_quota.rules()

with tab_offres:
    offers.render(queue_all, quota)
with tab_reserve:
    reserve.render(queue_all, quota)
with tab_cand:
    tab_applications.render(queue_all)
with tab_nogo:
    nogo.render()
with tab_suivi:
    followup.render()
with tab_stats:
    stats.render(k)
with tab_infos:
    my_info.render()
