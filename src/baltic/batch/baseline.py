"""The baseline for the scaling study: the SAME map function as Spark, run by a multiprocessing.Pool.

Pool(1) is "one plain Python process"; Pool(8) is "parallel Python without Spark". The parent process
does the reduce (merging per-key counts) and the de-duplication of articles that Spark does in SQL.
"""

import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from baltic import article
from baltic.batch import mapper

KEY = [mapper.COLUMNS.index(c) for c in article.KEY]  # the key's positions in a mapped row


def map_path(path: Path) -> tuple[Counter[mapper.Key], set[tuple[str, ...]]]:
    """Map one zip: summed row counts per key, and the keys of its relevant articles."""
    counts: Counter[mapper.Key] = Counter()
    ids = set()
    for kind, value in mapper.map_file(str(path), path.read_bytes()):
        if kind == "article":
            ids.add(tuple(value[i] for i in KEY))
        else:
            key, (rows, _about) = value
            counts[key] += rows
    return counts, ids


def run(files: list[Path], processes: int) -> dict[str, Any]:
    """Map every file in `processes` worker processes, reduce in the parent; returns the measurements."""
    t0 = time.perf_counter()
    counts: Counter[mapper.Key] = Counter()
    ids: set[tuple[str, ...]] = set()
    with Pool(processes) as pool:
        for c, i in pool.imap_unordered(map_path, files):  # reduce as the results arrive
            counts.update(c)
            ids |= i
    wall_s = round(time.perf_counter() - t0, 2)
    return {"files": len(files), "rows": counts.total(), "relevant": len(ids), "wall_s": wall_s}
