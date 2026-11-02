"""Pure helpers over the offer rows of the review queue: no Streamlit here, so the tests import them directly."""

from __future__ import annotations

from typing import Any

Row = dict[str, Any]

# Daily selection, one section per job family (from the role each offer matched in preferences.json).
CATEGORIES = [
    ("🤖 AI / GenAI Engineer", ("ai engineer", "genai engineer", "applied ai engineer", "llm engineer")),
    ("🛠️ Data Engineer", ("data engineer", "ai data engineer")),
    ("🧠 Machine Learning Engineer", ("machine learning engineer", "mlops engineer")),
    ("🔬 Data Scientist", ("data scientist",)),
    ("🏛️ Architecte data / IA", ("architect",)),
    ("📐 Analytics Engineer", ("analytics engineer",)),
    ("📊 Data Analyst", ("data analyst",)),
    ("💼 Consultant data / IA", ("consultant",)),
    ("🧩 Produit data", ("product owner", "product manager")),
    ("📋 Business Analyst", ("business analyst",)),
]
OTHER_CATEGORY = "🗂️ Autres"


def track_lang(cv_id: str | None) -> tuple[str, str]:
    """('Tech'|'Business', 'FR'|'EN') from the recommended CV id, e.g. tech_fr."""
    track, _, lang = str(cv_id or "tech_fr").partition("_")
    return ("Business" if track == "business" else "Tech"), (lang or "fr").upper()


def published(r: Row) -> str | None:
    """Posting date (YYYY-MM-DD), else the day Fitly first saw the offer."""
    return (r.get("posted_at") or str(r.get("first_seen_at") or "")[:10]) or None


def category_of(r: Row) -> str:
    role = str(r.get("matched_role") or "").lower()
    for name, keys in CATEGORIES:
        if any(k in role for k in keys):
            return name
    return OTHER_CATEGORY


def by_company(rows: list[Row]) -> list[Row]:
    """Offers of one family grouped by company (companies ordered by their best score, offers by score):
    all the offers of a company follow each other, so you see at a glance how many you apply to there."""
    groups: dict[str, list[Row]] = {}
    for r in sorted(rows, key=lambda r: -(r["score"] or 0)):
        groups.setdefault(r["company"], []).append(r)

    def rank(company: str) -> float:  # best offer and overall level both count: 95/90/85 beats 96/60
        scores = [r["score"] or 0 for r in groups[company]]
        return -(max(scores) + sum(scores) / len(scores)) / 2

    return [r for company in sorted(groups, key=rank) for r in groups[company]]


def first_batch(rows: list[Row], size: int = 10) -> list[Row]:
    """The first `size` offers, extended so a company is never split between two batches (10 -> 13 when the
    11th-13th are from the same company as the 10th)."""
    batch = list(rows[:size])
    for r in rows[size:]:
        if batch and r["company"] == batch[-1]["company"]:
            batch.append(r)
        else:
            break
    return batch
