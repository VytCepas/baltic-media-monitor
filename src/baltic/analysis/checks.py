"""Correctness evidence, each with a pass/fail verdict.

- reconcile: Spark's per-day row counts equal an independent collector's (reference_days.json, written by
  a separate script in September); relevant articles agree within 0.5 % (Spark de-duplicates by article key).
- stream_vs_batch: every slot from the live start to the horizon was closed by the monitor and agrees
  with Spark's series over the same raw files (equal security counts per group). Duplicate lines (a slot
  re-closed after a crash between writing and saving state: at-least-once) are counted, and do not fail.
"""

import json
from importlib import resources
from typing import Any

import pandas as pd

from baltic import gdelt
from baltic.layout import Lake


def reference() -> dict[str, dict[str, int]]:
    """Per-day row and relevant counts from the independent collector (research/gdelt-30d)."""
    ref: dict[str, dict[str, int]] = json.loads(
        resources.files(__package__).joinpath("reference_days.json").read_text()
    )
    return ref


def reconcile(lake: Lake, ref: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Compare every reference day; a day the lake lacks fails."""

    def ymd(dt: pd.Series) -> pd.Series:
        return pd.to_datetime(dt).dt.strftime("%Y%m%d")

    dd = pd.read_parquet(lake.gold("domain_day"), columns=["dt", "n_rows"])
    rows = dd.groupby(ymd(dd.dt)).n_rows.sum()
    silver = pd.read_parquet(lake.silver, columns=["dt"])
    relevant = silver.groupby(ymd(silver.dt)).size()
    days = {}
    for day in sorted(ref):
        n = int(rows.get(day, 0))
        rel_diff = abs(int(relevant.get(day, 0)) - ref[day]["relevant"]) / ref[day]["relevant"]
        ok = n == ref[day]["rows"] and rel_diff <= 0.005
        days[day] = {"rows": n, "ref_rows": ref[day]["rows"], "relevant_diff": rel_diff, "ok": ok}
    return {"days": days, "ok": sum(d["ok"] for d in days.values()), "of": len(days)}


def stream_vs_batch(
    slots: pd.DataFrame, lake: Lake, horizon: str, start: str = ""
) -> dict[str, Any]:
    """Monitor slots (slots.jsonl rows) against the live lake, from start to horizon (YYYYMMDDHHMMSS).

    start defaults to the lake's first slot. Every slot in between is expected, so a stalled or empty
    monitor fails.
    """
    series = pd.read_parquet(lake.gold("series_15m"))
    start = start or series.ts.min()
    slots = slots[slots.slot.between(start, horizon)]
    unique = slots.drop_duplicates("slot", keep="last").set_index("slot").sort_index()
    expected = gdelt.slots(start, horizon)
    stream = pd.DataFrame(list(unique.security), index=unique.index).fillna(0).astype(int)
    batch = series.pivot_table(index="ts", columns="group", values="count", aggfunc="sum")
    columns = stream.columns.union(batch.columns)
    batch = batch.reindex(index=stream.index, columns=columns).fillna(0).astype(int)
    stream = stream.reindex(columns=columns, fill_value=0)
    differ = (stream != batch).any(axis=1)
    return {
        "slots": len(unique),
        "duplicates": len(slots) - len(unique),
        "missing": len(set(expected) - set(unique.index)),
        "differ": int(differ.sum()),
        "security_stream": int(stream.to_numpy().sum()),
        "security_batch": int(batch.to_numpy().sum()),
        "ok": bool(expected) and not differ.any() and set(expected) <= set(unique.index),
    }
