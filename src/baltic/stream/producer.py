"""Ingestion, the speed layer's first step: GDELT -> Kafka, then archive the raw zip.

Each poll walks the slots from `since` to GDELT's newest, OLDEST FIRST and both feeds per slot, so Kafka
receives files in time order even while catching up after downtime. The archive on disk is the only state:
a file is published while neither its zip nor its .missing marker exists, and archived only after Kafka
confirmed delivery. A crash therefore re-publishes one file (at-least-once) and never skips one.
"""

import json
import logging
import time
from collections.abc import Iterable
from typing import Any, Protocol

import requests
from confluent_kafka import KafkaError, Message, Producer

from baltic import article, gdelt
from baltic.layout import Layout, write_atomic

log = logging.getLogger(__name__)
# a 404 younger than a day means "not there yet" (late upload, CDN, throttling): wait, never skip.
# Skipping too early loses data silently; waiting shows up as lag in the monitor.
GRACE_SLOTS = 96
BACKLOG_SLOTS = 8  # without --since, look 2 hours back


class Sink(Protocol):
    """Where a parsed file goes; KafkaSink in production, a list in the tests."""

    def publish(self, feed: str, ts: str, articles: Iterable[article.Article]) -> int:
        """Send every article of one file, then its 'done' marker; return the distinct article count."""
        ...


class KafkaSink:
    """Publishes one GDELT file as article messages plus a closing 'done' marker, keyed by slot."""

    def __init__(self, bootstrap: str, topic: str) -> None:
        """Idempotent producer that waits for the broker's acknowledgement of every message."""
        self.topic, self.failed = topic, 0
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "acks": "all",
                "enable.idempotence": True,
                "compression.type": "zstd",
                "linger.ms": 50,
            }
        )

    def publish(self, feed: str, ts: str, articles: Iterable[article.Article]) -> int:
        """Send a file and block until Kafka confirmed all of it; raise if anything was lost."""
        self.failed = 0  # this file's failures only: one bad send must not block every later file
        ids = set()
        for a in articles:
            self._send({"kind": "article", "slot": ts, **a})
            ids.add(a["id"])
        done = {"kind": "done", "slot": ts, "feed": feed, "ids": len(ids), "sent_at": time.time()}
        self._send(done)
        if self.producer.flush(120) > 0 or self.failed:
            raise RuntimeError(f"Kafka did not confirm {feed}/{ts} ({self.failed} failed)")
        return len(ids)

    def _send(self, msg: dict[str, Any]) -> None:
        while True:
            try:
                self.producer.produce(
                    self.topic, json.dumps(msg).encode(), msg["slot"], on_delivery=self._ack
                )
                break
            except BufferError:  # local queue full: let it drain
                self.producer.poll(0.5)
        self.producer.poll(0)

    def _ack(self, err: KafkaError | None, _msg: Message) -> None:
        self.failed += err is not None


class Ingestor:
    """Publishes, in time order, every slot from `since` to GDELT's newest that is not on disk yet."""

    def __init__(
        self, layout: Layout, sink: Sink, http: requests.Session, since: str | None = None
    ) -> None:
        """since: first slot to publish (YYYYMMDDHHMMSS); default: the last BACKLOG_SLOTS slots."""
        self.layout, self.sink, self.http, self.since = layout, sink, http, since

    def poll(self) -> int:
        """One pass; returns the number of files published. Stops early to keep time order."""
        newest = {feed: gdelt.latest(feed, self.http) for feed in gdelt.FEEDS}
        last = max(newest.values())
        published = 0
        for ts in gdelt.slots(self.since or gdelt.shift(last, 1 - BACKLOG_SLOTS), last):
            for feed in gdelt.FEEDS:
                if ts > newest[feed] or self.layout.has(feed, ts):
                    continue
                data = gdelt.download(feed, ts, self.http)
                if data is None and ts > gdelt.shift(newest[feed], -GRACE_SLOTS):
                    log.warning(
                        json.dumps({"event": "waiting", "feed": feed, "ts": ts, "status": 404})
                    )
                    return published  # retry next poll; never skip ahead (time order)
                self.sink.publish(feed, ts, _articles(data, feed))
                if data is None:  # GDELT skipped this file: tell consumers, remember it
                    write_atomic(self.layout.missing(feed, ts), b"")
                else:
                    write_atomic(self.layout.raw(feed, ts), data)
                log.info(json.dumps({"event": "published", "feed": feed, "ts": ts}))
                published += 1
        return published

    def run(self, every_s: float = 60) -> None:
        """Poll forever; a failed poll is logged and retried, a long-running service must not die."""
        while True:
            try:
                self.poll()
            except Exception:  # network, GDELT or Kafka hiccup: the next poll retries
                log.exception("poll failed")
            time.sleep(every_s)


def _articles(data: bytes | None, feed: str) -> list[article.Article]:
    rows = gdelt.read_rows(data) if data else []
    return [a for row in rows if (a := article.parse(row, feed))]
