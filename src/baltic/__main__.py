"""The command line, the only place that reads arguments and environment: `python -m baltic <command>`.

Every command is a thin adapter from arguments to one library function, in pipeline order.
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from baltic import article, gdelt
from baltic.layout import Layout

Args = argparse.Namespace


def produce(a: Args, layout: Layout) -> int:
    """Speed layer 1: publish GDELT files to Kafka from --since on, forever."""
    from baltic.stream.producer import Ingestor, KafkaSink

    Ingestor(layout, KafkaSink(a.kafka, a.topic), gdelt.session(), a.since).run()
    return 0


def monitor(a: Args, layout: Layout) -> int:
    """Speed layer 2: consume, close slots, alert, forever."""
    from baltic.stream.monitor import Monitor, consume

    consume(Monitor(layout), a.kafka, a.topic)
    return 0


def seed(_a: Args, layout: Layout) -> int:
    """Warm-start the monitor from the batch layer's history."""
    from baltic.stream.seed import seed as warm_start

    print("seeded" if warm_start(layout) else "state exists, left as it is")
    return 0


def mirror(a: Args, layout: Layout) -> int:
    """Backfill raw files; a failed download stops it with an error (re-run to resume)."""
    from baltic.batch.mirror import mirror as backfill

    print(json.dumps(backfill(layout, gdelt.session(), a.day, a.days)))
    return 0


def etl(a: Args, layout: Layout) -> int:
    """Batch layer: Spark over the raw files into the lake (or the live lake)."""
    from baltic.batch import etl as job

    files = layout.raw_files(a.day, a.days)
    lake = layout.live_lake if a.live else layout.lake
    print(json.dumps(job.run(files, a.workers, lake, os.environ.get("SPARK_MEM", "6g"))))
    return 0


def measure(a: Args, layout: Layout) -> int:
    """One benchmark run, printed as JSON (bench runs this in a fresh process)."""
    from baltic.batch import baseline, etl

    files = layout.raw_files(a.day, a.days)
    result = (
        etl.run(files, a.workers, None) if a.engine == "spark" else baseline.run(files, a.workers)
    )
    print(json.dumps(result))
    return 0


def bench(a: Args, layout: Layout) -> int:
    """The scaling study: E1, E2, E3, repeated and shuffled."""
    from baltic.batch.bench import configs, run_all

    print(f"{run_all(layout, a.day, configs(tuple(a.days), tuple(a.cores)), a.repeats)} runs done")
    return 0


def match(a: Args, layout: Layout) -> int:
    """AI: match ru_by articles to Baltic ones via the embedding service and via shared names."""
    from baltic.ai.match import HIGH_COS, MIN_SHARED, Embedder, google_token, load
    from baltic.ai.match import match as pair

    token = google_token(a.embedder) if a.embedder.startswith("https://") else None
    pairs = pair(*load(layout.lake), Embedder(a.embedder, token))
    layout.pairs.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_parquet(layout.pairs)
    print(
        f"{len(pairs)} pairs; cos >= {HIGH_COS}: {(pairs.emb_cos >= HIGH_COS).mean():.1%}; "
        f"entity matched: {(pairs.ent_shared >= MIN_SHARED).mean():.1%}"
    )
    return 0


def sample(_a: Args, layout: Layout) -> int:
    """Write the blind labelling sheets (pairs, detector hours) and their hidden keys."""
    from baltic.ai import evaluate
    from baltic.detector import SpikeDetector, backtest
    from baltic.stream.seed import history

    table = history(layout.lake)
    alerts = backtest(table, SpikeDetector(article.GROUPS))
    articles = pd.read_parquet(
        layout.lake.silver, columns=["ts", "group", "title", "about_baltic", "security"]
    )
    sheets = {
        "pairs": evaluate.sample_pairs(pd.read_parquet(layout.pairs)),
        "slots": evaluate.sample_slots(table, alerts, articles),
    }
    for name, (sheet, key) in sheets.items():
        sheet.to_csv(layout.labels(name), index=False)
        key.to_csv(layout.labels(name, key=True), index=False)
        print(f"{len(sheet)} items to label in {layout.labels(name)}")
    return 0


def evaluate(_a: Args, layout: Layout) -> int:
    """Score the filled-in sheets: precision per stratum (H3) and the detector's blind check."""
    from baltic.ai import evaluate as ev

    def read(name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        return (
            pd.read_csv(layout.labels(name), dtype=str, keep_default_na=False),
            pd.read_csv(layout.labels(name, key=True), dtype=str),
        )

    precision = ev.score(*read("pairs"), by="stratum")
    result = {
        "precision": precision,
        "H3": ev.h3(precision),
        "detector": ev.score(*read("slots"), by="alert"),
    }
    layout.evaluation.write_text(json.dumps(result, indent=1))
    print(json.dumps(result["H3"]))
    return 0


def reconcile(_a: Args, layout: Layout) -> int:
    """Check: Spark row counts = the independent collector's, day by day."""
    from baltic.analysis import checks

    result = checks.reconcile(layout.lake, checks.reference())
    print(json.dumps(result, indent=1))
    return 0 if result["of"] and result["ok"] == result["of"] else 1


def stream_vs_batch(a: Args, layout: Layout) -> int:
    """Check: the monitor's slots = Spark over the same raw files, up to the horizon."""
    from baltic.analysis import checks
    from baltic.stream.monitor import read_slots

    horizon = a.horizon or gdelt.to_ts(
        datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=30)
    )
    result = checks.stream_vs_batch(read_slots(layout.slots), layout.live_lake, horizon)
    (layout.report).mkdir(parents=True, exist_ok=True)
    (layout.report / "stream_vs_batch.json").write_text(json.dumps(result))
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def report(_a: Args, layout: Layout) -> int:
    """Every figure and summary.json."""
    from baltic.analysis.report import build

    print(", ".join(build(layout)) or "nothing to report yet")
    return 0


def parser() -> argparse.ArgumentParser:
    """All commands and their arguments."""
    p = argparse.ArgumentParser(prog="baltic", description=__doc__)
    p.add_argument("--data", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    p.add_argument("--kafka", default=os.environ.get("KAFKA", "localhost:9092"))
    p.add_argument("--topic", default=os.environ.get("TOPIC", "gkg.raw"))
    sub = p.add_subparsers(required=True, metavar="command")

    def command(fn: Callable[[Args, Layout], int], *arguments: tuple[str, dict[str, Any]]) -> None:
        c = sub.add_parser(fn.__name__.replace("_", "-"), help=fn.__doc__)
        for name, options in arguments:
            c.add_argument(name, **options)
        c.set_defaults(run=fn)

    day, days = ("day", {"help": "first day, YYYY-MM-DD"}), ("days", {"type": int})
    command(
        produce, ("--since", {"default": os.environ.get("SINCE") or None, "help": "YYYYMMDDHHMMSS"})
    )
    command(monitor)
    command(seed)
    command(mirror, day, days)
    command(
        etl,
        day,
        days,
        ("--workers", {"type": int, "default": os.cpu_count()}),
        ("--live", {"action": "store_true"}),
    )
    command(
        measure,
        ("engine", {"choices": ["spark", "python"]}),
        ("--day", {"required": True}),
        ("--days", {"type": int, "required": True}),
        ("--workers", {"type": int, "required": True}),
    )
    command(
        bench,
        day,
        ("--days", {"type": int, "nargs": "+", "default": [1, 7, 30]}),
        ("--cores", {"type": int, "nargs": "+", "default": [1, 2, 4, 8]}),
        ("--repeats", {"type": int, "default": 3}),
    )
    command(match, ("--embedder", {"default": os.environ.get("EMBEDDER", "http://localhost:8080")}))
    command(sample)
    command(evaluate)
    command(reconcile)
    command(stream_vs_batch, ("--horizon", {"help": "last slot to compare, default now - 30 min"}))
    command(report)
    return p


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run one command."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    a = parser().parse_args(argv)
    return int(a.run(a, Layout(a.data)))


if __name__ == "__main__":
    sys.exit(main())
