"""Backfill: download GDELT's raw zips for a date range into the same layout the producer archives into."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

from baltic import gdelt
from baltic.layout import Layout, write_atomic


def mirror(
    layout: Layout, http: requests.Session, day: str, days: int, workers: int = 12
) -> Counter[str]:
    """Fetch every missing file of `days` days from day (YYYY-MM-DD); idempotent. Returns outcome counts."""

    def fetch(job: tuple[str, str]) -> str:
        feed, ts = job
        data = gdelt.download(feed, ts, http)
        if data is None:
            write_atomic(layout.missing(feed, ts), b"")
            return "missing"
        write_atomic(layout.raw(feed, ts), data)
        return "downloaded"

    jobs = [(f, ts) for ts in gdelt.day_slots(day, days) for f in gdelt.FEEDS]
    todo = [job for job in jobs if not layout.has(*job)]
    with ThreadPoolExecutor(workers) as pool:  # I/O bound: threads overlap the downloads
        outcome = Counter(pool.map(fetch, todo))
    outcome["already there"] = len(jobs) - len(todo)
    return outcome
