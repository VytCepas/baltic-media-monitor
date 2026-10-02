"""Evaluation by blind hand labels: the AI matcher (H3) and the spike detector.

`sample` writes two sheets to fill in (y/n in the `label` column) and two hidden keys:
- labels_pairs.csv: matched headline pairs, "same story?", drawn from four strata of 30:
      emb_top   the top embedding pairs, as many as the entity baseline matched (EQUAL COVERAGE)
      emb_high  cosine >= 0.7        emb_mid  0.5 <= cosine < 0.7        ent  >= 2 shared names
- labels_slots.csv: an hour of one group's security headlines, "a real event?": the 10 strongest
  detector alerts mixed with 10 busy hours that did not alert (the control).
The labeller sees neither the method nor whether an hour alerted. `score` joins labels and keys.
"""

from dataclasses import asdict
from typing import Any

import pandas as pd

from baltic import gdelt
from baltic.ai.match import HIGH_COS, MIN_SHARED
from baltic.detector import Alert
from baltic.stats import wilson

SEED = 7
BUSY = 8  # a busy hour: as many security articles as the detector's own minimum for an alert


def strata(pairs: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """The four sampling strata as (russian_title, baltic_title) frames."""
    ent = pairs[pairs.ent_shared >= MIN_SHARED]
    emb = pairs[pairs.emb_cos >= 0].sort_values("emb_cos", ascending=False)

    def titles(df: pd.DataFrame, method: str) -> pd.DataFrame:
        return pd.DataFrame({"russian_title": df.src_title, "baltic_title": df[f"{method}_title"]})

    return {
        "emb_top": titles(emb.head(len(ent)), "emb"),
        "emb_high": titles(emb[emb.emb_cos >= HIGH_COS], "emb"),
        "emb_mid": titles(emb[emb.emb_cos.between(0.5, HIGH_COS, inclusive="left")], "emb"),
        "ent": titles(ent, "ent"),
    }


def blind(items: pd.DataFrame, shown: list[str], seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Number the distinct items in a random order: (sheet with an empty label column, key)."""
    sheet = items[shown].drop_duplicates().sample(frac=1, random_state=seed).reset_index(drop=True)
    sheet.insert(0, "item", range(1, len(sheet) + 1))
    return sheet.assign(label=""), items.merge(sheet, on=shown)


def sample_pairs(
    pairs: pd.DataFrame, n: int = 30, seed: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """N pairs per stratum; a pair drawn by two strata is labelled once and counts for both."""
    drawn = [
        df.sample(min(n, len(df)), random_state=seed).assign(stratum=name)
        for name, df in strata(pairs).items()
    ]
    return blind(pd.concat(drawn), ["russian_title", "baltic_title"], seed)


def hours(table: pd.DataFrame, alerts: list[Alert]) -> pd.DataFrame:
    """Every (slot, group) with its rolling-hour count and whether the detector alerted there."""
    hourly = table.rolling(4, min_periods=1).sum().rename_axis(index="ts").reset_index()
    long = hourly.melt(id_vars="ts", var_name="group", value_name="count")
    alerted = {(a["slot"], a["group"]) for a in alerts}
    long["alert"] = [(t, g) in alerted for t, g in zip(long.ts, long.group, strict=True)]
    return long


def sample_slots(
    table: pd.DataFrame, alerts: list[Alert], articles: pd.DataFrame, n: int = 10
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The n strongest alerts and n busy (rolling hour >= BUSY) hours without one, with headlines."""
    h = hours(table, alerts)
    strongest = pd.DataFrame(
        sorted(alerts, key=lambda a: -a["score"])[:n], columns=list(Alert.__annotations__)
    )
    picked = h.merge(strongest.rename(columns={"slot": "ts"})[["ts", "group"]])
    quiet = h[~h.alert & (h["count"] >= BUSY)]
    items = pd.concat([picked, quiet.sample(min(n, len(quiet)), random_state=SEED)])
    security = articles[articles.about_baltic & articles.security]

    def headlines(ts: str, group: str) -> str:
        mine = security[(security.group == group) & security.ts.between(gdelt.shift(ts, -3), ts)]
        return " | ".join(mine.title.head(8))

    items["headlines"] = [headlines(t, g) for t, g in zip(items.ts, items.group, strict=True)]
    return blind(items, ["group", "ts", "headlines"], SEED)


def score(sheet: pd.DataFrame, key: pd.DataFrame, by: str) -> dict[str, Any]:
    """Share of 'y' labels per value of `by`, with Wilson intervals; unlabelled items are left out."""
    # anything else ("not sure", "?") is left out
    answer = {"y": "y", "yes": "y", "n": "n", "no": "n"}
    labels = sheet.set_index("item").label.astype(str).str.strip().str.lower().map(answer)
    done = key.assign(label=key["item"].map(labels)).dropna(subset=["label"])
    out = {}
    for value, g in done.groupby(by):
        k = int((g.label == "y").sum())
        out[str(value)] = {"k": k, "n": len(g), **asdict(wilson(k, len(g)))}
    return out


def h3(precision: dict[str, Any]) -> dict[str, Any]:
    """H3: at equal coverage, the embedding precision's lower bound beats the entity upper bound."""
    if not {"emb_top", "ent"} <= precision.keys():
        return {"decided": False}
    shown = precision["emb_top"]["low"] > precision["ent"]["high"]
    return {
        "decided": True,
        "shown": shown,
        "emb_top": precision["emb_top"],
        "ent": precision["ent"],
    }
