# Fitly

[![CI](https://github.com/alexandreryba/Fitly/actions/workflows/ci.yml/badge.svg)](https://github.com/alexandreryba/Fitly/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A local job-search assistant. Every morning it collects public job offers, scores them against your
profile, lets you decide go / no-go, pre-fills application forms in your own browser and tracks every
application until the answer.

**It never applies on your behalf.** You read the form, you click *Submit*, then you record it.

## What it does

| Step | How |
|---|---|
| **Collect** | Public job-board APIs of the employers you add with `fitly probe` (Lever, Greenhouse, Ashby, SmartRecruiters, Workable, Recruitee, Teamtailor), optional search APIs with your own free keys (Google Jobs via SerpApi, Adzuna, Jooble, France Travail), job-alert e-mails, a spreadsheet you fill by hand, and a browser extension that adds the offer you are looking at. |
| **Score** | Deterministic and offline: target roles in priority order, skills, location, seniority, posting age and explicit penalties. Every score comes with its breakdown. Duplicates across sources are merged. |
| **Review** | A daily selection grouped by job family and company, with a per-company quota so you never send five applications to the same team. CSV, Excel and HTML exports. |
| **Pre-fill** | The extension fills only fields you have validated (name, e-mail, notice period...), attaches the right CV (family + language), declines cookie banners and stops on CAPTCHAs or unknown required questions. Consent and terms boxes are left to you unless you opt in (`tick_required_consents`); marketing boxes are never ticked. |
| **Track** | Statuses, follow-up reminders and drafts, status proposals read from your e-mails (applied only after you confirm), and funnel indicators. |

The point is to apply less, and better. On the author's setup, a morning run reads about 8,000 postings from
70 sources (66 employer boards, 4 search APIs) and merges them into about 7,500 distinct offers (the
deduplication itself takes seconds, `tests/test_dedup_index.py`); the daily selection then keeps the 10 best,
with at most 5 offers and 3 different roles per company.

## Principles

- **Human in the loop.** No final submission, no message sent, no account created.
- **Public and authorised sources only.** Official APIs, RSS feeds and your own e-mails. No scraping behind a login, and no attempt to get around CAPTCHAs or anti-bot protections: when one appears, Fitly stops and leaves the page to you.
- **Local first.** Data stays on your computer (SQLite). The interface and the extension's server listen on `127.0.0.1` only, and the server only answers the Fitly extension: its ID is pinned by the public key in `extension/manifest.json`, so neither a web page nor another extension can push offers or read your CV.
- **Personal data never enters the repository.** See [Configuration](#configuration).

## Quick start (Windows, PowerShell)

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"       # everything, tests included
.\.venv\Scripts\python.exe -m playwright install chromium   # only for the Playwright pre-fill mode and its tests
.\.venv\Scripts\python.exe -m fitly doctor                # checks settings, database and CV files
.\scripts\start_ui.ps1                                       # interface on http://localhost:8501
```

`pip install -e .` alone installs the core (collection, scoring, interface, extension server). Two features
are optional extras: `.[ai]` for the AI drafts (Anthropic SDK) and `.[browser]` for the Playwright pre-fill
mode and the form audit.

Then load the extension: `chrome://extensions` → *Developer mode* → *Load unpacked* → the `extension/`
folder. Its ID is always `edpcldibebibfflchgbodmbhnpihggdl`. The French step-by-step guide is in
[docs/guide.md](docs/guide.md).

Daily use from the command line:

```powershell
python -m fitly morning          # collect, score, store (read-only on the network)
python -m fitly review           # rebuild the review queue (JSON, CSV, HTML)
python -m fitly decide 42 go     # or fill the 'decision' column of the CSV, then: fitly decisions
python -m fitly applied 42 --cv tech_fr
python -m fitly followups --drafts
python -m fitly --help           # every command
```

`.\scripts\install_daily_task.ps1` registers the morning collection as a Windows scheduled task, and
`.\scripts\sandbox.ps1` opens the interface on a copy of the database to try things safely.

## Configuration

Shared defaults live in `config/` and are versioned. Your own settings live in `config/local/`, which is
git-ignored; a file there replaces the default of the same name, and the interface always saves there.
The versioned defaults describe a fictional candidate, collect from the four search APIs only (add employer
boards with `fitly probe "Company" --add`), keep the AI drafts off and tick no consent box.

| File | Content |
|---|---|
| `preferences.json` | Target roles in priority order, location, contracts, salary, companies to exclude |
| `profile.json` | Name, headline and skills used by the scoring and the AI drafts |
| `answer_library.json` | Validated answers for forms; an empty field is never filled |
| `candidate_facts.md` | The only facts the optional AI drafts may use (missing ones are marked, never invented) |
| `cv_profiles.json` | CV files per job family and language, with the keywords that select them |
| `ats_sources.json` | Employer job boards and search sources to collect (`fitly probe "Company" --add`) |
| `application_rules.json` | Follow-up delays, per-company quota, daily selection size, pre-fill mode |
| `ai.json`, `email_alerts.json` | Optional AI drafts (monthly cost cap) and IMAP alerts (read-only) |

Never versioned either: `data/` (database, exports, logs), `backups/`, `cvs/` and the manual offers sheet.
API keys and passwords are read from environment variables only (`.\scripts\configurer_sources.ps1`
stores them as Windows user variables).

## Architecture

```
src/fitly/
├── cli.py, __main__.py   command line (python -m fitly)
├── morning.py            daily collection: sources → scoring → storage → exports
├── paths.py, config.py   file locations; shared defaults vs personal settings
├── storage.py            SQLite schema with additive migrations, cross-source identity
├── sources/              public ATS clients, search APIs, e-mail alerts, manual sheet,
│                         availability check on the employer's own site, per-source health
├── scoring/              scoring pipeline, deduplication, per-company quota
├── review/               review queue, CSV / Excel / HTML exports, indicators
├── apply/                form analysis, local server for the extension, Playwright pre-fill,
│                         candidate pack, optional AI drafts
├── tracking/             statuses, follow-ups, updates proposed from e-mails
└── ui/                   Streamlit interface: app.py lays out the page, one module per tab in tabs/
extension/                Chrome / Edge extension (Manifest V3): capture and pre-fill
tests/                    pytest suite, plus opt-in end-to-end tests
```

The scoring never touches the network and the network code never decides: sources return normalised job
dictionaries, `scoring` turns them into scored offers, `storage` merges them, and everything the user sees
is derived from the database. The extension does the typing in the browser; the local server decides what
may be typed, from the same rules as the Playwright mode.

## Development

```powershell
python -m pytest -q          # unit, UI (streamlit.testing) and headless-browser tests
python -m ruff check .       # lint
python -m ruff format .      # format
python -m mypy               # type check (settings in pyproject.toml)
```

The tests never read your personal settings, keys or offers: `tests/conftest.py` points them at the shared
defaults and at temporary databases, and the scoring regression tests use a frozen fictional profile
(`tests/fixtures/`). The end-to-end test of the real extension in Edge is opt-in
(`$env:FITLY_E2E = "1"`).

## How it was built

Fitly was built with Claude Code, an AI coding assistant, used as a pair programmer. The author designed the
product and its rules (a human submits every application, public sources only, data stays local), made the
technical decisions, reviewed the changes and uses Fitly for a real job search. The test suite and the CI
check every change.

## License

[MIT](LICENSE)
