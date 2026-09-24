"""Seeded generators with known ground truth. Plan.md §16.4.

The only place in the system where "it got the right answer" is a checkable
statement, because everywhere else nobody knows what the right answer is.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl


def make_noise(
    n: int = 20_000, seed: int = 20260101, sigma: float = 5e-4, start: float = 50_000.0,
    garch_alpha: float = 0.08, garch_beta: float = 0.90,
) -> pl.DataFrame:
    """Driftless bars with GARCH volatility clustering and no edge whatsoever.

    Clustering matters: constant-volatility noise is easier to reject than the
    real thing, so a validator tested only against it would be tested against a
    weaker adversary than the market.
    """
    rng = np.random.default_rng(seed)
    variance = np.empty(n)
    variance[0] = sigma**2
    shocks = rng.standard_normal(n)
    returns = np.empty(n)
    returns[0] = shocks[0] * sigma
    omega = sigma**2 * (1 - garch_alpha - garch_beta)
    for i in range(1, n):
        variance[i] = omega + garch_alpha * returns[i - 1] ** 2 + garch_beta * variance[i - 1]
        returns[i] = shocks[i] * np.sqrt(variance[i])

    close = start * np.exp(np.cumsum(returns))
    # Intrabar extremes from a bridge, so highs and lows are consistent with the
    # path rather than bolted on.
    spread = np.abs(rng.standard_normal(n)) * np.sqrt(variance)
    open_ = np.concatenate([[start], close[:-1]])
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)

    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    times = [t0 + timedelta(minutes=i) for i in range(n)]
    return pl.DataFrame({
        "event_time": times,
        "available_at": times,
        "venue": ["synthetic"] * n,
        "asset": ["NOISE"] * n,
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.abs(rng.standard_normal(n)) * 100 + 10,
        "quote_volume": np.abs(rng.standard_normal(n)) * 1e6 + 1e5,
        "trade_count": rng.integers(50, 5000, n),
        "taker_buy_base": np.abs(rng.standard_normal(n)) * 50 + 5,
    })


def make_regime_switch(
    n: int = 20_000, seed: int = 20260101, n_regimes: int = 4,
    sigmas: tuple[float, ...] = (2e-4, 1.2e-3),
    drifts: tuple[float, ...] = (0.0, 6e-5),
) -> tuple[pl.DataFrame, np.ndarray]:
    """Bars with known regime switch points. Plan.md §16.4.

    Returns the bars and the true regime index per bar, so a classifier can be
    asked to recover something whose answer exists rather than judged by whether
    its output looks plausible.
    """
    rng = np.random.default_rng(seed)
    bounds = np.linspace(0, n, n_regimes + 1).astype(int)
    truth = np.zeros(n, dtype=np.int8)
    returns = np.zeros(n)
    for k in range(n_regimes):
        lo, hi = bounds[k], bounds[k + 1]
        sigma = sigmas[k % len(sigmas)]
        drift = drifts[(k // len(sigmas)) % len(drifts)]
        returns[lo:hi] = rng.standard_normal(hi - lo) * sigma + drift
        truth[lo:hi] = k

    close = 50_000.0 * np.exp(np.cumsum(returns))
    spread = np.abs(rng.standard_normal(n)) * np.abs(returns)
    open_ = np.concatenate([[close[0]], close[:-1]])
    t0 = datetime(2020, 1, 1, tzinfo=UTC)
    times = [t0 + timedelta(minutes=i) for i in range(n)]
    return pl.DataFrame({
        "event_time": times, "available_at": times,
        "venue": ["synthetic"] * n, "asset": ["SWITCH"] * n,
        "open": open_, "close": close,
        "high": np.maximum(open_, close) * (1 + spread),
        "low": np.minimum(open_, close) * (1 - spread),
        "volume": np.abs(rng.standard_normal(n)) * 100 + 10,
        "quote_volume": np.abs(rng.standard_normal(n)) * 1e6 + 1e5,
        "trade_count": rng.integers(50, 5000, n),
        "taker_buy_base": np.abs(rng.standard_normal(n)) * 50 + 5,
    }), truth


def make_edge(
    n: int = 20_000, seed: int = 20260101, sigma: float = 5e-4,
    edge_sharpe: float = 1.0, signal_period: int = 60, **kwargs,
) -> tuple[pl.DataFrame, np.ndarray]:
    """Noise plus a predictable component of known size.

    Returns the bars and the signal that generates the edge, so a validator can
    be asked to find a thing whose true magnitude is known rather than guessed.
    """
    rng = np.random.default_rng(seed + 1)
    frame = make_noise(n, seed, sigma, **kwargs)
    signal = np.sin(np.arange(n) * 2 * np.pi / signal_period) + 0.3 * rng.standard_normal(n)
    # Per-bar drift that yields the requested annualised Sharpe at this bar count.
    per_bar = edge_sharpe * sigma / np.sqrt(np.mean(signal**2))
    close = frame["close"].to_numpy() * np.exp(np.cumsum(per_bar * signal))
    scale = close / frame["close"].to_numpy()
    return frame.with_columns(
        close=pl.Series(close),
        open=pl.Series(frame["open"].to_numpy() * scale),
        high=pl.Series(frame["high"].to_numpy() * scale),
        low=pl.Series(frame["low"].to_numpy() * scale),
    ), signal
