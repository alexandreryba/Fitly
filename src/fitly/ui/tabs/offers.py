"""📋 Offres: daily selection by job family, then every offer to review, with GO / NO-GO."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import streamlit as st

from fitly import config
from fitly.ui.labels import source_label
from fitly.ui.offer_rows import (
    CATEGORIES,
    OTHER_CATEGORY,
    Row,
    by_company,
    category_of,
    first_batch,
    published,
    track_lang,
)
from fitly.ui.shared import PREP_NOTE, apply_decisions, flash, job_row, launch_prefill, render_pack

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


def availability_label(r: Row) -> str:
    a = r.get("availability")
    if a == "not_on_employer_site":
        return "⚠️ absente du site carrière"
    if a == "closed":
        return "🚫 expirée"
    from fitly.sources import employer_match

    if r.get("direct"):
        return "✅ lien employeur trouvé"
    return "🧭 via site d'emploi" if employer_match.is_relay(r["url"], r["company"]) else ""


def render_daily_by_category(queue: list[Row], quota: dict[str, Any]) -> None:
    """Morning view: every offer above the chosen score, grouped by job family; GO (and pre-fill) a whole
    family in one click, with one CV checked per family instead of offer by offer."""
    sel_cfg = quota.get("daily_selection", {"min_score": 55, "max_age_days": 14})
    cvs = [c["id"] for c in config.load("cv_profiles.json")["cvs"]]
    with st.expander("⭐ Sélection du jour, par famille de postes", expanded=True):
        top = st.columns([2, 2, 2])
        floor = top[0].slider(
            "Score minimum de la sélection", 0, 100, int(sel_cfg.get("min_score", 55)), 5, key="cat_score"
        )
        ages = {"Toutes": None, "7 derniers jours": 7, "14 derniers jours": 14, "30 derniers jours": 30}
        age = top[1].selectbox("Publiées", list(ages), index=0, key="cat_age")
        cv_ok = top[2].checkbox("Joindre le CV au pré-remplissage", value=True, key="cat_cv_attach")
        days = ages[age]
        since = (date.today() - timedelta(days=days)).isoformat() if days else None
        pool = [r for r in queue if (r["score"] or 0) >= floor and (not since or (published(r) or "") >= since)]
        groups: dict[str, list[Row]] = {}
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
            per_company: dict[str, int] = {}
            for r in rows:
                per_company[r["company"]] = per_company.get(r["company"], 0) + 1
            used: dict[str, int] = {}
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
                main_cv = max(used, key=lambda cv_id: used[cv_id])
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


def render(queue_all: list[Row], quota: dict[str, Any]) -> None:
    queue = [r for r in queue_all if r["application_status"] == "not_started" and r["quota"] != "reserve"]
    st.caption(
        f"Par entreprise : jusqu'à **{quota['max_offers_per_company']} offres** si ce sont des variantes du "
        f"même poste (senior, secteur, ville…), et au plus **{quota['max_distinct_roles_per_company']} métiers "
        f"différents**. Tes GO et candidatures des {quota['company_window_days']} derniers jours comptent. "
        "Le surplus est dans 🗂️ Réserve et remonte ici dès qu'une place se libère (un NO-GO ne prend pas de place)."
    )
    render_daily_by_category(queue, quota)
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
    days = periods[period]
    since = (date.today() - timedelta(days=days)).isoformat() if days else None
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
    in_reserve = sum(
        1
        for r in queue_all
        if r["application_status"] == "not_started" and r["quota"] == "reserve" and (r["score"] or 0) >= min_score
    )
    if in_reserve:
        st.caption(
            f"➕ {in_reserve} autre(s) offre(s) avec ce score sont dans l'onglet **🗂️ Réserve** : règle « 5 offres "
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
