"""Filesystem locations, in one place.

Personal files live in git-ignored folders: ``data/`` (database, review files, logs), ``cvs/`` (CV PDFs),
``config/local/`` (profile, preferences, answers) and the manual offers sheet at the project root.

``FITLY_DB`` points the whole application at another database: a sandbox copy or a test database.
Files produced from the database (review queue, run summary...) then live next to it, so a sandbox never
overwrites the real ones.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
CVS_DIR = ROOT / "cvs"
BACKUP_DIR = ROOT / "backups"
EXTENSION_DIR = ROOT / "extension"
DEMO_DIR = ROOT / "demo"
DEFAULT_DB = DATA_DIR / "fitly.db"


def db_path(override: str | os.PathLike | None = None) -> str:
    """Database in use, resolved at call time (the sandbox and the tests set FITLY_DB after import)."""
    return str(override or os.environ.get("FITLY_DB") or DEFAULT_DB)


def work_dir() -> Path:
    """Folder of the files derived from the database in use (``data/`` unless a sandbox is active)."""
    return Path(db_path()).parent


def is_sandbox() -> bool:
    return bool(os.environ.get("FITLY_DB"))


def project_path(path: str | os.PathLike) -> Path:
    """A path from a config file ('cvs/cv_tech_fr.pdf'), relative to the project root unless absolute."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
