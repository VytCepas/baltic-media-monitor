"""The command line end to end over a synthetic data tree: the wiring from arguments to functions."""

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from fakes import Http, slots_jsonl, write_lake

from baltic import gdelt
from baltic.__main__ import main
from baltic.batch import baseline, etl
from baltic.stream.monitor import State


def run(layout, *argv):
    return main(["--data", str(layout.root), *argv])


def test_seed_sample_evaluate_report(layout, month):
    write_lake(layout.lake.root, *month)
    assert run(layout, "seed") == 0 and State.load(layout.state).last_closed == "20260910234500"
    n = 120
    pairs = pd.DataFrame(
        {
            "src_title": [f"r{i}" for i in range(n)],
            "emb_cos": np.linspace(0.4, 1.0, n),
            "emb_title": [f"e{i}" for i in range(n)],
            "ent_shared": [2] * 40 + [0] * 80,
            "ent_title": [f"n{i}" for i in range(n)],
        }
    )
    layout.pairs.parent.mkdir(parents=True)
    pairs.to_parquet(layout.pairs)
    assert run(layout, "sample") == 0
    sheet = pd.read_csv(layout.labels("pairs"))
    key = pd.read_csv(layout.labels("pairs", key=True))
    # label every embedding pair "same story" and every entity pair not (titles e... / n...)
    method = key.drop_duplicates("item").set_index("item").baltic_title.str[0]
    sheet["label"] = sheet.item.map(method).map({"e": "y", "n": "n"})
    sheet.to_csv(layout.labels("pairs"), index=False)
    assert run(layout, "evaluate") == 0
    result = json.loads((layout.evaluation).read_text())
    assert result["H3"]["shown"] and result["precision"]["ent"]["k"] == 0
    assert run(layout, "report") == 0
    assert "ai" in json.loads((layout.report / "summary.json").read_text())


def test_checks_exit_non_zero_when_they_fail(layout, month):
    write_lake(layout.lake.root, *month)
    assert run(layout, "reconcile") == 1  # synthetic September does not match the real collector
    write_lake(layout.live_lake.root, *month)
    slots_jsonl(layout.slots, [{"slot": "20260901000000", "security": {"ru_by": 99}}])
    assert run(layout, "stream-vs-batch", "--horizon", "20260930000000") == 1
    assert not json.loads((layout.report / "stream_vs_batch.json").read_text())["ok"]


def test_report_includes_the_scaling_study(layout):
    for w, t in ((1, 40.0), (2, 22.0)):
        p = layout.bench(f"spark-d7-w{w}", 1)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"engine": "spark", "days": 7, "workers": w, "wall_s": t}))
    assert run(layout, "report") == 0
    summary = json.loads((layout.report / "summary.json").read_text())
    assert (
        summary["scaling"]["speedup"]["2"] == 40 / 22 and (layout.report / "4_scaling.png").exists()
    )


@pytest.mark.parametrize("since", ["20260915000000", "20991231000000"])
def test_mirror_stops_before_since_and_before_the_last_day(layout, monkeypatch, since):
    monkeypatch.setenv("SINCE", since)
    monkeypatch.setattr(gdelt, "session", lambda: Http({}, {}))
    day = "2026-09-15" if since < "2099" else datetime.now(UTC).date().isoformat()
    with pytest.raises(ValueError, match="live window"):
        run(layout, "mirror", day, "1")


def test_only_etl_live_writes_the_live_lake_and_measure_writes_none(layout, monkeypatch):
    seen = []
    monkeypatch.setattr(etl, "run", lambda _f, _w, lake, *_m: seen.append(lake) or {})
    monkeypatch.setattr(baseline, "run", lambda _f, _w: seen.append("pool") or {})
    one = ["--day", "2026-09-01", "--days", "1", "--workers", "1"]
    for argv in (
        ["etl", "2026-09-01", "1", "--live"],
        ["etl", "2026-09-01", "1"],
        ["measure", "spark", *one],
        ["measure", "python", *one],
    ):
        assert run(layout, *argv) == 0
    assert seen == [layout.live_lake, layout.lake, None, "pool"]
