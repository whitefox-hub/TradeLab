"""Vectorised screening engine. Plan.md §9.1.

Tier A evaluates 10^4-10^6 configurations by turning the whole backtest into
array arithmetic. It is approximate on costs and exact on causality — the reverse
would be worthless.

The causality rule, and the only place it lives:

    a signal computed from bar t may only take effect from bar t+1

A feature is allowed to use bar t's close because that bar has closed. Acting on
it within bar t is not; that is trading on a price you learn at the end of the
period. One `shift(1)` is the entire difference between a screening engine and a
machine for generating impossible equity curves, so it is applied here, centrally,
rather than left to each strategy to remember.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel

#: Bars per year by timeframe, for annualisation.
BARS_PER_YEAR = {"1m": 525_600, "5m": 105_120, "15m": 35_040,
                 "1h": 8_760, "4h": 2_190, "1d": 365}


@dataclass(frozen=True, slots=True)
class BacktestResult:
    sharpe: float
    total_return: float
    max_drawdown: float
    n_trades: int
    turnover: float
    avg_holding_bars: float
    gross_sharpe: float
    cost_drag: float
    exposure: float
    # Independent bets, not bars. A position held for 240 bars produces 240
    # serially dependent returns, not 240 observations, and every significance
    # figure computed from the bar count inherits that inflation (§7).
    effective_n: float

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


def run(
    close: np.ndarray, position: np.ndarray, costs: CostModel | None = None,
    timeframe: str = "1h", already_lagged: bool = False,
) -> BacktestResult:
    """Evaluate one position series.

    `position` is the target exposure in units of equity, as decided from each
    bar's information. It is lagged by one bar here unless the caller states it
    has already been lagged — and stating that is deliberately awkward, because
    doing it twice is harmless and forgetting it once is fatal.
    """
    costs = costs or BINANCE_RETAIL
    if not already_lagged:
        position = np.concatenate([[0.0], position[:-1]])

    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    gross = position * returns
    traded = np.abs(np.diff(np.concatenate([[0.0], position])))
    cost = traded * costs.per_side
    net = gross - cost

    equity = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0

    bars = BARS_PER_YEAR.get(timeframe, 8_760)
    std = net.std(ddof=1)
    gross_std = gross.std(ddof=1)
    entries = int(np.sum((traded > 0) & (np.abs(position) > 0)))
    exposure = float(np.mean(np.abs(position) > 0))

    # Effective observations: completed holding periods plus the flat stretches
    # between them, which are genuinely independent of each other. Bounded below
    # by 1 so downstream statistics never divide by zero on a strategy that
    # never traded.
    flat_runs = int(np.sum(np.diff(np.concatenate([[0.0], (position == 0).astype(float)])) > 0))
    effective_n = float(max(entries + flat_runs, 1))

    return BacktestResult(
        sharpe=float(net.mean() / std * np.sqrt(bars)) if std > 0 else 0.0,
        total_return=float(equity[-1] - 1.0),
        max_drawdown=float(drawdown.min()),
        n_trades=entries,
        turnover=float(traded.sum()),
        avg_holding_bars=float(exposure * len(close) / max(entries, 1)),
        gross_sharpe=float(gross.mean() / gross_std * np.sqrt(bars)) if gross_std > 0 else 0.0,
        cost_drag=float(cost.sum()),
        exposure=exposure,
        effective_n=effective_n,
    )


def threshold_position(signal: np.ndarray, entry: float, exit_: float | None = None) -> np.ndarray:
    """Long above `entry`, flat below `exit_`, holding in between.

    Hysteresis is not a refinement: without it a signal resting on its threshold
    flips every bar, and the turnover that produces is an artefact of the
    threshold rather than a property of the strategy — charged at full cost.

    Vectorised rather than scanned. The state only changes where the signal
    crosses a threshold, so the whole path is a forward-fill of the last crossing
    instead of a per-bar loop. That is what takes tier A from 35 evaluations a
    second to a useful rate; at the previous speed the 10^6 configurations §9.1
    calls for would have taken eight hours.
    """
    exit_ = entry if exit_ is None else exit_

    # +1 enter, -1 exit, 0 undecided (inside the band, or not a number).
    decision = np.where(signal > entry, 1, np.where(signal <= exit_, -1, 0)).astype(np.int8)
    decision[np.isnan(signal)] = -1

    # Forward-fill the last decision: index of the most recent non-zero entry.
    made = decision != 0
    last = np.maximum.accumulate(np.where(made, np.arange(decision.size), -1))
    held = np.where(last >= 0, decision[last], -1)
    return (held > 0).astype(np.float64)


def run_frame(
    frame: pl.DataFrame, signal_column: str, entry: float, exit_: float | None = None,
    costs: CostModel | None = None, timeframe: str = "1h",
) -> BacktestResult:
    close = frame["close"].to_numpy().astype(np.float64)
    signal = frame[signal_column].to_numpy().astype(np.float64)
    return run(close, threshold_position(signal, entry, exit_), costs, timeframe)
