"""⚙️ Mes infos: form answers, excluded companies, facts for the AI and CVs (all in local files)."""

from __future__ import annotations

import streamlit as st

from fitly import config, paths
from fitly.ui.shared import flash

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


def render() -> None:
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
    prefs_now = config.load("preferences.json")
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
    for c_ in config.load("cv_profiles.json")["cvs"]:
        p_ = paths.project_path(c_["path"])
        st.markdown(
            f"{'✅' if p_.is_file() else '❌'} `{c_['id']}` → `{c_['path']}`"
            + ("" if p_.is_file() else " — à déposer dans ce dossier")
        )
