import pytest
from fakes import Http, rows, zip_of

from baltic import gdelt


def test_slot_arithmetic():
    assert gdelt.shift("20261001000000", -1) == "20260930234500"
    assert gdelt.slots("20260930233000", "20261001000000") == [
        "20260930233000",
        "20260930234500",
        "20261001000000",
    ]
    day = gdelt.day_slots("2026-10-01")
    assert len(day) == 96 and day[0] == "20261001000000" and day[-1] == "20261001234500"
    assert len(gdelt.day_slots("2026-09-01", 30)) == 30 * 96


def test_file_urls():
    assert gdelt.file_url("en", "20261001000000").endswith("/20261001000000.gkg.csv.zip")
    assert gdelt.file_url("tr", "20261001000000").endswith(
        "/20261001000000.translation.gkg.csv.zip"
    )


def test_latest_reads_the_index():
    http = Http({"en": "20261001101500", "tr": "20261001100000"}, {})
    assert gdelt.latest("en", http) == "20261001101500"
    assert gdelt.latest("tr", http) == "20261001100000"


def test_download_distinguishes_skipped_from_failed():
    http = Http({}, {("en", "a"): b"zip", ("en", "b"): 503})
    assert gdelt.download("en", "a", http) == b"zip"
    assert gdelt.download("en", "c", http) is None  # 404: GDELT skipped the file
    with pytest.raises(RuntimeError):
        gdelt.download("en", "b", http)  # any other failure must not look like "skipped"


def test_read_rows_round_trips_a_zip():
    original = rows("sample.gkg.csv")
    assert list(gdelt.read_rows(zip_of(original))) == original
