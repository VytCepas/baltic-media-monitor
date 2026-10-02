"""Stream processing, the speed layer: Kafka -> per-slot counts -> spike alerts.

Messages are grouped into 15-minute slots. A slot closes, strictly in time order, once both feeds' 'done'
markers have arrived AND every article each marker announced has arrived (a lost article must not be
silently skipped: the producer re-sends a file it could not confirm). The detector then scores the slot,
and one line goes to slots.jsonl (plus one per alert).
The topic has ONE partition: Kafka orders messages only within a partition, and the close rule needs the
global time order the producer published in.

Delivery is at-least-once. Offsets are committed by hand, only after the state (detector + last closed
slot) is saved and no slot is half-read. A crash therefore re-reads messages; re-read messages of slots
that were already closed are dropped, and duplicates inside an open slot are dropped by article key (id + url).
"""

import json
import logging
import pickle
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from confluent_kafka import Consumer

from baltic import article, gdelt
from baltic.detector import Alert, SpikeDetector
from baltic.layout import Layout, write_atomic

log = logging.getLogger(__name__)
# force-close a slot once data one day newer has arrived: a lost marker must not stall
STUCK_SLOTS = 96


@dataclass
class Slot:
    """One 15-minute slot being assembled from messages."""

    ts: str
    ids: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # feed -> keys received
    about: int = 0
    security: Counter[str] = field(default_factory=Counter)
    done: dict[str, float] = field(default_factory=dict)  # feed -> producer's send time
    announced: dict[str, int] = field(default_factory=dict)  # feed -> distinct articles sent

    def add(self, msg: dict[str, Any]) -> None:
        """Count an article (once per key) or record a feed's 'done' marker."""
        if msg["kind"] == "done":
            self.done[msg["feed"]] = msg["sent_at"]
            # markers sent before the count existed (still in Kafka's 14 days): announce 0
            self.announced[msg["feed"]] = msg.get("ids", 0)
        elif (k := article.key(msg)) not in (seen := self.ids.setdefault(msg["feed"], set())):
            seen.add(k)
            self.about += msg["about_baltic"]
            self.security[msg["group"]] += msg["about_baltic"] and msg["security"]

    @property
    def rows(self) -> int:
        """Distinct articles received, both feeds."""
        return sum(map(len, self.ids.values()))

    @property
    def complete(self) -> bool:
        """Both feeds are done and every article they announced has arrived."""
        return len(self.done) == len(gdelt.FEEDS) and all(
            len(self.ids.get(feed, ())) >= n for feed, n in self.announced.items()
        )


class SlotAssembler:
    """The close rule, free of Kafka and files: messages in, closed slots out, oldest first."""

    def __init__(self, last_closed: str = "") -> None:
        """last_closed: slots up to and including it are history (from `seed` or a restart)."""
        self.open: dict[str, Slot] = {}
        self.last_closed = last_closed

    def add(self, msg: dict[str, Any]) -> list[Slot]:
        """Feed one message; returns the slots it closed."""
        ts = msg["slot"]
        if ts <= self.last_closed:
            return []  # a re-read message of a slot that is already closed
        self.open.setdefault(ts, Slot(ts)).add(msg)
        return self._close_ready()

    def _close_ready(self) -> list[Slot]:
        closed = []
        while self.open:
            oldest = min(self.open)
            stuck = max(self.open) > gdelt.shift(oldest, STUCK_SLOTS)
            if not (self.open[oldest].complete or stuck):
                break
            closed.append(self.open.pop(oldest))
            self.last_closed = oldest
        return closed

    @property
    def idle(self) -> bool:
        """No slot is half-read, so committing the Kafka offset loses nothing."""
        return not self.open


@dataclass
class State:
    """What survives a restart: the detector's history and the last closed slot."""

    detector: SpikeDetector
    last_closed: str = ""

    @classmethod
    def load(cls, path: Path) -> "State":
        """The saved state, or a cold one (no history: no alerts for the first min_history days)."""
        if path.exists():
            state: State = pickle.loads(path.read_bytes())  # noqa: S301  our own state file
            return state
        return cls(SpikeDetector(article.GROUPS))

    def save(self, path: Path) -> None:
        """Atomically replace the state file."""
        write_atomic(path, pickle.dumps(self))


class Monitor:
    """Turns messages into closed slots, scores them and writes slots.jsonl / alerts.jsonl."""

    def __init__(self, layout: Layout, clock: Callable[[], float] = time.time) -> None:
        """Resume from the saved state, if any."""
        self.layout, self.clock = layout, clock
        self.state = State.load(layout.state)
        self.assembler = SlotAssembler(self.state.last_closed)

    def handle(self, msg: dict[str, Any]) -> list[Slot]:
        """Process one message; returns the slots it closed (already scored and written)."""
        closed = self.assembler.add(msg)
        for slot in closed:
            self._emit(slot, self.state.detector.update(gdelt.to_time(slot.ts), slot.security))
        if closed:
            self.state.last_closed = self.assembler.last_closed
            self.state.save(self.layout.state)
        return closed

    def _emit(self, slot: Slot, alerts: list[Alert]) -> None:
        sent = max(slot.done.values(), default=float("nan"))
        record = {
            "slot": slot.ts,
            "rows": slot.rows,
            "about_baltic": slot.about,
            "security": dict(slot.security),
            "feeds": sorted(slot.done),
            "sent_at": sent,
            "closed_at": self.clock(),
            "alerts": len(alerts),
        }
        _append(self.layout.slots, [record])
        _append(self.layout.alerts, alerts)
        log.info(json.dumps({"event": "slot", **record}))


def _append(path: Path, records: list[Any]) -> None:
    if records:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.writelines(json.dumps(r) + "\n" for r in records)


def consume(monitor: Monitor, bootstrap: str, topic: str, group_id: str = "monitor") -> None:
    """The consume loop: poll, handle, commit when nothing is half-read."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([topic])
    while True:
        m = consumer.poll(1.0)
        if m is None:
            continue
        value = m.value()
        if m.error() or not value:  # the client retries broker errors itself: log and move on
            log.warning("skipped message: %s", m.error() or "empty value")
            continue
        if monitor.handle(json.loads(value)) and monitor.assembler.idle:
            consumer.commit(asynchronous=False)


def read_slots(path: Path) -> pd.DataFrame:
    """slots.jsonl as a frame, slot kept as its 14-character string, times as epoch seconds."""
    return pd.read_json(path, lines=True, convert_dates=False, dtype={"slot": str})
