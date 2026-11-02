"""Read-only collector registry for public Greenhouse and Lever job boards."""

import re
from html import unescape
from typing import Any

from fitly.sources.ats_clients import LISTERS, SEARCHERS, iso_date, list_france_travail
from fitly.sources.greenhouse_client import list_jobs as greenhouse_list_jobs
from fitly.sources.lever_client import list_postings as lever_list_postings


def _plain(value):
    value = unescape(str(value or ""))
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def collect_from_config(config, stats=None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect every configured source. `stats` (a list), when given, receives one entry per source:
    {type, name, target, status: ok|empty|error|no_key|disabled, collected, error}."""
    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in config.get("sources", []):
        kind = str(item.get("type", "")).lower()
        target = item.get("board_token") if kind == "greenhouse" else item.get("site_name") or item.get("board")
        entry = {
            "type": kind,
            "name": item.get("company") or target or kind,
            "target": target,
            "status": "ok",
            "collected": 0,
            "error": "",
        }
        if stats is not None:
            stats.append(entry)
        if item.get("enabled") is False:
            entry["status"] = "disabled"
            continue
        before = len(jobs)
        try:
            if kind == "greenhouse":
                data = greenhouse_list_jobs(item["board_token"], content=True)
                batch = data.get("jobs", []) if isinstance(data, dict) else []
                for j in batch:
                    jobs.append(
                        {
                            "id": f"greenhouse:{item['board_token']}:{j.get('id')}",
                            "external_id": str(j.get("id", "")),
                            "source": "greenhouse",
                            "title": j.get("title", ""),
                            "company": j.get("company_name") or item["board_token"],
                            "location": (j.get("location") or {}).get("name", ""),
                            "description": _plain(j.get("content", "")),
                            "url": j.get("absolute_url", ""),
                            "application_route": "external_ats",
                            "posted_at": iso_date(j.get("first_published") or j.get("updated_at")),
                        }
                    )
            elif kind == "lever":
                batch = lever_list_postings(item["site_name"], eu=bool(item.get("eu", False)), mode="json")
                if not isinstance(batch, list):
                    raise ValueError("Lever API response was not a list")
                for j in batch:
                    cats = j.get("categories") or {}
                    jobs.append(
                        {
                            "id": f"lever:{item['site_name']}:{j.get('id')}",
                            "external_id": str(j.get("id", "")),
                            "source": "lever",
                            "title": j.get("text", ""),
                            "company": item.get("company") or item["site_name"],
                            "location": cats.get("location", ""),
                            "description": _plain(j.get("descriptionPlain") or j.get("description", "")),
                            "url": j.get("hostedUrl") or j.get("applyUrl", ""),
                            "application_route": "external_ats",
                            "posted_at": iso_date(j.get("createdAt")),
                            "employment_type": cats.get("commitment", ""),
                        }
                    )
            elif kind in LISTERS:
                jobs.extend(LISTERS[kind](item["board"], company=item.get("company")))
            elif kind in SEARCHERS:
                import inspect

                accepted = inspect.signature(SEARCHERS[kind]).parameters  # each source gets only its own options
                opts = {k: v for k, v in item.items() if k in accepted and k != "keywords"}
                jobs.extend(SEARCHERS[kind](item.get("keywords") or [], **opts))
            elif kind == "france_travail":
                jobs.extend(
                    list_france_travail(
                        item.get("keywords") or [],
                        departements=tuple(
                            item.get("departements") or ("75", "92", "93", "94", "91", "78", "95", "77")
                        ),
                        published_since_days=int(item.get("published_since_days", 7)),
                    )
                )
            else:
                errors.append(
                    {
                        "source": kind or "unknown",
                        "target": target,
                        "error": "Unsupported source type; use greenhouse, lever, "
                        + ", ".join(LISTERS)
                        + " or a search source: "
                        + ", ".join(SEARCHERS)
                        + ", france_travail.",
                    }
                )
            entry["collected"] = len(jobs) - before
            entry["status"] = "ok" if entry["collected"] else "empty"
        except Exception as exc:  # deliberate: one failing source (network, API change, bad key) never stops the others
            # "enabled": "auto" = run only when the user's API key is configured; stay silent otherwise.
            if item.get("enabled") == "auto" and any(w in str(exc).lower() for w in ("not set", "missing")):
                entry["status"] = "no_key"
                continue
            entry.update(status="error", error=str(exc)[:200], collected=len(jobs) - before)
            errors.append({"source": kind or "unknown", "target": target, "error": str(exc)})
    return jobs, errors
