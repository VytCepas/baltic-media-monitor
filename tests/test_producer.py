import json

import pytest
from fakes import Http, ListSink, rows, with_ts, zip_of

from baltic import gdelt
from baltic.stream import producer
from baltic.stream.producer import Ingestor

SINCE = "20261001000000"


def gdelt_with(newest, skip=(), status=None):
    """GDELT serving every slot from SINCE to newest for both feeds, except `skip` (404) / `status`."""
    files = {}
    for ts in gdelt.slots(SINCE, newest):
        for feed in gdelt.FEEDS:
            if (feed, ts) not in skip:
                files[(feed, ts)] = zip_of(with_ts(rows("sample.gkg.csv")[:3], ts))
    files.update(status or {})
    return Http({"en": newest, "tr": newest}, files)


def test_catch_up_publishes_slot_by_slot_both_feeds_each(layout):
    """The N1 bug: a feed-outer loop sent the whole en backlog before any tr file."""
    newest = gdelt.shift(SINCE, 3)
    sink = ListSink()
    assert Ingestor(layout, sink, gdelt_with(newest), SINCE).poll() == 8
    order = [(ts, feed) for feed, ts, _ in sink.published]
    assert order == [(ts, f) for ts in gdelt.slots(SINCE, newest) for f in gdelt.FEEDS]
    assert all(n == 3 for *_, n in sink.published)


def test_archived_files_are_not_published_again(layout):
    newest = gdelt.shift(SINCE, 1)
    http = gdelt_with(newest)
    Ingestor(layout, ListSink(), http, SINCE).poll()
    again = ListSink()
    assert Ingestor(layout, again, http, SINCE).poll() == 0 and again.published == []
    assert layout.raw("tr", newest).exists()


def test_a_file_is_archived_only_after_the_sink_confirmed_it(layout):
    newest = gdelt.shift(SINCE, 2)
    failing = ListSink(fail_on=("tr", gdelt.shift(SINCE, 1)))
    with pytest.raises(RuntimeError):
        Ingestor(layout, failing, gdelt_with(newest), SINCE).poll()
    assert layout.raw("en", gdelt.shift(SINCE, 1)).exists()
    # not archived, so the next poll re-publishes it: at-least-once
    assert not layout.raw("tr", gdelt.shift(SINCE, 1)).exists()
    assert not layout.raw("en", newest).exists()  # nothing after the failure


def test_a_404_younger_than_a_day_waits_instead_of_skipping(layout):
    newest = gdelt.shift(SINCE, 8)
    sink = ListSink()
    assert Ingestor(layout, sink, gdelt_with(newest, skip={("tr", SINCE)}), SINCE).poll() == 1
    assert sink.published == [("en", SINCE, 3)] and not layout.has("tr", SINCE)


def test_an_old_404_is_published_as_missing_and_remembered(layout):
    newest = gdelt.shift(SINCE, 97)
    sink = ListSink()
    Ingestor(layout, sink, gdelt_with(newest, skip={("tr", SINCE)}), SINCE).poll()
    assert ("tr", SINCE, 0) in sink.published
    assert layout.missing("tr", SINCE).exists() and not layout.raw("tr", SINCE).exists()


def test_a_fresh_404_stops_the_pass_to_keep_time_order(layout):
    newest = gdelt.shift(SINCE, 3)
    recent = gdelt.shift(newest, -1)
    sink = ListSink()
    n = Ingestor(layout, sink, gdelt_with(newest, skip={("en", recent)}), SINCE).poll()
    assert n == 4 and max(ts for _, ts, _ in sink.published) < recent
    assert not layout.has("en", recent)


def test_a_server_error_is_not_mistaken_for_a_skipped_file(layout):
    newest = gdelt.shift(SINCE, 5)
    with pytest.raises(RuntimeError):
        Ingestor(layout, ListSink(), gdelt_with(newest, status={("en", SINCE): 503}), SINCE).poll()
    assert not layout.has("en", SINCE)


def test_without_since_only_the_last_two_hours_are_fetched(layout):
    newest = gdelt.shift(SINCE, 20)
    sink = ListSink()
    Ingestor(layout, sink, gdelt_with(newest)).poll()
    assert {ts for _, ts, _ in sink.published} == set(gdelt.slots(gdelt.shift(newest, -7), newest))


def test_a_lagging_feed_does_not_block_the_other(layout):
    en_newest, tr_newest = gdelt.shift(SINCE, 2), SINCE
    http = gdelt_with(en_newest)
    http.newest["tr"] = tr_newest
    sink = ListSink()
    Ingestor(layout, sink, http, SINCE).poll()
    assert [(f, ts) for f, ts, _ in sink.published if f == "tr"] == [("tr", SINCE)]
    assert len([1 for f, _, _ in sink.published if f == "en"]) == 3


class FakeProducer:
    """confluent_kafka.Producer's surface used by KafkaSink; deliveries of slots in `fail` fail."""

    def __init__(self, _conf):
        self.fail, self.sent, self.pending = set(), [], []

    def produce(self, _topic, value, key, on_delivery):
        self.sent.append(json.loads(value))
        self.pending.append((on_delivery, key))

    def poll(self, _timeout):
        pass

    def flush(self, _timeout):
        for on_delivery, key in self.pending:
            on_delivery("broker error" if key in self.fail else None, None)
        self.pending = []
        return 0


def test_kafka_sink_sends_articles_then_done_and_one_failure_fails_only_that_file(monkeypatch):
    monkeypatch.setattr(producer, "Producer", FakeProducer)
    sink = producer.KafkaSink("kafka:9092", "gkg.raw")
    sink.producer.fail = {"20261001000000"}
    with pytest.raises(RuntimeError, match="2 failed"):
        sink.publish("en", "20261001000000", [{"id": "a"}])
    sink.producer.fail = set()
    assert sink.publish("tr", "20261001001500", [{"id": "b"}, {"id": "c"}]) == 2
    kinds = [(m["kind"], m["slot"]) for m in sink.producer.sent[2:]]
    assert kinds == [("article", "20261001001500")] * 2 + [("done", "20261001001500")]
