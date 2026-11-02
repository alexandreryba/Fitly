"""Public Greenhouse job-board client (boards-api.greenhouse.io). GET only."""

from typing import Any


def _get(url):
    from fitly.sources.ats_clients import get_json  # imported here: ats_clients imports this module lazily

    return get_json(url)


def list_jobs(board_token, page=0, content=True) -> Any:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content={'true' if content else 'false'}"
    return _get(f"{url}&page={page}" if page else url)


def get_job(board_token, job_id, content=True) -> Any:
    return _get(
        f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?content={'true' if content else 'false'}"
    )
