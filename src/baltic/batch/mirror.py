"""Backfill: download GDELT's raw zips for a date range into the same layout the producer archives into."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

from baltic import gdelt
from baltic.layout import Layout, write_atomic

WORKERS = 12  # I/O bound: threads overlap the downloads (gdelt.session's pool holds 16)


def mirror(layout: Layout, http: requests.Session, day: str, days: int, until: str) -> Counter[str]:
    """Fetch every missing file of `days` days from day (YYYY-MM-DD), all before `until`; idempotent.

    Returns outcome counts. Files from `until` on belong to the producer: archived here, they would never
    reach Kafka, and a fresh 404 there may only mean "not uploaded yet".
    """

    def fetch(job: tuple[str, str]) -> str:
        feed, ts = job
        data = gdelt.download(feed, ts, http)
        if data is None:
            write_atomic(layout.missing(feed, ts), b"")
            return "missing"
        write_atomic(layout.raw(feed, ts), data)
        return "downloaded"

    jobs = [(f, ts) for ts in gdelt.day_slots(day, days) for f in gdelt.FEEDS]
    if jobs[-1][1] >= until:
        raise ValueError(f"mirror only fetches slots before {until} (the producer's live window)")
    todo = [job for job in jobs if not layout.has(*job)]
    with ThreadPoolExecutor(WORKERS) as pool:
        outcome = Counter(pool.map(fetch, todo))
    outcome["already there"] = len(jobs) - len(todo)
    return outcome
