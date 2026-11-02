"""Public Lever postings client (api.lever.co). GET only.

Two retries with a 45 s timeout: some boards answer slowly (about 1 MB of JSON) and used to fail the whole
source on the first timeout.
"""

TIMEOUT = 45


def _get(url):
    from fitly.sources.ats_clients import get_json  # imported here: ats_clients imports this module lazily

    return get_json(url, retries=2, timeout=TIMEOUT)


def list_postings(site_name, eu=False, mode="json"):
    host = "api.eu.lever.co" if eu else "api.lever.co"
    return _get(f"https://{host}/v0/postings/{site_name}?mode={mode}")


def get_posting(site_name, posting_id, eu=False, mode="json"):
    host = "api.eu.lever.co" if eu else "api.lever.co"
    return _get(f"https://{host}/v0/postings/{site_name}/{posting_id}?mode={mode}")
