# Changelog

## Unreleased

- **Type checking.** mypy runs in CI (lint job, Python 3.13) on `src/fitly`: every function body is checked
  and public functions declare their return type (enforced by Ruff's `ANN201`).
- **Interface split by tab.** `ui/app.py` now only lays out the page; each tab lives in `ui/tabs/`, the
  sidebar in `ui/sidebar.py`, shared helpers in `ui/shared.py` and the pure offer helpers in
  `ui/offer_rows.py` (imported directly by the tests).
- **Narrower error handling.** `except Exception` is kept only where any failure must be contained (network
  sources, browser automation, hand-edited workbooks, the local server), each with its reason; elsewhere the
  expected errors are named (database, file system, Playwright).

## 1.1.0

- **The extension server answers the Fitly extension only.** Its ID is pinned by a public key in
  `extension/manifest.json` (`edpcldibebibfflchgbodmbhnpihggdl`), and the local server checks that exact
  origin: another installed extension can no longer push offers or read the CV. Reload the extension once
  after updating (*Load unpacked* again): its ID changes.
- **Consent boxes are left to you by default.** `tick_required_consents` is now `false` in the shared
  `application_rules.json`; set it to `true` in `config/local/` to have required policy boxes ticked
  (marketing boxes never are).
- **Neutral defaults.** The versioned configuration describes a fictional candidate, has no salary, collects
  from the four search APIs only (employer boards are added with `fitly probe "Company" --add`, into
  `config/local/`) and keeps the AI drafts off. Scoring regression tests use their own frozen fixture
  (`tests/fixtures/`), so editing the example never moves them.
- **Optional extras.** `anthropic` and `playwright` moved to `pip install -e ".[ai]"` and `".[browser]"`;
  without them, Fitly falls back to template drafts and says which extra to install.
- **Faster deduplication.** Offers are matched through an index (posting id, fingerprint, company + title)
  instead of being compared pair by pair: a full morning now deduplicates in seconds instead of tens of
  minutes, with the same result. The morning run prints timed progress lines.
- **CI on every supported Python** (3.11, 3.12, 3.13), as declared by `requires-python`.
- **Docs:** feedback goes through GitHub issues; walkthroughs no longer name real companies.

## 1.0.0

First public release.

- **Package.** `fitly` is installable (`pip install -e .`) and runs with `python -m fitly`; modules are grouped
  by responsibility: `sources`, `scoring`, `review`, `apply`, `tracking`, `ui`.
- **Personal data separated.** Shared defaults live in `config/`; personal settings in the git-ignored
  `config/local/`, where the interface saves. The versioned defaults describe a fictional profile.
- **One place for paths and settings** (`fitly.paths`, `fitly.config`), every default resolved at call time,
  so a sandbox database (`FITLY_DB`) never touches the real files.
- **Command line:** morning collection, review files, go / no-go, applications, follow-ups, status,
  dashboard, metrics, candidate pack, AI drafts, source checks and `doctor`.
- **Tooling:** Ruff (lint + format), pytest suite (unit, Streamlit UI, headless browser, opt-in end-to-end
  test of the real extension in Edge), GitHub Actions CI on Windows.

Features carried over from the private versions: collection from public ATS and search APIs, deterministic
scoring with explanations, daily selection by job family and company, browser-extension pre-fill (relay page,
one-click fill, cookie refusal, CAPTCHA stop), employer-site availability check, e-mail alerts and status
proposals, follow-ups and indicators.
