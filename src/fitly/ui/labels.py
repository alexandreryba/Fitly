"""Display names of the offer sources (interface and Excel export)."""

SOURCE_LABELS = {
    "lever": "Lever (site carrière)",
    "greenhouse": "Greenhouse (site carrière)",
    "ashby": "Ashby (site carrière)",
    "smartrecruiters": "SmartRecruiters (site carrière)",
    "workable": "Workable (site carrière)",
    "recruitee": "Recruitee (site carrière)",
    "teamtailor": "Teamtailor (site carrière)",
    "linkedin": "LinkedIn (alerte e-mail)",
    "wttj": "Welcome to the Jungle (alerte)",
    "hellowork": "HelloWork (alerte)",
    "indeed": "Indeed (alerte)",
    "apec": "APEC (alerte)",
    "cadremploi": "Cadremploi (alerte)",
    "free_work": "Free-Work (alerte)",
    "meteojob": "Meteojob (alerte)",
    "jobteaser": "JobTeaser (alerte)",
    "glassdoor": "Glassdoor (alerte)",
    "wellfound": "Wellfound (alerte)",
    "manual": "Ajout manuel (Mes offres.xlsx)",
    "email": "Candidature (e-mail)",
    "google_jobs": "Google Jobs",
    "adzuna": "Adzuna",
    "jooble": "Jooble",
    "france_travail": "France Travail",
}


def source_label(src, via=None):
    label = SOURCE_LABELS.get(src or "", src or "?")
    if via:  # aggregators: show the original site ("Google Jobs · via Welcome To The Jungle")
        label += f" · {str(via).replace('via ', '').strip()}"
    return label
