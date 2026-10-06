"""Spike detector: is a group's rolling-hour count of security articles far above its usual for that hour?

"Usual" is the median of the same hour on previous similar days (weekday vs weekend, as news volume halves
at weekends), with the median absolute deviation (MAD) as the spread: robust to the spikes it looks for.
A group alerts on the rising edge only and re-arms once it falls back, so one event gives one alert.
The live monitor and the offline backtest run this same class.
"""

import statistics
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from datetime import datetime
from functools import partial
from typing import TypedDict

import pandas as pd

from baltic import gdelt


class Alert(TypedDict):
    """One spike: the group, the slot it was detected in, the observed and the usual rolling-hour count."""

    group: str
    slot: str  # slot timestamp, YYYYMMDDHHMMSS
    observed: int
    expected: float
    score: float


class SpikeDetector:
    """Robust z-score of the rolling-hour count against the same hour on previous similar days."""

    def __init__(
        self,
        groups: Iterable[str],
        z: float = 4.0,
        min_count: int = 8,
        history_days: int = 14,
        min_history: int = 5,
    ) -> None:
        """Thresholds: score >= z and count >= min_count; needs min_history past days for that hour."""
        self.groups = list(groups)
        self.z, self.min_count, self.min_history = z, min_count, min_history
        # partial, not lambda: the monitor pickles this object as its state
        self.history: dict[tuple[str, bool, int], deque[int]] = defaultdict(
            partial(deque, maxlen=history_days)
        )
        self.last_hour: dict[str, deque[int]] = defaultdict(partial(deque, maxlen=4))
        self.active: set[str] = set()

    def update(self, slot: datetime, counts: Mapping[str, int]) -> list[Alert]:
        """Feed one closed 15-minute slot (in time order); returns the alerts it raises."""
        alerts: list[Alert] = []
        for g in self.groups:
            self.last_hour[g].append(counts.get(g, 0))
            observed = sum(self.last_hour[g])
            past = self.history[(g, slot.weekday() >= 5, slot.hour)]
            if len(past) >= self.min_history:
                usual = statistics.median(past)
                mad = statistics.median(abs(x - usual) for x in past)
                score = (observed - usual) / (1.4826 * mad + 1.0)  # +1: sparse groups have MAD = 0
                high = score >= self.z and observed >= self.min_count
                if high and g not in self.active:
                    alerts.append(
                        Alert(
                            group=g,
                            slot=gdelt.to_ts(slot),
                            observed=observed,
                            expected=usual,
                            score=round(score, 2),
                        )
                    )
                (self.active.add if high else self.active.discard)(g)
            if slot.minute == 45:  # learn one value per hour, after scoring it
                past.append(observed)
        return alerts


def densify(series: pd.DataFrame, groups: Iterable[str], first: str, last: str) -> pd.DataFrame:
    """Rows (ts, group, count) -> one row per slot from first to last, one column per group, gaps = 0."""
    table = series.pivot_table(index="ts", columns="group", values="count", aggfunc="sum")
    table = table.reindex(index=gdelt.slots(first, last), columns=list(groups))
    return table.fillna(0).astype(int)


def backtest(table: pd.DataFrame, detector: SpikeDetector) -> list[Alert]:
    """Run a detector over a densified table, slot by slot; returns every alert."""
    alerts: list[Alert] = []
    for ts, row in table.iterrows():
        alerts += detector.update(gdelt.to_time(str(ts)), {str(g): int(n) for g, n in row.items()})
    return alerts
