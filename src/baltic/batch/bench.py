"""The scaling study (requirement 2.3): every configuration, repeated, in a random order, resumable.

    E1 data size   Spark, all cores, 1 / 7 / 30 days
    E2 cores       Spark, 7 days, 1 / 2 / 4 / 8 cores
    E3 baseline    the same map function in Pool(1) on 7 days and Pool(8) on 1 / 7 / 30 days

Each run is a fresh process (a fresh JVM for Spark) and writes bench/TAG-rN.json; a run whose file exists is
skipped, so a reboot continues where it stopped. The order is shuffled so that warm-up or page-cache effects
do not favour any one configuration.
"""

import json
import os
import random
import subprocess
import sys
from dataclasses import asdict, dataclass

from baltic.layout import Layout, write_atomic


@dataclass(frozen=True)
class Config:
    """One benchmark configuration."""

    engine: str  # "spark" or "python"
    days: int
    workers: int  # Spark local[workers] threads, or Pool(workers) processes

    @property
    def tag(self) -> str:
        """File-name tag, e.g. spark-d7-w4."""
        return f"{self.engine}-d{self.days}-w{self.workers}"


def configs(
    days: tuple[int, ...] = (1, 7, 30), cores: tuple[int, ...] = (1, 2, 4, 8)
) -> list[Config]:
    """E1 + E2 + E3 as a set of distinct configurations (E1's 7-day run is also E2's 8-core run)."""
    mid, top = days[len(days) // 2], cores[-1]
    wanted = [Config("spark", d, top) for d in days]
    wanted += [Config("spark", mid, c) for c in cores]
    wanted += [Config("python", mid, 1)] + [Config("python", d, top) for d in days]
    return list(dict.fromkeys(wanted))


def schedule(cfgs: list[Config], repeats: int, seed: int = 1) -> list[tuple[Config, int]]:
    """Every (configuration, repeat) pair in a reproducible random order."""
    runs = [(c, r) for r in range(1, repeats + 1) for c in cfgs]
    random.Random(seed).shuffle(runs)  # noqa: S311  run order, not security
    return runs


def run_all(layout: Layout, day: str, cfgs: list[Config], repeats: int = 3) -> int:
    """Run every pending (configuration, repeat) as a subprocess; returns how many ran."""
    ran = 0
    for cfg, repeat in schedule(cfgs, repeats):
        out = layout.bench(cfg.tag, repeat)
        if out.exists() or cfg.workers > (os.cpu_count() or 1):
            continue
        command = [
            sys.executable,
            "-m",
            "baltic",
            "--data",
            str(layout.root),
            "measure",
            cfg.engine,
        ]
        command += ["--day", day, "--days", str(cfg.days), "--workers", str(cfg.workers)]
        done = subprocess.run(command, check=True, capture_output=True, text=True)  # noqa: S603  own CLI
        result = json.loads(done.stdout.splitlines()[-1])  # the last line; Spark may log above it
        record = {**asdict(cfg), "tag": cfg.tag, "repeat": repeat, "day": day, **result}
        write_atomic(
            out, json.dumps(record).encode()
        )  # a crash never leaves a half file that is skipped
        ran += 1
    return ran
