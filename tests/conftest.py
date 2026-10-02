"""Shared fixtures: a data root per test, and a small synthetic month of articles."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from baltic import article, gdelt
from baltic.layout import Layout


@pytest.fixture
def layout(tmp_path):
    return Layout(tmp_path / "data")


@pytest.fixture
def month():
    """Ten days of articles in which ru_by covers the Baltics more, and more negatively off-security.

    Returns (articles, domain_day, series_15m) shaped like the lake's tables.
    """
    rng = np.random.default_rng(0)
    start = datetime(2026, 9, 1)
    groups = {
        "ru_by": (0.30, -4.0),
        "regional": (0.05, -1.0),
        "other": (0.02, -1.0),
        "baltic": (0.9, -1.0),
        "baltic_rus": (0.9, -1.5),
    }
    arts, days = [], []
    for g, (share, tone) in groups.items():
        for d in range(12):
            domain = f"site{d}.{'ru' if g == 'ru_by' else 'pl' if g == 'regional' else 'lt' if g.startswith('baltic') else 'com'}"
            for day in range(10):
                n_rows, n_about = 200, int(rng.binomial(200, share))
                days.append(
                    {
                        "dt": (start + timedelta(day)).date(),
                        "group": g,
                        "domain": domain,
                        "n_rows": n_rows,
                        "n_about": n_about,
                    }
                )
                for i in range(max(1, n_about // 2)):
                    ts = gdelt.to_ts(
                        start + timedelta(days=day, minutes=15 * int(rng.integers(96)))
                    )
                    security = bool(i % 2)
                    arts.append(
                        {
                            "id": f"{g}-{d}-{day}-{i}",
                            "ts": ts,
                            "feed": "tr",
                            "domain": domain,
                            "lang": "rus",
                            "group": g,
                            "title": f"Story {i} on day {day} about Vilnius and Riga",
                            "countries": ["LH"],
                            "about_baltic": True,
                            "security": security,
                            "themes": ["MILITARY"] if security else ["EDUCATION"],
                            "persons": [],
                            "orgs": [],
                            "tone": float(rng.normal(-1.0 if security else tone, 1.0)),
                        }
                    )
    articles = pd.DataFrame(arts)
    articles["dt"] = pd.to_datetime(articles.ts.str[:8]).dt.date
    sec = articles[articles.security]
    series = sec.groupby(["ts", "group"]).size().rename("count").reset_index()
    assert set(article.GROUPS) == set(groups)
    return articles, pd.DataFrame(days), series
