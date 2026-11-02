"""Offers the user saves by hand: "Mes offres.xlsx" in the Fitly folder.

One line per offer seen while browsing (Welcome to the Jungle, a company site, a newsletter...):
    A Date collecte | B Entreprise | C Poste | D Localisation | E Publication | F Lien fiche de poste | G Notes
Only Entreprise, Poste and Lien are required. Publication accepts "3 days ago", "il y a 2 semaines",
"today", "hier" or a date. Fitly imports the new lines (scored, deduplicated with the other sources, then
followed like any offer) at every morning collection, and from the interface ("📥 Importer Mes offres").
The file is never modified by Fitly except to create it (with an example line) when it is missing.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from fitly import config, paths

FILE = paths.ROOT / "Mes offres.xlsx"
HEADERS = ["Date collecte", "Entreprise", "Poste", "Localisation", "Publication", "Lien fiche de poste", "Notes"]
EXAMPLE = [
    "2026-10-15",
    "Exemple SAS",
    "Data Engineer (exemple : supprime cette ligne)",
    "Paris",
    "3 days ago",
    "https://www.welcometothejungle.com/fr/companies/exemple/jobs/data-engineer",
    "",
]
UNITS = {
    "minute": 0,
    "hour": 0,
    "heure": 0,
    "day": 1,
    "jour": 1,
    "week": 7,
    "semaine": 7,
    "month": 30,
    "mois": 30,
    "year": 365,
    "an": 365,
    "année": 365,
    "annee": 365,
}


def create_template(path=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    path = path or FILE
    wb = Workbook()
    ws = wb.active
    ws.title = "Mes offres"
    ws.append(HEADERS)
    ws.append(EXAMPLE)
    for c, w in enumerate([14, 26, 50, 24, 15, 60, 40], 1):
        ws.column_dimensions[get_column_letter(c)].width = w
        ws.cell(row=1, column=c).font = Font(bold=True, color="FFFFFF")
        ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor="1F4E78")
    ws.freeze_panes = "A2"
    help_ws = wb.create_sheet("Mode d'emploi")
    for line in [
        "Une ligne par offre vue en naviguant (Welcome to the Jungle, site d'une entreprise...).",
        "Obligatoire : Entreprise, Poste, Lien fiche de poste. Le reste est facultatif.",
        "Publication : « 3 days ago », « il y a 2 semaines », « today », « hier » ou une date.",
        "Enregistre le fichier : Fitly l'importe à la collecte du matin, ou via « 📥 Importer Mes offres ».",
        "Les offres importées sont notées, dédoublonnées et suivies comme les autres (GO, candidature, relances).",
        "Supprimer une ligne ici ne supprime pas l'offre de Fitly (utilise NO-GO dans l'interface).",
    ]:
        help_ws.append([line])
    help_ws.column_dimensions["A"].width = 110
    wb.save(path)
    return path


def _date(value, today=None):
    """Publication -> YYYY-MM-DD ('' if unknown)."""
    today = today or date.today()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    if text in ("today", "aujourd'hui", "aujourdhui", "just now", "à l'instant"):
        return today.isoformat()
    if text in ("yesterday", "hier"):
        return (today - timedelta(days=1)).isoformat()
    m = re.search(
        r"(\d+|an?|une?|one)\s*\+?\s*(minute|hour|heure|day|jour|week|semaine|month|mois|year|année|annee|an)s?", text
    )
    if m:
        n = 1 if m.group(1) in ("a", "an", "un", "une", "one") else int(m.group(1))
        return (today - timedelta(days=n * UNITS[m.group(2)])).isoformat()
    return ""


def read(path=None):
    """Valid offers of the file (example line and incomplete lines skipped). Returns (jobs, problems)."""
    from openpyxl import load_workbook

    path = Path(path or FILE)
    if not path.exists():
        return [], []
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    head = [str(h or "").strip().lower() for h in rows[0]]

    def col(name, default):
        return head.index(name.lower()) if name.lower() in head else default

    idx = {
        k: col(h, i)
        for i, (k, h) in enumerate(
            zip(("collected", "company", "title", "location", "published", "url", "notes"), HEADERS, strict=False)
        )
    }
    jobs, problems = [], []
    for n, r in enumerate(rows[1:], start=2):

        def get(k, r=r):
            return r[idx[k]] if idx[k] < len(r) else None

        company, title, url = (str(get(k) or "").strip() for k in ("company", "title", "url"))
        if not (company or title or url):
            continue
        if "exemple" in company.lower() and "exemple" in title.lower():
            continue
        if not (company and title and url.startswith("http")):
            problems.append(f"ligne {n} : il faut Entreprise, Poste et un lien http(s)")
            continue
        jobs.append(
            {
                "source": "manual",
                "external_id": "manual:" + hashlib.sha1(url.encode()).hexdigest()[:16],
                "title": title,
                "company": company,
                "location": str(get("location") or "").strip(),
                "description": str(get("notes") or "").strip(),
                "url": url,
                "posted_at": _date(get("published")) or _date(get("collected")),
                "application_route": "external_or_manual",
            }
        )
    return jobs, problems


def import_file(path=None, db_path=None):
    """Score and store the offers of the file. Returns {read, new, problems}."""
    from fitly.scoring.pipeline import run_pipeline
    from fitly.storage import connect, upsert_jobs

    db_path = paths.db_path(db_path)
    jobs, problems = read(path or FILE)
    if not jobs:
        return {"read": 0, "new": 0, "problems": problems}
    scored = run_pipeline(jobs, *config.scoring_inputs())
    con = connect(db_path)
    before = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    con.close()
    upsert_jobs(scored, db_path)
    con = connect(db_path)
    after = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    con.close()
    return {"read": len(jobs), "new": after - before, "problems": problems}


def live_status(db_path=None, report=None):
    """For the Sources panel, computed at every page load: lines in the file, offers in Fitly coming from it
    (relevant ones too), and whether the last morning collection read the file."""
    from fitly.storage import connect

    db_path = paths.db_path(db_path)
    try:
        lines, problems = read()
    except Exception:
        lines, problems = [], ["fichier illisible (ouvert dans Excel ?)"]
    con = connect(db_path)
    try:
        own = con.execute("SELECT COUNT(*), SUM(COALESCE(score,0) >= 55) FROM jobs WHERE source='manual'").fetchone()
        merged = con.execute(
            "SELECT COUNT(DISTINCT s.job_id) FROM job_sources s JOIN jobs j ON j.id=s.job_id "
            "WHERE s.source='manual' AND j.source!='manual'"
        ).fetchone()[0]
    finally:
        con.close()
    morning = next((s for s in (report or {}).get("sources", []) if s.get("type") == "manual"), None)
    return {
        "file": FILE.exists(),
        "lines": len(lines),
        "problems": problems,
        "in_fitly": own[0] or 0,
        "relevant": own[1] or 0,
        "merged": merged or 0,
        "morning_at": (report or {}).get("at") if morning else None,
        "morning_read": morning.get("collected") if morning else None,
        "morning_status": morning.get("status") if morning else None,
    }


if __name__ == "__main__":
    if not FILE.exists():
        print(f"Modèle créé : {create_template()}")
    print(import_file())
