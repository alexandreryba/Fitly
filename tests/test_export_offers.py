"""Excel export of the offers: requested columns A-F, LinkedIn-style publication age, relevant sheet."""

from datetime import date, timedelta
from pathlib import Path

from fitly.review import export_offers
from fitly.storage import upsert_jobs

ROOT = Path(__file__).resolve().parents[1]


def test_ago():
    t = date(2026, 10, 15)
    assert [export_offers.ago(str(t - timedelta(days=d)), t) for d in (0, 1, 3, 7, 16, 45, 400)] == [
        "today",
        "1 day ago",
        "3 days ago",
        "1 week ago",
        "2 weeks ago",
        "1 month ago",
        "1 year ago",
    ]
    assert export_offers.ago(None) == "" and export_offers.ago("n/a") == ""


def test_excel_export(tmp_path):
    from openpyxl import load_workbook

    db = tmp_path / "x.db"
    upsert_jobs(
        [
            {
                "source": "lever",
                "external_id": "1",
                "title": "Data Engineer",
                "company": "Acme",
                "location": "Paris",
                "description": "",
                "url": "https://jobs.lever.co/acme/1",
                "application_route": "external_ats",
                "score": 80,
                "posted_at": (date.today() - timedelta(days=7)).isoformat(),
            },
            {
                "source": "lever",
                "external_id": "2",
                "title": "Sales Manager",
                "company": "Beta",
                "location": "Paris",
                "description": "",
                "url": "https://jobs.lever.co/beta/2",
                "application_route": "external_ats",
                "score": 20,
            },
        ],
        str(db),
    )
    path, n = export_offers.write(tmp_path / "out.xlsx", str(db))
    wb = load_workbook(path)
    assert wb.sheetnames == ["Offres pertinentes", "Toutes les offres", "Infos"] and n == 2
    rel = list(wb["Offres pertinentes"].iter_rows(values_only=True))
    assert rel[0][:6] == ("Date collecte", "Entreprise", "Poste", "Localisation", "Publication", "Lien fiche de poste")
    assert len(rel) == 2 and rel[1][1:6] == (
        "Acme",
        "Data Engineer",
        "Paris",
        "1 week ago",
        "https://jobs.lever.co/acme/1",
    )
    assert wb["Offres pertinentes"]["F2"].hyperlink.target == "https://jobs.lever.co/acme/1"
    assert len(list(wb["Toutes les offres"].iter_rows(values_only=True))) == 3
