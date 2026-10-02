import json

import pytest
from fakes import art, done

from baltic import gdelt
from baltic.stream import monitor
from baltic.stream.monitor import Monitor, SlotAssembler, State, read_slots

A, B, C = "20261001000000", "20261001001500", "20261001003000"


def test_a_slot_closes_only_when_both_feeds_are_done():
    s = SlotAssembler()
    assert s.add(art(A, "1")) == [] and s.add(done(A, "en")) == []
    (closed,) = s.add(done(A, "tr"))
    assert closed.ts == A and closed.security["ru_by"] == 1 and s.idle


def test_slots_close_in_time_order():
    s = SlotAssembler()
    for m in (done(A, "en"), done(B, "en"), done(B, "tr")):
        assert s.add(m) == []  # B is complete but A is not
    assert [x.ts for x in s.add(done(A, "tr"))] == [A, B]


def test_duplicates_count_once_and_late_messages_of_closed_slots_are_dropped():
    s = SlotAssembler()
    s.add(art(A, "1"))
    s.add(art(A, "1"))  # re-delivered (at-least-once)
    s.add(done(A, "en"))
    (closed,) = s.add(done(A, "tr"))
    assert closed.security["ru_by"] == 1 and closed.rows == 1
    assert s.add(art(A, "2")) == [] and s.idle  # a re-read after a restart


def test_a_slot_waits_for_every_announced_article():
    s = SlotAssembler()
    s.add(art(A, "1"))
    s.add(done(A, "en", ids=1))
    assert s.add(done(A, "tr", ids=2)) == []  # the tr articles were lost; the marker got through
    assert s.add(art(A, "t1", feed="tr")) == []
    (closed,) = s.add(art(A, "t2", feed="tr"))  # the producer's re-send completes it
    assert closed.rows == 3 and s.idle


def test_only_security_articles_about_the_baltics_are_counted():
    s = SlotAssembler()
    for m in (
        art(A, "1"),
        art(A, "2", security=False),
        art(A, "3", about_baltic=False),
        art(A, "4", group="baltic"),
    ):
        s.add(m)
    s.add(done(A, "en"))
    (closed,) = s.add(done(A, "tr"))
    assert closed.about == 3 and closed.security == {"ru_by": 1, "baltic": 1}


def test_a_lost_marker_stalls_at_most_one_day():
    s = SlotAssembler()
    s.add(done(A, "en"))  # tr marker of A never arrives
    day_later = gdelt.shift(A, 96)
    assert s.add(done(day_later, "en")) + s.add(done(day_later, "tr")) == []
    late = gdelt.shift(day_later, 1)
    assert [x.ts for x in s.add(done(late, "en"))] == [A, day_later]


def test_monitor_writes_slots_and_resumes_after_a_restart(layout):
    m = Monitor(layout, clock=lambda: 100.0)
    for msg in (art(A, "1"), done(A, "en", 90.0), done(A, "tr", 95.0), done(B, "en")):
        m.handle(msg)
    assert not m.idle  # B is half-read: the offset must not be committed yet
    restarted = Monitor(layout)
    assert restarted.assembler.last_closed == A
    for msg in (art(A, "1"), done(A, "en"), done(A, "tr"), done(B, "en"), done(B, "tr")):
        restarted.handle(msg)  # Kafka re-delivers from the last committed offset
    slots = read_slots(layout.slots)
    assert slots.slot.tolist() == [A, B]
    first = slots.iloc[0]
    assert first.security == {"ru_by": 1} and first.sent_at == 95.0 and first.closed_at == 100.0


def test_alerts_are_written_as_they_happen(layout):
    m = Monitor(layout)
    m.state.detector.history[("ru_by", False, 0)].extend([0] * 5)  # five quiet Thursdays at 00:00
    for i in range(10):
        m.handle(art(A, str(i)))
    m.handle(done(A, "en"))
    m.handle(done(A, "tr"))
    (alert,) = [json.loads(x) for x in (layout.alerts).read_text().splitlines()]
    assert alert["group"] == "ru_by" and alert["observed"] == 10


def test_cold_state_has_no_history(layout):
    s = State.load(layout.state)
    assert s.last_closed == "" and not s.detector.history


class FakeMessage:
    def __init__(self, msg, error=None):
        self.msg, self._error = msg, error

    def value(self):
        return json.dumps(self.msg).encode() if self.msg else b""

    def error(self):
        return self._error


class FakeConsumer:
    """confluent_kafka.Consumer's surface used by consume(); ends the loop when the messages run out."""

    def __init__(self, messages):
        self.messages, self.commits = list(messages), []

    def subscribe(self, _topics):
        pass

    def poll(self, _timeout):
        if not self.messages:
            raise StopIteration
        return self.messages.pop(0)

    def commit(self, asynchronous):
        self.commits.append((len(self.messages), asynchronous))


def test_the_offset_is_committed_only_when_no_slot_is_half_read(layout, monkeypatch):
    stream = [
        None,  # poll timeout
        FakeMessage(None, error="broker hiccup"),
        FakeMessage(done(B, "en")),
        FakeMessage(done(A, "en")),
        FakeMessage(done(A, "tr")),  # closes A while B is half-read: no commit
        FakeMessage(done(B, "tr")),  # closes B: nothing open, commit
    ]
    consumer = FakeConsumer(stream)
    monkeypatch.setattr(monitor, "Consumer", lambda _conf: consumer)
    with pytest.raises(StopIteration):
        monitor.consume(Monitor(layout), "kafka:9092", "gkg.raw")
    assert consumer.commits == [(0, False)] and read_slots(layout.slots).slot.tolist() == [A, B]
