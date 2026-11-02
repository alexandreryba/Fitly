"""New vs already-seen offers, and the same posting found on several sources (deduplication)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def fingerprint(job):
    return "|".join([norm(job.get("company")), norm(job.get("title")), norm(job.get("location"))])


def description_similarity(a, b):
    x, y = norm(a.get("description")), norm(b.get("description"))
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y).ratio()


def city(location):
    """First place name of a location ('Paris, Île-de-France, France' -> 'paris'); '' if unknown."""
    import re as _re

    first = _re.split(r"[,(;|/]", str(location or ""))[0]
    return norm(first)


def same_job(a, b):
    if a.get("external_id") and (a.get("source"), a.get("external_id")) == (b.get("source"), b.get("external_id")):
        return True
    company_a, title_a = norm(a.get("company")), norm(a.get("title"))
    company_b, title_b = norm(b.get("company")), norm(b.get("title"))
    # Do not treat records with missing identity fields as the same job merely
    # because their empty company/title/location fingerprints are identical.
    if (company_a or title_a) and (company_b or title_b) and fingerprint(a) == fingerprint(b):
        return True
    if company_a and title_a and company_a == company_b and title_a == title_b:
        # Same posting seen on two sources: 'Paris, France' on the career site, 'Paris' on Google Jobs.
        if city(a.get("location")) and city(a.get("location")) == city(b.get("location")):
            return True
        return description_similarity(a, b) >= 0.72
    return False


def _match_keys(job):
    """Keys under which `same_job` can possibly match `job`: one per rule of `same_job`."""
    keys = []
    if job.get("external_id"):
        keys.append(("posting", job.get("source"), job.get("external_id")))
    company, title = norm(job.get("company")), norm(job.get("title"))
    if company or title:
        keys.append(("fingerprint", fingerprint(job)))
    if company and title:
        keys.append(("company_title", company, title))
    return keys


class JobIndex:
    """The jobs added so far, indexed so that `first_match` does what scanning them all with `same_job`
    would do — first match in insertion order — without comparing every pair (thousands of offers a day)."""

    def __init__(self, jobs=()):
        self._jobs = []
        self._positions = {}  # key -> positions of the jobs carrying it
        for job in jobs:
            self.add(job)

    @property
    def jobs(self):
        return list(self._jobs)

    def add(self, job):
        self._jobs.append(job)
        self.reindex(len(self._jobs) - 1)

    def reindex(self, position):
        """Index again the job at `position` after it was updated in place (old keys stay: harmless)."""
        for key in _match_keys(self._jobs[position]):
            self._positions.setdefault(key, set()).add(position)

    def first_match(self, job):
        """(position, job) of the first indexed job that `same_job(job, ...)` accepts, else (None, None)."""
        candidates = set()
        for key in _match_keys(job):
            candidates |= self._positions.get(key, set())
        for position in sorted(candidates):
            if same_job(job, self._jobs[position]):
                return position, self._jobs[position]
        return None, None


def classify(current, previous):
    """Split today's offers into new and already known ones; `duplicates`: new offers seen twice today."""
    seen_before = JobIndex(previous)
    new, known = [], []
    for job in current:
        (new if seen_before.first_match(job)[1] is None else known).append(job)
    unique, duplicates, today = [], [], JobIndex()
    for job in new:
        if today.first_match(job)[1] is None:
            unique.append(job)
            today.add(job)
        else:
            duplicates.append(job)
    return {"new": unique, "known": known, "duplicates": duplicates}
