import json
import shutil
import subprocess

import pytest
from fakes import Http, put_raw, rows, zip_of

from baltic import article, gdelt
from baltic.batch import baseline, bench, mapper, mirror

TS = "20260930120000"


def raw_month(layout, slots=4):
    """Real Baltic fixture rows archived for a few slots of both feeds."""
    for i in range(slots):
        ts = gdelt.shift(TS, i)
        put_raw(layout, "en", ts, rows("baltic_en.gkg.csv"))
        put_raw(layout, "tr", ts, rows("baltic_tr.gkg.csv"))
    return layout.raw_files("2026-09-30", 1)


def test_mapper_emits_relevant_articles_and_combined_counts():
    data = zip_of(rows("baltic_tr.gkg.csv"))
    out = list(mapper.map_file("raw/feed=tr/dt=20260930/x.zip", data))
    arts = [v for k, v in out if k == "article"]
    counts = dict(v for k, v in out if k == "count")
    parsed = [article.parse(r, "tr") for r in rows("baltic_tr.gkg.csv")]
    assert len(arts) == sum(article.is_relevant(a) for a in parsed)
    assert sum(n for n, _ in counts.values()) == len(parsed)
    assert sum(a for _, a in counts.values()) == sum(a["about_baltic"] for a in parsed)
    assert all(len(row) == len(mapper.COLUMNS) and row[2] == "tr" for row in arts)
    assert mapper.add_counts((1, 2), (3, 4)) == (4, 6)


def test_mirror_downloads_marks_missing_and_resumes(layout):
    day = gdelt.day_slots("2026-09-30")
    files = {(f, ts): zip_of(rows("sample.gkg.csv")[:1]) for ts in day for f in gdelt.FEEDS}
    del files[("tr", day[5])]
    first = mirror.mirror(layout, Http({}, files), "2026-09-30", 1, until="20261001000000")
    assert first == {"downloaded": 191, "missing": 1, "already there": 0}
    assert layout.missing("tr", day[5]).exists()
    assert mirror.mirror(layout, Http({}, files), "2026-09-30", 1, "20261001000000") == {
        "already there": 192
    }
    with pytest.raises(ValueError, match="before 20260930234500"):
        mirror.mirror(layout, Http({}, files), "2026-09-30", 1, until=day[-1])


def test_baseline_is_the_same_whatever_the_number_of_processes(layout):
    files = raw_month(layout)
    one, two = baseline.run(files, 1), baseline.run(files, 2)
    assert {k: one[k] for k in ("files", "rows", "relevant")} == {
        k: two[k] for k in ("files", "rows", "relevant")
    }
    assert one["files"] == 8 and one["relevant"] == 4 * (
        len(rows("baltic_en.gkg.csv")) + len(rows("baltic_tr.gkg.csv")) - 16
    )


def test_bench_configurations_are_distinct_and_cover_e1_e2_e3():
    cfgs = bench.configs()
    tags = {c.tag for c in cfgs}
    assert len(cfgs) == len(tags) == 10
    assert {
        "spark-d1-w8",
        "spark-d30-w8",
        "spark-d7-w1",
        "spark-d7-w8",
        "python-d7-w1",
        "python-d30-w8",
    } <= tags


def test_bench_schedule_is_shuffled_reproducible_and_complete():
    cfgs = bench.configs()
    runs = bench.schedule(cfgs, 3)
    assert runs == bench.schedule(cfgs, 3) and len(runs) == 30
    assert runs != [(c, r) for r in (1, 2, 3) for c in cfgs]


def test_bench_skips_finished_and_oversized_runs(layout, monkeypatch):
    calls = []

    def fake_run(command, **_):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='warn\n{"wall_s": 1.0}\n')

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    monkeypatch.setattr(bench.os, "cpu_count", lambda: 2)
    cfgs = [bench.Config("python", 1, 1), bench.Config("python", 1, 4)]
    layout.bench("python-d1-w1", 1).parent.mkdir(parents=True)
    layout.bench("python-d1-w1", 1).write_text("{}")
    assert bench.run_all(layout, "2026-09-30", cfgs, repeats=2) == 1
    assert len(calls) == 1 and "--workers" in calls[0]
    assert json.loads(layout.bench("python-d1-w1", 2).read_text())["wall_s"] == 1.0


def has_java():
    """A working JVM; macOS ships a /usr/bin/java stub that only prints an install hint."""
    java = shutil.which("java")
    return (
        bool(java)
        and subprocess.run([java, "-version"], capture_output=True, check=False).returncode == 0
    )


@pytest.mark.skipif(not has_java(), reason="Spark needs a JVM (runs in CI and in the image)")
def test_spark_and_the_baseline_agree_and_the_lake_is_written(layout):
    from baltic.batch import etl

    files = raw_month(layout)
    distinct = baseline.run(files, 1)["relevant"]
    # GDELT re-lists an article in a later file now and then: the same ids again
    again = layout.raw("en", gdelt.shift(TS, len(files)))
    again.write_bytes(layout.raw("en", TS).read_bytes())
    files = layout.raw_files("2026-09-30", 1)
    measured = etl.run(files, 2, None)
    written = etl.run(files, 2, layout.lake)
    plain = baseline.run(files, 1)
    for k in ("files", "rows", "relevant"):
        assert measured[k] == written[k] == plain[k]
    assert plain["relevant"] == distinct  # rows count twice, articles once
    assert layout.lake.done and (layout.lake.silver / "dt=2026-09-30").is_dir()
