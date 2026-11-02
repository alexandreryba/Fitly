"""SQLite storage: offers, their sources, applications and the event history.

The schema only grows by additive, nullable columns (`MIGRATIONS`), so an existing database keeps working.
An offer's identity is company + title + location when all three are known, so the same posting coming from
two sources is stored once (one row in `jobs`, one row per source in `job_sources`).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from fitly import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT UNIQUE NOT NULL,
    external_id TEXT,
    source TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    description TEXT,
    url TEXT,
    score REAL,
    score_band TEXT,
    recommended_cv TEXT,
    application_route TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    external_id TEXT,
    source_url TEXT,
    discovered_at TEXT,
    UNIQUE(job_id, source, external_id)
);
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER UNIQUE NOT NULL,
    cv_type TEXT,
    route TEXT,
    status TEXT DEFAULT 'new',
    applied_at TEXT,
    next_follow_up_at TEXT,
    last_action_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    application_id INTEGER,
    event_type TEXT NOT NULL,
    event_at TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(lower(trim(company)));
CREATE INDEX IF NOT EXISTS idx_job_sources_posting ON job_sources(source, external_id);
"""


def connect(db_path=None):
    """Open the database (created and migrated on first use); `None` means the database in use."""
    p = paths.project_path(paths.db_path(db_path))
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


# Additive, nullable columns only: existing rows and older code keep working.
MIGRATIONS = {
    "jobs": {
        "score_details": "TEXT",
        "posted_at": "TEXT",
        "employment_type": "TEXT",
        "via": "TEXT",
        # employer_match: same offer on the employer's own site; 'open' / 'closed' / ...
        "direct_url": "TEXT",
        "availability": "TEXT",
        "availability_checked_at": "TEXT",
    }
}


def _migrate(con):
    for table, columns in MIGRATIONS.items():
        existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    con.commit()


def score_details(job):
    """Compact JSON explanation of the score (role matched + point breakdown)."""
    exp = job.get("match_explanation") or {}
    if not exp:
        return None
    return json.dumps(
        {
            "matched_role": exp.get("matched_role"),
            "skill_hits": exp.get("skill_hits"),
            "breakdown": exp.get("score_breakdown") or {},
        },
        ensure_ascii=False,
    )


def _identity_parts(job):
    return [" ".join((job.get(k) or "").strip().lower().split()) for k in ("company", "title", "location")]


def canonical_key(job):
    # Prefer a cross-source identity when company, title and location are all
    # present. Source-specific IDs remain a fallback for incomplete records.
    parts = _identity_parts(job)
    if all(parts):
        return "job:" + "|".join(parts)
    ext = job.get("external_id")
    if ext:
        return f"{job.get('source', 'unknown')}:{ext}"
    return "incomplete:" + "|".join(parts)


def legacy_key(job):
    """Key format of the first versions (rows stored back then keep it)."""
    ext = job.get("external_id")
    if ext:
        return f"{job.get('source', 'unknown')}:{ext}"
    return "|".join((job.get(k) or "").strip().lower() for k in ("company", "title", "location"))


def _find_existing(con, job, key):
    # 1. Same source posting already linked to a job (works for legacy rows).
    if job.get("external_id"):
        row = con.execute(
            "SELECT job_id AS id FROM job_sources WHERE source=? AND external_id=?",
            (job.get("source"), job.get("external_id")),
        ).fetchone()
        if row:
            return row
    # 2. Current canonical key, then the legacy key.
    for k in (key, legacy_key(job)):
        row = con.execute("SELECT id FROM jobs WHERE canonical_key=?", (k,)).fetchone()
        if row:
            return row
    # 3. Same complete company/title/location stored under a legacy key.
    parts = _identity_parts(job)
    if all(parts):
        from fitly.scoring.novelty import city

        for r in con.execute("SELECT id, company, title, location FROM jobs WHERE lower(trim(company))=?", (parts[0],)):
            other = _identity_parts(dict(r))
            if other == parts:
                return r
            # Same company + title, same city written differently ('Paris' vs 'Paris, France').
            if other[1] == parts[1] and city(job.get("location")) and city(job.get("location")) == city(r["location"]):
                return r
    return None


def upsert_jobs(jobs, db_path=None):
    now = datetime.now(UTC).isoformat()
    con = connect(db_path)
    inserted, updated = 0, 0
    for j in jobs:
        key = canonical_key(j)
        row = _find_existing(con, j, key)
        values = (
            key,
            j.get("external_id"),
            j.get("source"),
            j.get("title"),
            j.get("company"),
            j.get("location"),
            j.get("description"),
            j.get("url"),
            j.get("score"),
            j.get("score_band"),
            j.get("recommended_cv"),
            j.get("application_route"),
            score_details(j),
            j.get("posted_at") or None,
            j.get("employment_type") or None,
            j.get("via") or None,
        )
        if row:
            con.execute(
                """UPDATE jobs SET external_id=?, source=?, title=?, company=?,
                location=?, description=?, url=?, score=?, score_band=?,
                recommended_cv=?, application_route=?, score_details=?,
                posted_at=COALESCE(?, posted_at), employment_type=COALESCE(?, employment_type), via=COALESCE(?, via),
                last_seen_at=? WHERE id=?""",
                (*values[1:], now, row["id"]),
            )
            job_id = row["id"]
            updated += 1
        else:
            cur = con.execute(
                """INSERT INTO jobs(
                canonical_key,external_id,source,title,company,location,description,url,
                score,score_band,recommended_cv,application_route,score_details,posted_at,employment_type,via,first_seen_at,last_seen_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*values, now, now),
            )
            job_id = cur.lastrowid
            inserted += 1

        con.execute(
            """INSERT OR IGNORE INTO job_sources
            (job_id,source,external_id,source_url,discovered_at)
            VALUES(?,?,?,?,?)""",
            (job_id, j.get("source"), j.get("external_id"), j.get("url"), now),
        )
    con.commit()
    con.close()
    return {"inserted": inserted, "updated": updated}
