"""The baseline for the scaling study: the SAME map function as Spark, run by a multiprocessing.Pool.

Pool(1) is "one plain Python process"; Pool(8) is "parallel Python without Spark". The parent process
does the reduce (merging per-key counts) and the de-duplication of articles that Spark does in SQL.
"""

import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
from typing import Any

from baltic.batch import mapper


def map_path(path: Path) -> tuple[Counter[mapper.Key], set[str]]:
    """Map one zip: summed row counts per key, and the ids of its relevant articles."""
    counts: Counter[mapper.Key] = Counter()
    ids = set()
    for kind, value in mapper.map_file(str(path), path.read_bytes()):
        if kind == "article":
            ids.add(value[0])
        else:
            key, (rows, _about) = value
            counts[key] += rows
    return counts, ids


def run(files: list[Path], processes: int) -> dict[str, Any]:
    """Map every file in `processes` worker processes, reduce in the parent; returns the measurements."""
    t0 = time.perf_counter()
    counts: Counter[mapper.Key] = Counter()
    ids: set[str] = set()
    with Pool(processes) as pool:
        for c, i in pool.imap_unordered(map_path, files):  # reduce as the results arrive
            counts.update(c)
            ids |= i
    wall_s = round(time.perf_counter() - t0, 2)
    return {"files": len(files), "rows": counts.total(), "relevant": len(ids), "wall_s": wall_s}
