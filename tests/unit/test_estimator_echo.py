"""A shock must fade from an estimator, not fall out of it.

RESULT-004 measured what a rolling standard deviation costs when it drives a
decision. The window has two edges: it takes an observation on bar t and drops
it on bar t+N, and the drop moves the estimate as sharply as the take did. But
nothing happened on bar t+N. In PREREG-004 that second move was the single most
expensive trade of 640 days — the panel's largest return, 90 days later,
resizing the whole book for no reason connected to that day.

Sizing is where it cost money; the same edge sits under any estimator driving a
decision. `triple_barrier` sets barrier width from volatility, so a cliff
mislabels a bar as having touched nothing. `classify` sets the regime label, so
a cliff files a day under the wrong regime — and §13.1's point is that
correlation is a property of the regime, which FINDING-004 confirmed the
expensive way.

The invariant is about the *shape* of forgetting, not its speed. An estimator is
entitled to remember a shock for a long time; it is not entitled to forget it
all at once on a bar where nothing happened. So this measures how the shock's
influence decays and fails on a cliff. Asserting instead that the two series
agree a window later would fail every honest estimator, because remembering is
the point.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from tradelab.labeling.barriers import BarrierSpec, triple_barrier
from tradelab.regimes.classifier import RegimeSpec, classify

SHOCK_AT = 150
N = 600
# A single bar's departure may not erase more than this share of the peak
# influence. A rolling window scores near 1.0 by construction; smooth decay
# spreads the same total forgetting over many bars and scores far below it.
MAX_CLIFF = 0.25


def _bars(shock: float) -> pl.DataFrame:
    """A quiet random walk with one shock bar. Fixed seed, so the only
    difference between two calls is the shock itself."""
    rng = np.random.default_rng(20260926)
    steps = rng.normal(0.0, 0.002, N)
    steps[SHOCK_AT] = shock
    close = 100.0 * np.exp(np.cumsum(steps))
    stamps = np.arange(N).astype("datetime64[s]").astype("datetime64[us]")
    return pl.DataFrame({
        "event_time": stamps,
        # Synthetic bars are knowable when they close (§5); the point-in-time
        # columns are carried so this exercises the same path as real data.
        "ingest_time": stamps, "available_at": stamps,
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(N, 1_000.0),
    })


def _cliff(quiet: np.ndarray, shocked: np.ndarray) -> tuple[float, int]:
    """Largest single-bar collapse in the shock's influence, as a share of peak.

    `influence` is how far the shock has moved the estimator at each bar. It
    should fall gradually. The largest one-bar fall, divided by the peak, is
    near 1.0 when one bar's departure does all the forgetting at once.
    """
    influence = np.abs(np.nan_to_num(shocked) - np.nan_to_num(quiet))[SHOCK_AT:]
    peak = influence.max()
    assert peak > 0, "the shock never reached the estimator at all"
    drops = influence[:-1] - influence[1:]
    return float(drops.max() / peak), int(np.argmax(drops)) + SHOCK_AT


@pytest.mark.parametrize("period", [60, 100])
def test_barrier_volatility_fades_rather_than_falling_off_a_cliff(period: int) -> None:
    spec = BarrierSpec(vol_period=period)
    quiet = triple_barrier(_bars(0.002), spec=spec)["vol"].to_numpy()
    shocked = triple_barrier(_bars(0.20), spec=spec)["vol"].to_numpy()

    cliff, where = _cliff(quiet, shocked)
    assert cliff < MAX_CLIFF, (
        f"{cliff:.0%} of the shock's influence on barrier width vanished in a "
        f"single bar, at {where - SHOCK_AT} bars after the shock (window "
        f"{period}). Barriers set on either side of that bar are decided by the "
        "window's trailing edge, not by price."
    )


@pytest.mark.parametrize("period", [60, 100])
def test_regime_volatility_fades_rather_than_falling_off_a_cliff(period: int) -> None:
    spec = RegimeSpec(vol_period=period, warmup=50)
    quiet = classify(_bars(0.002), spec=spec)["realised_vol"].to_numpy()
    shocked = classify(_bars(0.20), spec=spec)["realised_vol"].to_numpy()

    cliff, where = _cliff(quiet, shocked)
    assert cliff < MAX_CLIFF, (
        f"{cliff:.0%} of the shock's influence on the regime volatility vanished "
        f"in a single bar, {where - SHOCK_AT} bars after the shock (window "
        f"{period}). Days on either side of it are filed under different regimes "
        "for no reason present in the data."
    )


def test_the_shock_is_still_seen_when_it_happens() -> None:
    """Guards the other direction: a 'fix' that flattens the estimator until
    nothing moves it would pass every cliff test above."""
    spec = RegimeSpec(vol_period=100, warmup=50)
    quiet = classify(_bars(0.002), spec=spec)["regime"].to_numpy()
    shocked = classify(_bars(0.20), spec=spec)["regime"].to_numpy()
    at_shock = slice(SHOCK_AT, SHOCK_AT + 10)
    moved = int(np.sum(quiet[at_shock] != shocked[at_shock]))
    assert moved > 0, "the shock changed no regime label when it happened"
