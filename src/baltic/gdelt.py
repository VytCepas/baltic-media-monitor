"""GDELT 2.0 as a data source: every 15 minutes one GKG zip per feed, named by its slot timestamp.

A slot timestamp ("ts") is a 14-character UTC string, YYYYMMDDHHMMSS, and is the key the whole pipeline
uses: Kafka message key, raw file name, monitor slot, Spark series row.
"""

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import datetime, timedelta

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# GDELT's bucket via the storage endpoint: the data.gdeltproject.org host caches a 404 for an hour when
# anyone asked before the upload (seen from GCE: every translated file an hour late).
BASE = "https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/"
FEEDS = ("en", "tr")  # English, and ~65 languages machine-translated into English
INDEX = {"en": BASE + "lastupdate.txt", "tr": BASE + "lastupdate-translation.txt"}
TS = "%Y%m%d%H%M%S"
SLOT = timedelta(minutes=15)

csv.field_size_limit(2**31 - 1)  # a GKG field can exceed csv's 128 kB default


def to_time(ts: str) -> datetime:
    """Slot timestamp -> datetime."""
    return datetime.strptime(ts, TS)


def to_ts(t: datetime) -> str:
    """Datetime -> slot timestamp."""
    return t.strftime(TS)


def shift(ts: str, slots: int) -> str:
    """The slot `slots` steps (15 minutes each) after ts; negative steps go back."""
    return to_ts(to_time(ts) + slots * SLOT)


def slots(first: str, last: str) -> list[str]:
    """Every slot timestamp from first to last, inclusive, oldest first."""
    return list(pd.date_range(to_time(first), to_time(last), freq=SLOT).strftime(TS))


def day_slots(day: str, days: int = 1) -> list[str]:
    """Every slot of `days` days starting at day (YYYY-MM-DD)."""
    start = datetime.fromisoformat(day)
    return slots(to_ts(start), to_ts(start + timedelta(days=days) - SLOT))


def file_url(feed: str, ts: str) -> str:
    """Download URL of one GKG file."""
    return f"{BASE}{ts}.{'translation.' if feed == 'tr' else ''}gkg.csv.zip"


def session() -> requests.Session:
    """HTTP session that retries connection errors and 5xx answers with backoff."""
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))  # mirror runs 12 threads
    return s


def latest(feed: str, http: requests.Session) -> str:
    """Slot timestamp of the newest GKG file GDELT lists for a feed."""
    r = http.get(INDEX[feed], timeout=30)
    r.raise_for_status()
    url = next(line for line in r.text.split() if line.endswith(".gkg.csv.zip"))
    return url.rsplit("/", 1)[1][:14]


def download(feed: str, ts: str, http: requests.Session) -> bytes | None:
    """The zip's bytes; None only when GDELT answers 404 (it skipped that file). Other failures raise."""
    r = http.get(file_url(feed, ts), timeout=120)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def read_rows(data: bytes) -> Iterator[list[str]]:
    """The tab-separated rows of the CSV inside a GKG zip, streamed."""
    with zipfile.ZipFile(io.BytesIO(data)) as z, z.open(z.namelist()[0]) as fh:
        # the same decoding and quoting as the independent reference collector, so row counts reconcile
        yield from csv.reader(
            io.TextIOWrapper(fh, encoding="utf-8", errors="ignore"), delimiter="\t"
        )
