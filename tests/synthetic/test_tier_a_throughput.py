"""Throughput guard for tier A. Plan.md §16.6, §17 phase 2.

Tier A throughput is a specified requirement, not a nice-to-have: the funnel in
§9.1 only works if screening is cheap enough to discard 99.9% of candidates. A
regression here degrades quietly until the funnel stops being viable, so it fails
CI instead.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.engines.tier_a.engine import run, threshold_position
from tradelab.features import momentum  # noqa: F401
from tradelab.features.materialise import materialise
from tradelab.validation.synthetic import make_noise

#: Measured at ~650/s. The floor sits well below that so ordinary machine noise
#: does not fail the build, while a structural regression — reintroducing a
#: per-bar Python loop, say — is caught. The original scanned implementation ran
#: at 35/s, which would have put §9.1's million configurations at eight hours.
MIN_EVALUATIONS_PER_SECOND = 150


@pytest.mark.slow
def test_screening_throughput() -> None:
    bars = make_noise(n=50_000, seed=1)
    periods = list(range(5, 45))
    entries = np.arange(55.0, 75.0, 1.0)

    features = materialise(bars, [("rsi", {"period": p, "source": "close"}) for p in periods])
    close = bars["close"].to_numpy().astype(np.float64)

    start = time.perf_counter()
    evaluations = 0
    for period in periods:
        signal = features[f"rsi__period{period}_sourceclose"].to_numpy().astype(np.float64)
        for entry in entries:
            run(close, threshold_position(signal, entry), BINANCE_RETAIL, "1m")
            evaluations += 1
    rate = evaluations / (time.perf_counter() - start)

    assert rate > MIN_EVALUATIONS_PER_SECOND, (
        f"tier A fell to {rate:,.0f} evaluations/s (floor {MIN_EVALUATIONS_PER_SECOND}). "
        f"At this rate the 10^6 configurations of §9.1 take "
        f"{1e6 / rate / 3600:.1f} hours."
    )


@pytest.mark.slow
def test_feature_materialisation_fuses_variants() -> None:
    """Forty RSI periods must cost close to what one costs (§6).

    Features are lazy expressions so the query planner shares one pass over the
    data. If that stops happening, feature cost becomes linear in the number of
    variants and the funnel's economics change.
    """
    bars = make_noise(n=50_000, seed=2)

    start = time.perf_counter()
    materialise(bars, [("rsi", {"period": 14, "source": "close"})])
    single = time.perf_counter() - start

    start = time.perf_counter()
    materialise(bars, [("rsi", {"period": p, "source": "close"}) for p in range(5, 45)])
    forty = time.perf_counter() - start

    assert forty < single * 15, (
        f"40 variants cost {forty / single:.1f}x a single one; expression fusion "
        f"appears to have stopped working."
    )
