"""A test that cannot fail proves nothing: break each guarded behaviour, run its test, expect RED, restore.

usage: just red-check   (exits 1 if any mutation stays green)
"""

import subprocess
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parents[1]
MUTATIONS = [
    (
        "N1 slot-outer loop",
        "src/baltic/stream/producer.py",
        "        for ts in gdelt.slots(self.since or gdelt.shift(last, 1 - BACKLOG_SLOTS), last):\n            for feed in gdelt.FEEDS:",
        "        for feed in gdelt.FEEDS:\n            for ts in gdelt.slots(self.since or gdelt.shift(last, 1 - BACKLOG_SLOTS), last):",
        "tests/test_producer.py::test_catch_up_publishes_slot_by_slot_both_feeds_each",
    ),
    (
        "archive after ack",
        "src/baltic/stream/producer.py",
        "                self.sink.publish(feed, ts, _articles(data, feed))\n                if data is None:",
        "                if data is not None:\n                    write_atomic(self.layout.raw(feed, ts), data)\n                self.sink.publish(feed, ts, _articles(data, feed))\n                if data is None:",
        "tests/test_producer.py::test_a_file_is_archived_only_after_the_sink_confirmed_it",
    ),
    (
        "seed warms the detector",
        "src/baltic/stream/seed.py",
        "    backtest(table, detector)\n",
        "",
        "tests/test_seed.py::test_seed_warms_the_detector_so_it_alerts_on_day_one",
    ),
    (
        "seed keeps a live state",
        "src/baltic/stream/seed.py",
        "    if path.exists():\n        return False\n",
        "",
        "tests/test_seed.py::test_seed_never_overwrites_a_live_state",
    ),
    (
        "closed slots stay closed",
        "src/baltic/stream/monitor.py",
        "        if ts <= self.last_closed:",
        "        if False:",
        "tests/test_monitor.py::test_duplicates_count_once_and_late_messages_of_closed_slots_are_dropped",
    ),
    (
        "close in time order",
        "src/baltic/stream/monitor.py",
        "            oldest = min(self.open)",
        "            oldest = max(self.open)",
        "tests/test_monitor.py::test_slots_close_in_time_order",
    ),
    (
        "404 is not a failure",
        "src/baltic/gdelt.py",
        "    if r.status_code == 404:\n        return None\n",
        "",
        "tests/test_gdelt.py::test_download_distinguishes_skipped_from_failed",
    ),
    (
        "reduce sums both counts",
        "src/baltic/batch/mapper.py",
        "    return x[0] + y[0], x[1] + y[1]",
        "    return x[0] + y[0], x[1]",
        "tests/test_batch.py::test_mapper_emits_relevant_articles_and_combined_counts",
    ),
    (
        "H2b equivalence margin",
        "src/baltic/stats.py",
        "        return -margin < self.low and self.high < margin",
        "        return self.low < 0 < self.high",
        "tests/test_stats.py::test_decision_rules",
    ),
    (
        "H3 equal coverage",
        "src/baltic/ai/evaluate.py",
        '"emb_top": titles(emb.head(len(ent)), "emb")',
        '"emb_top": titles(emb.head(30), "emb")',
        "tests/test_ai.py::test_strata_compare_at_equal_coverage",
    ),
    (
        "one Kafka failure fails one file",
        "src/baltic/stream/producer.py",
        "        self.failed = 0  # this file's failures only",
        "        pass  # this file's failures only",
        "tests/test_producer.py::test_kafka_sink_sends_articles_then_done_and_one_failure_fails_only_that_file",
    ),
    (
        "H1 needs both control groups",
        "src/baltic/analysis/hypotheses.py",
        '"accepted": all(e.above(0) for e in diffs.values()),',
        '"accepted": any(e.above(0) for e in diffs.values()),',
        "tests/test_analysis.py::test_h1_needs_both_control_groups",
    ),
    (
        "reconcile tolerance",
        "src/baltic/analysis/checks.py",
        "rel_diff <= 0.005",
        "rel_diff <= 0.5",
        "tests/test_analysis.py::test_reconcile_tolerates_half_a_percent_of_relevant_articles_and_no_more",
    ),
    (
        "a missing slot fails stream = batch",
        "src/baltic/analysis/checks.py",
        '"ok": not differ.any() and set(expected) <= set(unique.index),',
        '"ok": not differ.any(),',
        "tests/test_analysis.py::test_stream_vs_batch_fails_on_a_missing_slot_alone",
    ),
    (
        "detector scale",
        "src/baltic/detector.py",
        "(1.4826 * mad + 1.0)",
        "(1.4826 * mad + 0.5)",
        "tests/test_detector.py::test_the_score_divides_by_scaled_mad_plus_one",
    ),
    (
        "unclear labels are left out",
        "src/baltic/ai/evaluate.py",
        ".str.lower().map(answer)",
        ".str.lower().str[:1]",
        "tests/test_ai.py::test_score_and_h3",
    ),
    (
        "wait for every announced article",
        "src/baltic/stream/monitor.py",
        "len(self.ids.get(feed, ())) >= n",
        "n >= 0",
        "tests/test_monitor.py::test_a_slot_waits_for_every_announced_article",
    ),
    (
        "a recent 404 waits",
        "src/baltic/stream/producer.py",
        "GRACE_SLOTS = 96",
        "GRACE_SLOTS = 2",
        "tests/test_producer.py::test_a_404_younger_than_a_day_waits_instead_of_skipping",
    ),
]


def pytest(test: str) -> int:
    """Exit code of one test: 0 passed, 1 failed (anything else: the test could not even run)."""
    cmd = ["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", test]
    return subprocess.run(cmd, cwd=R, capture_output=True, text=True, check=False).returncode


bad = 0
for name, path, old, new, test in MUTATIONS:
    f = R / path
    original = f.read_text()
    assert old in original, name
    assert pytest(test) == 0, f"{name}: the test must pass before the mutation"
    f.write_text(original.replace(old, new, 1))
    try:
        code = pytest(test)
    finally:
        time.sleep(
            1
        )  # a same-size restore within one second would keep the mutant's cached bytecode
        f.write_text(original)
    bad += code != 1
    print(f"{'RED  ' if code == 1 else 'GREEN'} {name}  ({test.split('::')[1]})")
sys.exit(1 if bad else 0)
