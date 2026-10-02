"""H1 and H2, each decided by a rule fixed before the hypothesis tests ran.

Disclosed: an exploratory pass over the 30 days (articles about the Baltics and overall tone per group, a
first shared-name pairing; no security split, no CIs) came before the rules were written down.

Every comparison is a difference of ratios of sums with a domain-clustered bootstrap CI (stats.py).
Tone is GDELT's positive % minus negative % of words: a difference is "tone points", never a ratio.

    H1  ru_by give the Baltics a larger share of their output        CI low > 0 vs other AND regional
    H2a on non-security stories ru_by tone is lower                  CI high < 0 vs other(tr) AND baltic_rus
    H2b on security stories ru_by tone is the same                   CI inside +-MARGIN vs both
"""

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from baltic.stats import Estimate, ratio_difference

MARGIN = 0.5  # H2b equivalence margin in tone points, fixed in the plan before the tests ran


def _clusters(df: pd.DataFrame, num: str, den: str) -> tuple[np.ndarray, np.ndarray]:
    """Per-domain sums of a numerator and a denominator column."""
    per = df.groupby("domain")[[num, den]].sum()
    return per[num].to_numpy(), per[den].to_numpy()


def h1(domain_day: pd.DataFrame) -> dict[str, Any]:
    """Attention: share of each group's articles that are about the Baltics."""
    totals = domain_day.groupby("group")[["n_about", "n_rows"]].sum()
    by = {g: _clusters(df, "n_about", "n_rows") for g, df in domain_day.groupby("group")}
    diffs = {ref: ratio_difference(by["ru_by"], by[ref]) for ref in ("other", "regional")}
    return {
        "share": (totals.n_about / totals.n_rows).to_dict(),
        "domains": domain_day.groupby("group").domain.nunique().to_dict(),
        "ru_by minus": {ref: asdict(e) for ref, e in diffs.items()},
        "accepted": all(e.above(0) for e in diffs.values()),
    }


def tone_differences(articles: pd.DataFrame, security: bool) -> dict[str, Estimate]:
    """ru_by mean tone minus each reference group's, on security or on non-security Baltic stories."""
    a = articles[articles.about_baltic & articles.tone.notna() & (articles.security == security)]
    a = a.assign(n=1)
    refs = {  # other foreign outlets from the translated feed: the same machine translation as ru_by
        "other (translated)": a[(a.group == "other") & (a.feed == "tr")],
        "baltic_rus": a[a.group == "baltic_rus"],
    }
    ru_by = _clusters(a[a.group == "ru_by"], "tone", "n")
    return {
        name: ratio_difference(ru_by, _clusters(ref, "tone", "n")) for name, ref in refs.items()
    }


def h2(articles: pd.DataFrame) -> dict[str, Any]:
    """Framing: H2a on non-security stories, H2b (equivalence) on security stories."""
    other, sec = tone_differences(articles, False), tone_differences(articles, True)
    if all(e.within(MARGIN) for e in sec.values()):
        verdict = "same within the margin"
    elif all(e.low <= 0 <= e.high for e in sec.values()):
        verdict = "no detectable difference"
    else:
        verdict = "different"
    a = articles[articles.about_baltic]
    return {
        "mean_tone": a.groupby(["group", "security"]).tone.mean().unstack().to_dict(),
        "H2a non-security, ru_by minus": {k: asdict(e) for k, e in other.items()},
        "H2a accepted": all(e.below(0) for e in other.values()),
        "H2b security, ru_by minus": {k: asdict(e) for k, e in sec.items()},
        "H2b verdict": verdict,
        "margin": MARGIN,
    }
