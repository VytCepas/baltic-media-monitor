import pickle
from datetime import datetime, timedelta

import pandas as pd

from baltic import gdelt
from baltic.detector import SpikeDetector, backtest, densify

T0 = datetime(2026, 9, 1)  # a Tuesday


def feed(d, slots, level, burst=None):
    """Feed `slots` slots from T0; burst = (start, end, count) overrides the flat level."""
    alerts = []
    for i in range(slots):
        t = T0 + timedelta(minutes=15 * i)
        n = burst[2] if burst and burst[0] <= t < burst[1] else level
        alerts += d.update(t, {"g": n})
    return alerts


def test_flat_history_never_alerts_and_a_burst_does():
    d = SpikeDetector(["g"])
    assert feed(d, 8 * 96, 2) == []
    spike = T0 + timedelta(days=8, hours=10)
    d2 = SpikeDetector(["g"])
    alerts = feed(d2, 8 * 96 + 11 * 4, 2, (spike, spike + timedelta(hours=1), 30))
    assert len(alerts) == 1 and alerts[0]["group"] == "g" and alerts[0]["observed"] >= 30


def test_one_event_gives_one_alert():
    d = SpikeDetector(["g"])
    spike = T0 + timedelta(days=8, hours=10)
    assert len(feed(d, 8 * 96 + 14 * 4, 2, (spike, spike + timedelta(minutes=90), 30))) == 1


def test_no_alert_before_enough_history():
    d = SpikeDetector(["g"], min_history=5)
    burst = (T0 + timedelta(days=3, hours=10), T0 + timedelta(days=3, hours=11), 50)
    assert feed(d, 4 * 96, 2, burst) == []


def test_small_counts_never_alert():
    d = SpikeDetector(["g"], min_count=8)
    spike = T0 + timedelta(days=8, hours=10)
    assert feed(d, 8 * 96 + 11 * 4, 0, (spike, spike + timedelta(hours=1), 1)) == []


def test_state_survives_pickling():
    d = SpikeDetector(["g"])
    feed(d, 96, 3)
    clone = pickle.loads(pickle.dumps(d))
    assert list(clone.last_hour["g"]) == [3, 3, 3, 3] and len(clone.history[("g", False, 0)]) == 1
    nxt = T0 + timedelta(days=1)
    assert clone.update(nxt, {"g": 3}) == d.update(nxt, {"g": 3})


def test_densify_fills_every_slot_and_group():
    series = pd.DataFrame(
        {"ts": ["20260901001500", "20260901001500"], "group": ["a", "a"], "count": [2, 3]}
    )
    table = densify(series, ["a", "b"], "20260901000000", "20260901003000")
    assert table.index.tolist() == ["20260901000000", "20260901001500", "20260901003000"]
    assert table.to_dict("list") == {"a": [0, 5, 0], "b": [0, 0, 0]}


def test_backtest_equals_feeding_slot_by_slot():
    ts = gdelt.slots("20260901000000", gdelt.shift("20260901000000", 9 * 96))
    table = pd.DataFrame(
        {"g": [30 if 8 * 96 + 40 <= i < 8 * 96 + 44 else 2 for i in range(len(ts))]}, index=ts
    )
    manual = SpikeDetector(["g"])
    expected = [
        a for t in ts for a in manual.update(gdelt.to_time(t), {"g": int(table.loc[t, "g"])})
    ]
    assert backtest(table, SpikeDetector(["g"])) == expected and len(expected) == 1


def test_the_score_divides_by_scaled_mad_plus_one():
    # flat zeros: MAD = 0, so an hour of 2 per slot (8 in total) scores exactly 8 / (0 + 1) = 8
    spike = T0 + timedelta(days=8, hours=10)
    burst = (spike, spike + timedelta(hours=1), 2)
    assert feed(SpikeDetector(["g"], z=8.01), 8 * 96 + 11 * 4, 0, burst) == []
    assert feed(SpikeDetector(["g"], z=8.0), 8 * 96 + 11 * 4, 0, burst)[0]["score"] == 8.0
