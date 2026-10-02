"""Correctness evidence, each with a pass/fail verdict.

- reconcile: Spark's per-day row counts equal an independent collector's (reference_days.json, written by
  a separate script in September); relevant articles agree within 0.5 % (Spark de-duplicates ids).
- stream_vs_batch: every slot the monitor closed agrees with Spark's series over the same raw files:
  no slot missing and equal security counts per group. Duplicate lines (a slot re-closed after a crash
  between writing and saving state: at-least-once) are counted and reported, and do not fail the check.
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
    """Compare every day both sources cover."""
    dd = pd.read_parquet(lake.gold("domain_day"), columns=["dt", "n_rows"])
    rows = dd.groupby(pd.to_datetime(dd.dt).dt.strftime("%Y%m%d")).n_rows.sum()
    silver = pd.read_parquet(lake.silver, columns=["dt"])
    relevant = silver.groupby(pd.to_datetime(silver.dt).dt.strftime("%Y%m%d")).size()
    days = {}
    for day in sorted(set(rows.index) & ref.keys()):
        rel_diff = abs(int(relevant.get(day, 0)) - ref[day]["relevant"]) / ref[day]["relevant"]
        ok = int(rows[day]) == ref[day]["rows"] and rel_diff <= 0.005
        days[day] = {
            "rows": int(rows[day]),
            "ref_rows": ref[day]["rows"],
            "relevant_diff": rel_diff,
            "ok": ok,
        }
    return {"days": days, "ok": sum(d["ok"] for d in days.values()), "of": len(days)}


def stream_vs_batch(slots: pd.DataFrame, lake: Lake, horizon: str) -> dict[str, Any]:
    """Monitor slots (slots.jsonl rows) against the live lake, up to horizon (YYYYMMDDHHMMSS)."""
    slots = slots.assign(slot=slots.slot.astype(str))
    slots = slots[slots.slot <= horizon]
    unique = slots.drop_duplicates("slot", keep="last").set_index("slot").sort_index()
    expected = gdelt.slots(unique.index[0], unique.index[-1]) if len(unique) else []
    stream = pd.DataFrame(list(unique.security), index=unique.index).fillna(0).astype(int)
    series = pd.read_parquet(lake.gold("series_15m"))
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
        "ok": not differ.any() and set(expected) <= set(unique.index),
    }
