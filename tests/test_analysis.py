import json

import pandas as pd
import pytest
from fakes import slots_jsonl, write_lake

from baltic import gdelt
from baltic.__main__ import main
from baltic.analysis import checks, hypotheses, report
from baltic.stats import Estimate
from baltic.stream.monitor import read_slots


def test_h1_detects_the_larger_share(month):
    _, domain_day, _ = month
    h1 = hypotheses.h1(domain_day)
    assert h1["accepted"] and h1["share"]["ru_by"] > h1["share"]["other"]
    assert h1["ru_by minus"]["other"]["low"] > 0 and h1["domains"]["ru_by"] == 12


def test_h1_is_rejected_when_shares_are_equal(month):
    _, domain_day, _ = month
    same = domain_day.assign(n_about=50)
    assert not hypotheses.h1(same)["accepted"]


def test_h2_negative_off_security_and_same_on_security(month):
    articles, _, _ = month
    h2 = hypotheses.h2(articles)
    assert h2["H2a accepted"]
    assert h2["H2a non-security, ru_by minus"]["baltic_rus"]["high"] < 0
    assert h2["H2b verdict"] in {"same within the margin", "no detectable difference"}


NON_SEC_OK, NON_SEC_MIXED = [(-1.5, -0.5), (-0.6, -0.1)], [(-1.5, -0.5), (-0.6, 0.2)]


@pytest.mark.parametrize(
    ("cis", "expected"),
    [  # (non-security CIs, security CIs) of the two reference groups -> (H2a, H2b)
        ((NON_SEC_OK, [(-0.4, 0.4), (-0.1, 0.1)]), (True, "same within the margin")),
        ((NON_SEC_MIXED, [(-0.6, 0.4), (-0.1, 0.1)]), (False, "no detectable difference")),
        ((NON_SEC_OK, [(0.1, 0.9), (-0.1, 0.1)]), (True, "different")),
    ],
)
def test_h2_decision_rules(month, monkeypatch, cis, expected):
    monkeypatch.setattr(
        hypotheses,
        "tone_differences",
        lambda _a, sec: {f"ref{i}": Estimate(0, lo, hi) for i, (lo, hi) in enumerate(cis[sec])},
    )
    h2 = hypotheses.h2(month[0])
    assert (h2["H2a accepted"], h2["H2b verdict"]) == expected


def test_h2b_reports_a_real_security_difference(month):
    articles, _, _ = month
    shifted = articles.assign(
        tone=articles.tone.where(~((articles.group == "ru_by") & articles.security), -6.0)
    )
    assert hypotheses.h2(shifted)["H2b verdict"] == "different"


def test_reconcile_passes_on_equal_counts_and_fails_on_a_lost_file(layout, month):
    write_lake(layout.lake.root, *month)
    ref = reference_of(month)
    assert checks.reconcile(layout.lake, ref)["ok"] == 10
    ref[min(ref)]["rows"] += 1
    assert checks.reconcile(layout.lake, ref)["ok"] == 9


def reference_of(month):
    """The per-day counts an independent collector would report for the month."""
    articles, domain_day, _ = month
    rows = domain_day.groupby(pd.to_datetime(domain_day.dt).dt.strftime("%Y%m%d")).n_rows.sum()
    rel = articles.groupby(articles.ts.str[:8]).size()
    return {d: {"rows": int(rows[d]), "relevant": int(rel[d])} for d in rows.index}


def test_the_checks_exit_zero_only_when_they_pass(layout, month, monkeypatch):
    write_lake(layout.lake.root, *month)
    monkeypatch.setattr(checks, "reference", lambda: reference_of(month))
    assert main(["--data", str(layout.root), "reconcile"]) == 0
    monkeypatch.setattr(checks, "reference", dict)
    assert main(["--data", str(layout.root), "reconcile"]) == 1  # nothing compared is no pass
    ts = stream_and_lake(layout, month)
    assert main(["--data", str(layout.root), "stream-vs-batch", "--horizon", ts[-1]]) == 0


def test_the_vendored_reference_covers_september():
    ref = checks.reference()
    assert (
        len(ref) == 30 and min(ref) == "20260901" and all(v["rows"] > 150_000 for v in ref.values())
    )


def stream_and_lake(layout, month, drop=None, change=None):
    articles, domain_day, series = month
    write_lake(layout.live_lake.root, articles, domain_day, series)
    per_slot = series.pivot_table(
        index="ts", columns="group", values="count", aggfunc="sum"
    ).fillna(0)
    ts_all = gdelt.slots(per_slot.index.min(), per_slot.index.max())
    records = [
        {
            "slot": t,
            "security": {g: int(n) for g, n in per_slot.loc[t].items() if n}
            if t in per_slot.index
            else {},
        }
        for t in ts_all
    ]
    if change:
        records[change]["security"] = {"ru_by": 99}
    records = [r for i, r in enumerate(records) if i != drop]
    slots_jsonl(layout.slots, records + records[:3])  # three re-read duplicates
    return ts_all


def test_stream_equals_batch(layout, month):
    ts = stream_and_lake(layout, month)
    r = checks.stream_vs_batch(read_slots(layout.slots), layout.live_lake, ts[-1])
    assert (
        r["ok"]
        and r["duplicates"] == 3
        and r["missing"] == 0
        and r["security_stream"] == r["security_batch"]
    )


def test_stream_vs_batch_catches_a_missing_and_a_wrong_slot(layout, month):
    ts = stream_and_lake(layout, month, drop=5, change=9)
    r = checks.stream_vs_batch(read_slots(layout.slots), layout.live_lake, ts[-1])
    assert not r["ok"] and r["missing"] == 1 and r["differ"] == 1


def test_scaling_summary_and_the_spark_vs_pool_rule():
    runs = [("spark", 7, w, 100 / w + r) for w in (1, 2, 4, 8) for r in (0, 1, 2)]
    runs += [("python", 7, 8, t) for t in (20, 21, 22)]
    bench = pd.DataFrame(runs, columns=["engine", "days", "workers", "wall_s"])
    s = report.scaling(bench)
    assert round(s["speedup"][8], 2) == round(101 / 13.5, 2) and s["efficiency"][1] == 1.0
    assert s["efficiency"][8] == pytest.approx(s["speedup"][8] / 8)
    assert s["spark_beats_pool"]  # 13.5 s median < 20 s, the Pool's fastest repeat
    slower = bench.assign(wall_s=bench.wall_s.where(bench.engine == "python", bench.wall_s + 10))
    assert not report.scaling(slower)["spark_beats_pool"]


def test_live_lag_uses_steady_state_slots_only():
    end = pd.Timestamp("2026-10-01 00:15").timestamp()
    slots = pd.DataFrame(
        {
            "slot": ["20261001000000", "20261001001500"],
            "sent_at": [end + 100, end + 1000],
            "closed_at": [end + 7200, end + 1000 + 30],  # the first closed during a catch-up
            "alerts": [0, 1],
            "rows": [3000, 3100],
            "about_baltic": [40, 50],
        }
    )
    reclosed = pd.concat([slots, slots.iloc[[1]]])  # at-least-once: a slot written twice
    live = report.live(reclosed, [{"group": "ru_by", "slot": "20261001001500"}])
    assert live["slots"] == 2 and live["steady_slots"] == 1 and live["lag_p50_s"] == 30
    assert live["alerts"] == {"ru_by": 1}


def test_report_builds_every_part_from_a_lake(layout, month):
    write_lake(layout.lake.root, *month)
    summary = report.build(layout)
    assert {"H1", "H2", "themes", "backtest"} <= summary.keys()
    assert {p.name for p in layout.report.glob("*.png")} == {
        "1_attention.png",
        "2_themes.png",
        "3_tone.png",
        "5_detector.png",
    }
    assert json.loads((layout.report / "summary.json").read_text())["H1"]["accepted"]


def test_cli_runs_report_on_an_empty_tree(layout, capsys):
    assert main(["--data", str(layout.root), "report"]) == 0
    assert "nothing to report yet" in capsys.readouterr().out


def test_cli_has_the_whole_pipeline():
    from baltic.__main__ import parser

    commands = parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    assert list(commands) == [
        "produce",
        "monitor",
        "seed",
        "mirror",
        "etl",
        "measure",
        "bench",
        "match",
        "sample",
        "evaluate",
        "reconcile",
        "stream-vs-batch",
        "report",
    ]


def test_h1_needs_both_control_groups(month):
    _, domain_day, _ = month
    ru = domain_day[domain_day.group == "ru_by"]
    share = ru.n_about.sum() / ru.n_rows.sum()
    as_high = domain_day.n_about.where(domain_day.group != "regional", domain_day.n_rows * share)
    h1 = hypotheses.h1(domain_day.assign(n_about=as_high.round().astype(int)))
    assert h1["ru_by minus"]["other"]["low"] > 0 and not h1["accepted"]


def test_reconcile_tolerates_half_a_percent_of_relevant_articles_and_no_more(layout, month):
    write_lake(layout.lake.root, *month)
    day = min(reference_of(month))
    exact = reference_of(month)[day]["relevant"]
    for relevant, ok in ((round(exact * 1.004), 10), (round(exact * 1.02), 9)):
        ref = reference_of(month)
        ref[day]["relevant"] = relevant
        assert checks.reconcile(layout.lake, ref)["ok"] == ok


def test_reconcile_fails_on_a_day_the_lake_lacks(layout, month):
    write_lake(layout.lake.root, *month)
    ref = {"20260801": {"rows": 1, "relevant": 1}}
    assert checks.reconcile(layout.lake, ref) == {
        "days": {
            "20260801": {"rows": 0, "ref_rows": 1, "relevant_diff": 1.0, "ok": False},
        },
        "ok": 0,
        "of": 1,
    }


def test_stream_vs_batch_fails_on_a_stalled_or_empty_monitor(layout, month):
    ts = stream_and_lake(layout, month)
    slots = read_slots(layout.slots)
    stalled = slots[slots.slot <= ts[10]]
    r = checks.stream_vs_batch(stalled, layout.live_lake, ts[-1])
    assert not r["ok"] and r["missing"] == len(ts) - 11
    assert not checks.stream_vs_batch(slots[:0], layout.live_lake, ts[-1])["ok"]
    later = checks.stream_vs_batch(stalled, layout.live_lake, ts[-1], start=ts[5])
    assert later["slots"] == 6 and later["missing"] == len(ts) - 11


def test_stream_vs_batch_fails_on_a_missing_slot_alone(layout, month):
    ts = stream_and_lake(layout, month, drop=5)
    r = checks.stream_vs_batch(read_slots(layout.slots), layout.live_lake, ts[-1])
    assert not r["ok"] and r["missing"] == 1 and r["differ"] == 0
