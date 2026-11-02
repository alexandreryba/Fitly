"""'Mes offres.xlsx': offers saved by hand are imported, scored, deduplicated and followed."""

from pathlib import Path

from fitly.review import review_queue
from fitly.sources import manual_offers
from fitly.storage import connect

ROOT = Path(__file__).resolve().parents[1]


def test_manual_file_is_imported_and_followed(tmp_path):
    from openpyxl import load_workbook

    xlsx = manual_offers.create_template(tmp_path / "Mes offres.xlsx")
    wb = load_workbook(xlsx)
    ws = wb.active
    assert [c.value for c in ws[1]][:6] == [
        "Date collecte",
        "Entreprise",
        "Poste",
        "Localisation",
        "Publication",
        "Lien fiche de poste",
    ]
    ws.append(
        [
            "2026-10-15",
            "Acme",
            "Data Engineer",
            "Paris",
            "il y a 3 jours",
            "https://www.welcometothejungle.com/fr/companies/acme/jobs/data-engineer",
            "vu sur WTTJ",
        ]
    )
    ws.append(["2026-10-15", "Beta", "", "Paris", "today", "https://x/2", ""])  # incomplete
    wb.save(xlsx)
    db = tmp_path / "m.db"
    res = manual_offers.import_file(xlsx, str(db))
    assert res["read"] == 1 and res["new"] == 1 and len(res["problems"]) == 1  # example line ignored
    again = manual_offers.import_file(xlsx, str(db))
    assert again["new"] == 0  # no duplicate on re-import
    row = next(r for r in review_queue.build(db) if r["company"] == "Acme")
    assert row["source"] == "manual" and row["score"] > 0 and row["posted_at"]
    con = connect(str(db))
    assert con.execute("SELECT description FROM jobs WHERE company='Acme'").fetchone()[0] == "vu sur WTTJ"
    con.close()
