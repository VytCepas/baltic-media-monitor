import re
from pathlib import Path

import pytest
from fakes import put_raw, rows

from baltic.layout import write_atomic


def test_raw_paths_follow_the_partitioned_schema(layout):
    assert layout.raw("tr", "20261001001500").relative_to(layout.root).as_posix() == (
        "raw/feed=tr/dt=20261001/20261001001500.zip"
    )
    assert layout.missing("en", "20261001001500").name == "20261001001500.missing"
    assert layout.bench("spark-d7-w4", 2).name == "spark-d7-w4-r2.json"


def test_has_is_archived_or_known_missing(layout):
    assert not layout.has("en", "20261001000000")
    write_atomic(layout.missing("en", "20261001000000"), b"")
    assert layout.has("en", "20261001000000")


def test_raw_files_lists_existing_zips_in_time_order(layout):
    for feed, ts in [("tr", "20261001001500"), ("en", "20261001000000"), ("en", "20261002000000")]:
        put_raw(layout, feed, ts, rows("sample.gkg.csv")[:1])
    files = layout.raw_files("2026-10-01", 1)
    assert [p.stem for p in files] == ["20261001000000", "20261001001500"]


def test_write_atomic_leaves_no_temporary_file(layout):
    p = layout.root / "a" / "b.bin"
    write_atomic(p, b"x")
    write_atomic(p, b"y")
    assert p.read_bytes() == b"y" and [q.name for q in p.parent.iterdir()] == ["b.bin"]


def test_lake_is_done_only_after_the_last_table_committed(layout):
    lake = layout.lake
    assert not lake.done
    lake.gold("series_15m").mkdir(parents=True)
    (lake.gold("series_15m") / "_SUCCESS").touch()
    assert lake.done


COMPOSE = Path(__file__).parents[1] / "docker-compose.yml"


@pytest.mark.skipif(not COMPOSE.exists(), reason="the image holds no docker-compose.yml")
def test_kafka_writes_its_log_into_its_volume():
    compose = COMPOSE.read_text()
    (log_dir,) = re.findall(r"KAFKA_LOG_DIRS: (\S+)", compose)
    assert f'"kafka-data:{log_dir}"' in compose  # else a re-created container loses the topic
