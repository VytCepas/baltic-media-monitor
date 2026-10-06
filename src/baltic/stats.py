"""The two statistical tools every result uses, both thin wrappers over scipy.

- ratio_difference: difference of two ratios of sums with a CLUSTER bootstrap CI. Articles from one outlet
  are not independent, so whole domains are resampled, never single articles.
- wilson: a precision k/n with its Wilson score interval (sound at small n and near 0 or 1).
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike
from scipy import stats


@dataclass(frozen=True)
class Estimate:
    """A point estimate with its 95 % confidence interval."""

    value: float
    low: float
    high: float

    def above(self, x: float) -> bool:
        """The whole interval lies above x."""
        return self.low > x

    def below(self, x: float) -> bool:
        """The whole interval lies below x."""
        return self.high < x

    def within(self, margin: float) -> bool:
        """The whole interval lies inside (-margin, +margin): equivalence at that margin."""
        return -margin < self.low and self.high < margin


def ratio_difference(
    a: tuple[ArrayLike, ArrayLike],
    b: tuple[ArrayLike, ArrayLike],
    n_resamples: int = 2000,
    seed: int = 0,
) -> Estimate:
    """sum(num_a)/sum(den_a) - sum(num_b)/sum(den_b), one array element per cluster (domain)."""
    (na, da), (nb, db) = ((np.asarray(n, float), np.asarray(d, float)) for n, d in (a, b))

    def diff(i: np.ndarray, j: np.ndarray) -> float:
        return float(na[i].sum() / da[i].sum() - nb[j].sum() / db[j].sum())

    res = stats.bootstrap(
        (np.arange(len(na)), np.arange(len(nb))),  # cluster indices, resampled per group
        diff,
        n_resamples=n_resamples,
        method="percentile",
        vectorized=False,
        rng=seed,
    )
    ci = res.confidence_interval
    return Estimate(diff(np.arange(len(na)), np.arange(len(nb))), float(ci.low), float(ci.high))


def wilson(k: int, n: int) -> Estimate:
    """Share k/n with its 95 % Wilson interval."""
    ci = stats.binomtest(k, n).proportion_ci(method="wilson")
    return Estimate(k / n, float(ci.low), float(ci.high))
