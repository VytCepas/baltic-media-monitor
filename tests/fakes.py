"""Stand-ins for the network and Kafka, and builders for raw files and lakes."""

import csv
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from baltic import gdelt
from baltic.layout import Layout

FIXTURES = Path(__file__).parent / "fixtures"


def rows(name: str) -> list[list[str]]:
    """Rows of a fixture file (real GKG rows)."""
    with (FIXTURES / name).open(encoding="utf-8") as f:
        return list(csv.reader(f, delimiter="\t"))


def zip_of(rs: list[list[str]]) -> bytes:
    """A GKG-style zip holding one tab-separated CSV."""
    text = io.StringIO()
    csv.writer(text, delimiter="\t", lineterminator="\n").writerows(rs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.gkg.csv", text.getvalue())
    return buf.getvalue()


def with_ts(rs: list[list[str]], ts: str, feed: str = "en") -> list[list[str]]:
    """Copies of rows re-stamped to slot ts, with ids unique to that slot and feed."""
    return [[f"{ts}-{feed}-{i}", ts, *r[2:]] for i, r in enumerate(rs)]


def put_raw(layout: Layout, feed: str, ts: str, rs: list[list[str]]) -> Path:
    """Archive rows as the raw zip of (feed, ts)."""
    p = layout.raw(feed, ts)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(zip_of(with_ts(rs, ts, feed)))
    return p


class Response:
    """The parts of requests.Response the code uses."""

    def __init__(self, status: int = 200, content: bytes = b"", body: Any = None) -> None:
        self.status_code, self.content, self._body = status, content, body
        self.text = content.decode(errors="ignore")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._body


class Http:
    """A requests.Session stand-in serving GDELT: index files, zips, 404s, and recording calls."""

    def __init__(self, newest: dict[str, str], files: dict[tuple[str, str], bytes | int]) -> None:
        """files: (feed, ts) -> zip bytes, or an HTTP status to answer instead."""
        self.newest, self.files, self.calls = newest, files, []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(url)
        for feed, index in gdelt.INDEX.items():
            if url == index:
                ts = self.newest[feed]
                return Response(content=f"1 h {gdelt.file_url(feed, ts)}\n".encode())
        for (feed, ts), f in self.files.items():
            if url == gdelt.file_url(feed, ts):
                return Response(f) if isinstance(f, int) else Response(content=f)
        return Response(404)


class ListSink:
    """A Sink that remembers what was published, optionally failing on one file."""

    def __init__(self, fail_on: tuple[str, str] | None = None) -> None:
        self.published: list[tuple[str, str, int]] = []
        self.fail_on = fail_on

    def publish(self, feed: str, ts: str, articles: Any) -> int:
        if (feed, ts) == self.fail_on:
            raise RuntimeError("broker down")
        n = len(list(articles))
        self.published.append((feed, ts, n))
        return n


def done(ts: str, feed: str, sent_at: float = 0.0, ids: int = 0) -> dict[str, Any]:
    """A 'done' marker message announcing `ids` distinct articles."""
    return {"kind": "done", "slot": ts, "feed": feed, "ids": ids, "sent_at": sent_at}


def art(ts: str, id_: str, **fields: Any) -> dict[str, Any]:
    """An article message with only the fields the monitor reads; `fields` override the defaults."""
    defaults = {"feed": "en", "group": "ru_by", "about_baltic": True, "security": True}
    return {"kind": "article", "slot": ts, "id": id_, **defaults, **fields}


def write_lake(
    root: Path, articles: pd.DataFrame, domain_day: pd.DataFrame, series: pd.DataFrame
) -> None:
    """A lake with the batch layer's file layout, written by pandas instead of Spark."""
    (root / "silver" / "articles").mkdir(parents=True)
    articles.to_parquet(root / "silver" / "articles" / "part.parquet")
    themes = articles[articles.about_baltic].explode("themes").groupby(["group", "themes"]).size()
    tables = {
        "domain_day": domain_day,
        "theme_group": themes.rename("count").reset_index().rename(columns={"themes": "theme"}),
        "series_15m": series,
    }
    for name, df in tables.items():
        (root / "gold" / name).mkdir(parents=True)
        df.to_parquet(root / "gold" / name / "part.parquet")
        (root / "gold" / name / "_SUCCESS").touch()


def slots_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """Write monitor slot records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
