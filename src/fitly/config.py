"""Settings files: shared defaults, personal overrides.

``config/<name>`` holds the defaults that ship with the project (versioned). Personal settings live in
``config/local/<name>`` (git-ignored): when that file exists it replaces the default as a whole. Everything
the interface saves goes to ``config/local/``, so the versioned files never receive personal data.

``FITLY_LOCAL_CONFIG`` moves the personal folder elsewhere (the tests point it at an empty folder).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fitly.paths import CONFIG_DIR

_MISSING = object()


def local_dir() -> Path:
    return Path(os.environ.get("FITLY_LOCAL_CONFIG") or CONFIG_DIR / "local")


def path(name: str) -> Path:
    """File actually used for ``name``: the personal copy when there is one, else the shared default."""
    local = local_dir() / name
    return local if local.exists() else CONFIG_DIR / name


def local_path(name: str) -> Path:
    """Where the personal copy of ``name`` is (or would be) saved."""
    return local_dir() / name


def load(name: str, default: Any = _MISSING) -> Any:
    """Parsed JSON of ``name``; ``default`` when given and the file is missing or unreadable."""
    try:
        return json.loads(path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if default is _MISSING:
            raise
        return default


def read_text(name: str, default: str = "") -> str:
    try:
        return path(name).read_text(encoding="utf-8")
    except OSError:
        return default


def save(name: str, data: Any) -> Path:
    """Write ``data`` (JSON, or a string as is) to the personal copy and return its path.

    The previous personal copy, if any, is kept as ``<name>.bak``.
    """
    target = local_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.with_name(target.name + ".bak").write_bytes(target.read_bytes())
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    target.write_text(text, encoding="utf-8")
    return target


def scoring_inputs() -> tuple[dict, dict, dict]:
    """The three files the scoring needs: preferences, profile, CV profiles."""
    return load("preferences.json"), load("profile.json"), load("cv_profiles.json")
