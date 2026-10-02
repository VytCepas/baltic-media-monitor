"""The MAP step of the batch layer, shared by the Spark job (etl) and the plain-Python baseline.

A GKG zip is the unit of map work (a zip cannot be split). The mapper parses every row and emits two kinds of
records: each relevant article, and per-(day, group, domain) row counts that it has already summed locally
(a combiner, so the shuffle moves thousands of counts instead of 350k rows a day).
"""

from collections import Counter
from collections.abc import Iterator
from typing import Any, get_type_hints

from baltic import article, gdelt

_SPARK_TYPE: dict[object, str] = {
    str: "string",
    bool: "boolean",
    list[str]: "array<string>",
    float | None: "double",
}
# One schema, derived from Article: Spark matches columns by position
_FIELDS = get_type_hints(article.Article)
COLUMNS = tuple(_FIELDS)
SCHEMA = ", ".join(f"{c} {_SPARK_TYPE[t]}" for c, t in _FIELDS.items())
Key = tuple[str, str, str]  # (day YYYYMMDD, group, domain)


def map_file(path: str, data: bytes) -> Iterator[tuple[str, Any]]:
    """Yield ("article", row) per relevant article, then ("count", (key, (rows, about))) per key."""
    feed = "tr" if "feed=tr" in path else "en"
    rows: Counter[Key] = Counter()
    about: Counter[Key] = Counter()
    for raw in gdelt.read_rows(data):
        a = article.parse(raw, feed)
        if a is None:
            continue
        key = (a["ts"][:8], a["group"], a["domain"])
        rows[key] += 1
        about[key] += a["about_baltic"]
        if article.is_relevant(a):
            yield "article", tuple(a[c] for c in COLUMNS)  # type: ignore[literal-required]
    for key, n in rows.items():
        yield "count", (key, (n, about[key]))


def add_counts(x: tuple[int, int], y: tuple[int, int]) -> tuple[int, int]:
    """The REDUCE function: sum (rows, about) pairs."""
    return x[0] + y[0], x[1] + y[1]
