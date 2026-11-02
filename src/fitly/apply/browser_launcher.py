"""Start a Playwright browser that works on this Windows machine.

Order: Microsoft Edge (signed, always on Windows 11) -> Google Chrome -> Playwright's bundled Chromium
(unsigned build that security software may block: 'spawn UNKNOWN'). Every launch uses a fresh temporary
profile: no access to the user's cookies, sessions or saved passwords.
"""

import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from fitly.paths import DATA_DIR

CHANNELS = ("msedge", "chrome", None)

# --- Persistent "Fitly" Chrome window for pre-filling (one new tab per offer) --------------------
PROFILE_DIR = DATA_DIR / "chrome_profile"  # dedicated profile (never the user's main Chrome profile)
CDP_PORT = int(os.environ.get("FITLY_CHROME_PORT", "9223"))
BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",  # fallback when Chrome is absent
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def open_in_user_chrome(urls):
    """Open URLs as new tabs in the user's own Chrome (its last active window). Returns the executable used."""
    exe = (
        next((p for p in BROWSER_PATHS if Path(p).is_file() and p.lower().endswith("chrome.exe")), None)
        or _browser_exe()
    )
    if not exe:
        raise RuntimeError("Google Chrome introuvable.")
    subprocess.Popen(
        [exe, *urls],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    return exe


def _cdp_ready(port=CDP_PORT):
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _browser_exe():
    return next((p for p in BROWSER_PATHS if Path(p).is_file()), None)


def open_user_window(pw, port=CDP_PORT):
    """Attach to the Fitly Chrome window (start it if needed). Returns (browser, context).

    The window is a normal, visible Chrome using a dedicated profile in data/chrome_profile; it keeps
    running after Fitly finishes, so the user reviews and submits in their own time. Each call adds tabs.
    Remote debugging listens on 127.0.0.1 only and only for that profile.
    """
    if not _cdp_ready(port):
        exe = _browser_exe()
        if not exe:
            raise RuntimeError("Google Chrome (ou Microsoft Edge) introuvable.")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                exe,
                f"--remote-debugging-port={port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        for _ in range(60):
            if _cdp_ready(port):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("La fenêtre Chrome Fitly n'a pas démarré (port de pilotage indisponible).")
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, context


def launch(pw, headless=False):
    errors = []
    for channel in CHANNELS:
        try:
            kwargs = {"headless": headless}
            if channel:
                kwargs["channel"] = channel
            return pw.chromium.launch(**kwargs)
        except Exception as exc:
            errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0][:100]}")
    raise RuntimeError("No usable browser (install Microsoft Edge or Google Chrome):\n  " + "\n  ".join(errors))
