"""Read a user setting (API key, password) even when it was saved after this terminal was opened.

`setx` and the setup assistant store keys as Windows *user* environment variables. A PowerShell window opened
before that does not see them in its environment, so we also look in the registry (HKCU\\Environment).
Values are only read, never printed or written anywhere.
"""

from __future__ import annotations

import os
import sys


def get(name, default=None) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    # Tests set FITLY_TEST_ISOLATION=1: never pick up the user's real keys there.
    if sys.platform == "win32" and os.environ.get("FITLY_TEST_ISOLATION") != "1":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if value:
                    return str(value)
        except OSError:
            pass
    return default
