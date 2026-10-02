"""Warm start: the batch layer hands its history to the speed layer.

The detector needs >= 5 previous similar days per hour before it may alert, so a cold monitor started on
Oct 1 could not alert before the presentation. seed() replays Spark's 30-day gold/series_15m through a
fresh detector and saves it as the monitor's state, with the last batch slot as "already closed".
"""

import pandas as pd

from baltic import article, gdelt
from baltic.detector import SpikeDetector, backtest, densify
from baltic.layout import Lake, Layout
from baltic.stream.monitor import State


def history(lake: Lake) -> pd.DataFrame:
    """The lake's security series as a dense slot x group table over the whole backfilled window."""
    days = pd.read_parquet(lake.gold("domain_day"), columns=["dt"]).dt
    first = gdelt.to_ts(pd.Timestamp(days.min()).to_pydatetime())
    last = gdelt.shift(gdelt.to_ts(pd.Timestamp(days.max()).to_pydatetime()), 95)  # 23:45 that day
    return densify(pd.read_parquet(lake.gold("series_15m")), article.GROUPS, first, last)


def seed(layout: Layout) -> bool:
    """Write the monitor's warm state; never overwrites a live state. Returns True when it wrote one."""
    path = layout.state
    if path.exists():
        return False
    table = history(layout.lake)
    detector = SpikeDetector(article.GROUPS)
    backtest(table, detector)
    State(detector, last_closed=str(table.index[-1])).save(path)
    return True
