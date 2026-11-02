"""The indexed deduplication must answer exactly like comparing every pair with `same_job`, only faster."""

import random
import time

from fitly import config
from fitly.scoring.novelty import JobIndex, classify, same_job
from fitly.scoring.pipeline import run_pipeline

COMPANIES = ["Acme", "acme ", "Globex", "Initech", "", "Umbrella"]
TITLES = ["Data Engineer", "data engineer", "Data Engineer (H/F)", "AI Engineer", "", "Analytics Engineer"]
PLACES = ["Paris", "Paris, France", "Paris (75)", "Lyon", "", "Remote"]
TEXTS = ["Python SQL dbt pipelines", "Python SQL dbt pipelines on GCP", "LLM RAG agents", "", "Spark Airflow"]


def random_jobs(n, seed):
    rng = random.Random(seed)
    jobs = []
    for i in range(n):
        source = rng.choice(["lever", "greenhouse", "google_jobs"])
        jobs.append(
            {
                "source": source,
                "external_id": rng.choice([None, "", str(rng.randint(1, n // 3 + 1))]),
                "company": rng.choice(COMPANIES),
                "title": rng.choice(TITLES),
                "location": rng.choice(PLACES),
                "description": rng.choice(TEXTS) + " " * rng.randint(0, 2),
                "n": i,
            }
        )
    return jobs


def naive_first_match(job, jobs):
    return next((old for old in jobs if same_job(job, old)), None)


def test_index_finds_the_same_first_match_as_a_full_scan():
    for seed in range(5):
        jobs = random_jobs(300, seed)
        index = JobIndex(jobs[:200])
        for job in jobs[200:]:
            assert index.first_match(job)[1] is naive_first_match(job, jobs[:200])


def test_classify_matches_the_pairwise_definition():
    previous, current = random_jobs(250, 1), random_jobs(250, 2)
    result = classify(current, previous)
    known = [j for j in current if naive_first_match(j, previous) is not None]
    new = [j for j in current if naive_first_match(j, previous) is None]
    unique = []
    for job in new:
        if naive_first_match(job, unique) is None:
            unique.append(job)
    assert [j["n"] for j in result["known"]] == [j["n"] for j in known]
    assert [j["n"] for j in result["new"]] == [j["n"] for j in unique]
    assert len(result["duplicates"]) == len(new) - len(unique)


def test_pipeline_deduplication_scales_to_a_real_morning():
    """Thousands of offers a day: the old pairwise scan took tens of minutes, the index a few seconds."""
    # 6,000 postings, each company's offer seen on two sources: 3,000 distinct offers.
    jobs = [
        {
            "source": "lever" if i < 3000 else "google_jobs",
            "external_id": str(i),
            "company": f"Company {i % 3000}",
            "title": "Data Engineer",
            "location": "Paris" if i < 3000 else "Paris, France",
            "description": "Python SQL",
        }
        for i in range(6000)
    ]
    start = time.perf_counter()
    result = run_pipeline(jobs, *config.scoring_inputs())
    assert len(result) == 3000
    assert time.perf_counter() - start < 60
