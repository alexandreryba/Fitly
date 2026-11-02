"""Test-wide isolation from the user's own data.

- No real API key or password: they are removed from the environment, and `user_env` does not look in the
  Windows registry while FITLY_TEST_ISOLATION is set.
- No personal settings: FITLY_LOCAL_CONFIG points at an empty folder, so every test reads the shared
  defaults of config/ (never config/local/).
- No personal offers sheet: the manual offers file is redirected to each test's temporary folder.
"""

import os
import tempfile

os.environ["FITLY_TEST_ISOLATION"] = "1"
os.environ["FITLY_LOCAL_CONFIG"] = tempfile.mkdtemp(prefix="fitly_no_local_config_")
for _name in (
    "SERPAPI_KEY",
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JOOBLE_API_KEY",
    "FT_CLIENT_ID",
    "FT_CLIENT_SECRET",
    "FITLY_IMAP_PASSWORD",
    "ANTHROPIC_API_KEY",
    "FITLY_DB",
):
    os.environ.pop(_name, None)

import pytest


@pytest.fixture(autouse=True)
def _no_real_manual_file(tmp_path, monkeypatch):
    """Tests never read the user's own manual offers sheet (a test that needs one creates its own)."""
    from fitly.sources import manual_offers

    monkeypatch.setattr(manual_offers, "FILE", tmp_path / "Mes offres (test).xlsx")
