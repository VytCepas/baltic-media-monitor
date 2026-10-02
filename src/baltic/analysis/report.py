"""The serving layer: every number the report quotes (summary.json) and every figure, from the data tree.

Each part is skipped while its input does not exist yet, so the report can be rebuilt at any stage.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from matplotlib.figure import Figure  # the object API: no pyplot state, no display backend

from baltic import article, gdelt
from baltic.analysis import hypotheses
from baltic.detector import SpikeDetector, backtest
from baltic.layout import Layout
from baltic.stream import seed
from baltic.stream.monitor import read_slots

LABEL = {
    "ru_by": "Russia/Belarus",
    "regional": "neighbours",
    "other": "other foreign",
    "baltic": "Baltic",
    "baltic_rus": "Baltic (Russian)",
}
COLOR = {
    "ru_by": "#c0392b",
    "regional": "#e67e22",
    "other": "#7f8c8d",
    "baltic": "#2980b9",
    "baltic_rus": "#8e44ad",
}
STEADY_S = 3600  # a slot closed within an hour of its end was not part of a catch-up
E2_DAYS = 7  # E2 (cores) is measured on this many days


def save(fig: Figure, path: Path) -> None:
    """Write a figure as PNG."""
    fig.savefig(path, dpi=150)


def attention_figure(h1: dict[str, Any], path: Path) -> None:
    """Figure 1: share of each foreign group's articles that are about the Baltics (H1 compares these)."""
    fig = Figure(figsize=(6.5, 3.2), layout="tight")
    ax = fig.subplots()
    groups = [g for g in ("ru_by", "regional", "other") if g in h1["share"]]
    bars = ax.bar(
        [LABEL[g] for g in groups],
        [100 * h1["share"][g] for g in groups],
        color=[COLOR[g] for g in groups],
    )
    ax.bar_label(bars, fmt="%.2f %%")
    ax.set_ylabel("% of the group's articles about LT/LV/EE")
    ax.set_title("H1 attention to the Baltics")
    save(fig, path)


def theme_figure(themes: pd.DataFrame, path: Path, top: int = 8) -> dict[str, float]:
    """Figure 2: themes over- and under-represented in ru_by coverage vs other foreign outlets."""
    counts = themes.pivot_table(index="theme", columns="group", values="count", fill_value=0)
    share = counts / counts.sum()
    frequent = counts[(counts[["ru_by", "other", "baltic"]] >= 100).all(axis=1)].index
    lexical = ("TAX_WORLDLANGUAGES_", "TAX_ETHNICITY_")  # fire on the word "Russian" itself
    common = [t for t in frequent if not str(t).startswith(lexical)]
    ratio = np.log(share.loc[common, "ru_by"] / share.loc[common, "other"]).sort_values()
    pick = ratio if len(ratio) <= 2 * top else pd.concat([ratio.head(top), ratio.tail(top)])
    fig = Figure(figsize=(7, 5), layout="tight")
    ax = fig.subplots()
    ax.barh(
        [t[:40] for t in pick.index],
        pick,
        color=np.where(pick > 0, COLOR["ru_by"], COLOR["baltic"]),
    )
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("log(share in ru_by coverage / share in other foreign coverage)")
    ax.set_title("Themes in coverage of the Baltics")
    save(fig, path)
    return {str(t): round(float(v), 3) for t, v in pick.items()}


def tone_figure(h2: dict[str, Any], path: Path) -> None:
    """Figure 3: mean tone per group, security vs other stories."""
    tone = pd.DataFrame(h2["mean_tone"]).reindex(article.GROUPS).dropna(how="all")
    tone.columns = pd.Index(["security" if c else "other stories" for c in tone.columns])
    fig = Figure(figsize=(8, 3.8), layout="tight")
    ax = fig.subplots()
    tone.rename(index=LABEL).plot.bar(ax=ax, rot=15, color=["#bdc3c7", "#34495e"])
    ax.legend(loc="upper left", bbox_to_anchor=(1, 1), frameon=False)  # beside, not over, the bars
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("mean tone (GDELT, % pos. - % neg. words)")
    ax.set_title("H2 tone of articles about the Baltics")
    save(fig, path)


def scaling(bench: pd.DataFrame, days: int = E2_DAYS) -> dict[str, Any]:
    """Median and range of the repeats per configuration, E2 speed-up and efficiency, the E3 rule."""
    runs = bench.groupby(["engine", "days", "workers"]).wall_s.agg(
        ["median", "min", "max", "count"]
    )
    runs = runs.reset_index()
    out: dict[str, Any] = {"runs": runs.to_dict("records")}
    spark = runs[(runs.engine == "spark") & (runs.days == days)].set_index("workers")
    pool = runs[(runs.engine == "python") & (runs.days == days)].set_index("workers")
    if 1 in spark.index:
        speedup = spark["median"].loc[1] / spark["median"]
        out["speedup"] = speedup.to_dict()
        out["efficiency"] = (speedup / speedup.index).to_dict()  # speed-up per worker; 1 = ideal
    if len(pool) and (top := pool.index.max()) in spark.index:
        # "Spark beats parallel Python" only if its median is below the Pool's fastest repeat
        out["spark_beats_pool"] = bool(spark.loc[top, "median"] < pool.loc[top, "min"])
    return out


def scaling_figure(bench: pd.DataFrame, path: Path) -> None:
    """Figure 4: E1 wall time vs data size, E2 speed-up vs cores (median, range of repeats)."""
    fig = Figure(figsize=(10, 3.6), layout="tight")
    (left, right) = fig.subplots(1, 2)
    for (engine, workers), g in bench.groupby(["engine", "workers"]):
        s = g.groupby("days").wall_s.agg(["median", "min", "max"])
        if len(s) > 1:
            err = [s["median"] - s["min"], s["max"] - s["median"]]
            left.errorbar(
                s.index, s["median"], yerr=err, marker="o", capsize=3, label=f"{engine} x{workers}"
            )
    left.set(xlabel="days of GDELT (both feeds)", ylabel="wall time, s", title="E1 data size")
    if left.get_legend_handles_labels()[0]:
        left.legend()
    spark = bench[(bench.engine == "spark") & (bench.days == E2_DAYS)]
    e2 = spark.groupby("workers").wall_s.median()
    if len(e2) > 1:
        right.plot(e2.index, e2.iloc[0] / e2, "o-", label="measured")
        right.plot(e2.index, e2.index / e2.index[0], "--", label="ideal")
        right.set(
            xlabel="Spark worker threads (local[n])",
            ylabel="speed-up",
            title=f"E2 cores, {E2_DAYS} days",
        )
        right.legend()
    save(fig, path)


def detector_figure(table: pd.DataFrame, alerts: list[Any], path: Path) -> None:
    """Figure 5: rolling-hour security counts per group with the backtest's alerts."""
    fig = Figure(figsize=(11, 3.5), layout="tight")
    ax = fig.subplots()
    times = pd.to_datetime(table.index, format=gdelt.TS)
    for g in table.columns:
        ax.plot(times, table[g].rolling(4).sum(), lw=0.7, color=COLOR[g], label=LABEL[g])
    for a in alerts:
        t = gdelt.to_time(a["slot"])  # a datetime: matplotlib converts it, its stubs say float
        ax.axvline(t, color=COLOR[a["group"]], alpha=0.4)  # type: ignore[arg-type]
    ax.set_yscale("symlog")
    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.1), frameon=False)
    ax.set_title(f"Security coverage of the Baltics, rolling hour; {len(alerts)} alerts")
    save(fig, path)


def live(slots: pd.DataFrame, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """The speed layer in operation: slots closed, alerts per group, steady-state pipeline lag."""
    slots = slots.drop_duplicates("slot", keep="last")
    end = gdelt.epoch_s(slots.slot) + gdelt.SLOT.seconds
    steady = slots[slots.closed_at - end < STEADY_S]
    lag = steady.closed_at - steady.sent_at
    return {
        "slots": len(slots),
        "first": slots.slot.min(),
        "last": slots.slot.max(),
        "alerts": dict(Counter(a["group"] for a in alerts)),
        "steady_slots": len(steady),
        "lag_p50_s": float(lag.median()) if len(lag) else None,
        "lag_p95_s": float(lag.quantile(0.95)) if len(lag) else None,
        "rows_per_slot": float(slots.rows.median()),
        "about_baltic_per_slot": float(slots.about_baltic.median()),
    }


def build(layout: Layout) -> dict[str, Any]:
    """Compute every available part, write figures and summary.json; returns the summary."""
    out, lake, summary = layout.report, layout.lake, {}
    out.mkdir(parents=True, exist_ok=True)
    if lake.done:
        summary["H1"] = hypotheses.h1(pd.read_parquet(lake.gold("domain_day")))
        summary["H2"] = hypotheses.h2(pd.read_parquet(lake.silver))
        attention_figure(summary["H1"], out / "1_attention.png")
        summary["themes"] = theme_figure(
            pd.read_parquet(lake.gold("theme_group")), out / "2_themes.png"
        )
        tone_figure(summary["H2"], out / "3_tone.png")
        table = seed.history(lake)
        alerts = backtest(table, SpikeDetector(article.GROUPS))
        detector_figure(table, alerts, out / "5_detector.png")
        summary["backtest"] = {"slots": len(table), "alerts": alerts}
    if runs := sorted(layout.bench().parent.glob(layout.bench().name)):
        bench = pd.DataFrame([json.loads(p.read_text()) for p in runs])
        summary["scaling"] = scaling(bench)
        scaling_figure(bench, out / "4_scaling.png")
    if layout.slots.exists():
        lines = layout.alerts.read_text().splitlines() if layout.alerts.exists() else []
        summary["live"] = live(read_slots(layout.slots), [json.loads(x) for x in lines])
    if layout.evaluation.exists():
        summary["ai"] = json.loads(layout.evaluation.read_text())
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary
