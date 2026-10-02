from datetime import timedelta

import pandas as pd
from fakes import write_lake

from baltic import gdelt
from baltic.stream.monitor import State
from baltic.stream.seed import history, seed


def flat_month(layout, days=10):
    """A lake with `days` days of 2 security articles per slot for ru_by, nothing for the others."""
    ts = gdelt.day_slots("2026-09-21", days)
    series = pd.DataFrame({"ts": ts, "group": "ru_by", "count": 2})
    dd = pd.DataFrame(
        {
            "dt": pd.date_range("2026-09-21", periods=days).date,
            "group": "ru_by",
            "domain": "a.ru",
            "n_rows": 1,
            "n_about": 1,
        }
    )
    arts = pd.DataFrame(
        {
            "id": ["x"],
            "ts": [ts[0]],
            "group": ["ru_by"],
            "about_baltic": [True],
            "themes": [["MILITARY"]],
        }
    )
    write_lake(layout.lake.root, arts, dd, series)
    return ts


def test_history_is_dense_from_the_first_to_the_last_slot_of_the_window(layout):
    ts = flat_month(layout)
    table = history(layout.lake)
    assert (
        table.index.tolist() == ts and (table["ru_by"] == 2).all() and (table["baltic"] == 0).all()
    )


def test_seed_warms_the_detector_so_it_alerts_on_day_one(layout):
    ts = flat_month(layout)
    assert seed(layout)
    state = State.load(layout.state)
    assert state.last_closed == ts[-1] == "20260930234500" and len(state.last_closed) == 14
    spike = gdelt.to_time("20261001100000")
    alerts = []
    for i in range(4):
        alerts += state.detector.update(spike + i * timedelta(minutes=15), {"ru_by": 30})
    assert alerts and alerts[0]["group"] == "ru_by"


def test_seed_never_overwrites_a_live_state(layout):
    flat_month(layout)
    live = layout.state
    State.load(live).save(live)  # a cold state the monitor is already running on
    assert not seed(layout)
    assert State.load(layout.state).last_closed == ""
