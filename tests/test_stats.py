import numpy as np
import pytest

from baltic.stats import Estimate, ratio_difference, wilson


def clusters(rate, n=40, size=100, seed=0):
    rng = np.random.default_rng(seed)
    den = np.full(n, size)
    return rng.binomial(den, rate), den


def test_equal_groups_give_an_interval_around_zero():
    e = ratio_difference(clusters(0.2, seed=1), clusters(0.2, seed=2))
    assert e.low < 0 < e.high and abs(e.value) < 0.03


def test_a_real_difference_is_detected_with_the_right_sign():
    e = ratio_difference(clusters(0.3), clusters(0.1, seed=3))
    assert e.above(0) and e.low < 0.2 < e.high


def test_clusters_widen_the_interval():
    """One huge outlet dominating a group must not look like many independent articles."""
    num_b, den_b = clusters(0.2, seed=4)
    one_big = (np.array([500, *[5] * 20]), np.array([1000, *[100] * 20]))  # 50 % vs 5 % domains
    wide = ratio_difference(one_big, (num_b, den_b))
    as_articles = (np.array([1] * 575 + [0] * 2425), np.ones(3000))  # same totals, no clustering
    narrow = ratio_difference(as_articles, (num_b, den_b))
    assert wide.high - wide.low > narrow.high - narrow.low


def test_same_seed_same_interval():
    a, b = clusters(0.3), clusters(0.2, seed=5)
    assert ratio_difference(a, b, seed=7) == ratio_difference(a, b, seed=7)


def test_decision_rules():
    e = Estimate(-1.0, -1.4, -0.6)
    assert e.below(0) and e.above(-2) and not e.above(0) and not e.within(0.5)
    assert Estimate(0.1, -0.3, 0.4).within(0.5)
    assert not Estimate(0.1, -0.3, 0.6).within(0.5)


def test_wilson_matches_the_textbook_interval():
    e = wilson(7, 10)
    assert e.value == 0.7
    assert (round(e.low, 3), round(e.high, 3)) == (0.397, 0.892)
    assert wilson(0, 30).low == pytest.approx(0, abs=1e-12) and wilson(0, 30).high > 0.1
