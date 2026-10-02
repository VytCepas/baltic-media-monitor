"""The file-system schema: every path the pipeline reads or writes is built here, and nowhere else.

data/                                    the bucket gs://<bucket>/ mirrors this tree 1:1
  raw/feed={en,tr}/dt=YYYYMMDD/TS.zip    GDELT files as downloaded; TS.missing = GDELT never published it
  lake/silver/articles/dt=YYYY-MM-DD/    relevant articles, Parquet          (batch layer, Sep 1-30)
  lake/gold/{domain_day,theme_group,series_15m}/   aggregates, Parquet
  lake-live/...                          the same tables over the live window (stream = batch check)
  monitor/{slots,alerts}.jsonl           speed-layer output, one line per closed slot / per alert
  monitor/state.pkl                      detector + last closed slot (warm-started by `seed`)
  ai/{pairs.parquet,labels_*.csv,evaluation.json}
  bench/TAG-rN.json                      one file per benchmark run
  report/{summary.json,*.png}            every number and figure the report quotes
"""

from dataclasses import dataclass
from pathlib import Path

from baltic import gdelt


def write_atomic(path: Path, data: bytes) -> None:
    """Write via a temporary file and rename, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


@dataclass(frozen=True)
class Lake:
    """A Spark output tree: silver articles plus gold aggregate tables."""

    root: Path

    @property
    def silver(self) -> Path:
        """Relevant articles, partitioned by day."""
        return self.root / "silver" / "articles"

    def gold(self, table: str) -> Path:
        """One gold aggregate table."""
        return self.root / "gold" / table

    @property
    def done(self) -> bool:
        """True once Spark finished writing the last table (it writes _SUCCESS on commit)."""
        return (self.gold("series_15m") / "_SUCCESS").exists()


@dataclass(frozen=True)
class Layout:
    """All paths under one data root."""

    root: Path

    def raw(self, feed: str, ts: str) -> Path:
        """The archived zip of one feed's slot."""
        return self.root / "raw" / f"feed={feed}" / f"dt={ts[:8]}" / f"{ts}.zip"

    def missing(self, feed: str, ts: str) -> Path:
        """Marker: GDELT never published this file."""
        return self.raw(feed, ts).with_suffix(".missing")

    def has(self, feed: str, ts: str) -> bool:
        """True when the slot is settled on disk: archived, or known to be missing."""
        return self.raw(feed, ts).exists() or self.missing(feed, ts).exists()

    def raw_files(self, day: str, days: int) -> list[Path]:
        """Every archived zip (both feeds) of `days` days from day (YYYY-MM-DD), in time order."""
        paths = (self.raw(f, ts) for ts in gdelt.day_slots(day, days) for f in gdelt.FEEDS)
        return [p for p in paths if p.exists()]

    @property
    def lake(self) -> Lake:
        """The batch layer's lake (backfilled history)."""
        return Lake(self.root / "lake")

    @property
    def live_lake(self) -> Lake:
        """The same tables, rebuilt over the live window."""
        return Lake(self.root / "lake-live")

    @property
    def slots(self) -> Path:
        """Speed-layer output: one JSON line per closed slot."""
        return self.root / "monitor" / "slots.jsonl"

    @property
    def alerts(self) -> Path:
        """Speed-layer output: one JSON line per alert."""
        return self.root / "monitor" / "alerts.jsonl"

    @property
    def state(self) -> Path:
        """The monitor's saved detector and last closed slot."""
        return self.root / "monitor" / "state.pkl"

    @property
    def pairs(self) -> Path:
        """Matched headline pairs."""
        return self.root / "ai" / "pairs.parquet"

    def labels(self, sheet: str, key: bool = False) -> Path:
        """A blind labelling sheet ("pairs" or "slots"), or its hidden answer key."""
        return self.root / "ai" / f"labels_{sheet}{'.key' if key else ''}.csv"

    @property
    def evaluation(self) -> Path:
        """Scored labels: matcher precision (H3) and the detector check."""
        return self.root / "ai" / "evaluation.json"

    @property
    def report(self) -> Path:
        """summary.json and the figures."""
        return self.root / "report"

    def bench(self, tag: str = "*", repeat: int | str = "*") -> Path:
        """Result file of one benchmark run (the defaults make it a glob pattern for all runs)."""
        return self.root / "bench" / f"{tag}-r{repeat}.json"
