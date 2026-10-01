"""Vectorised screening engine. Plan.md §9.1.

Approximate on costs, exact on causality. The one-bar lag (signal from bar t takes effect
at t+1) is applied here centrally, not by each strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel

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
    funding_drag: float
    exposure: float
    effective_n: float


def _legs(
    close: np.ndarray, position: np.ndarray, costs: CostModel,
    already_lagged: bool, funding: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The lag, the gross leg and every cost leg, unreduced.

    Shared so `net_returns` and `run` cannot disagree about what a net return is.
    """
    if not already_lagged:
        position = np.concatenate([[0.0], position[:-1]])

    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    gross = position * returns
    traded = np.abs(np.diff(np.concatenate([[0.0], position])))

    if funding is not None:
        funding_cost = position * np.asarray(funding, dtype=np.float64)
    else:
        funding_cost = position * costs.funding_per_bar

    return position, gross, traded * costs.per_side + funding_cost, funding_cost, traded


def net_returns(
    close: np.ndarray, position: np.ndarray, costs: CostModel | None = None,
    already_lagged: bool = False, funding: np.ndarray | None = None,
) -> np.ndarray:
    """Per-bar net returns that `run` summarises; the gate's deflated Sharpe needs these."""
    costs = costs or BINANCE_RETAIL
    _, gross, cost, _, _ = _legs(close, position, costs, already_lagged, funding)
    return gross - cost


def run(
    close: np.ndarray, position: np.ndarray, costs: CostModel | None = None,
    timeframe: str = "1h", already_lagged: bool = False,
    funding: np.ndarray | None = None,
) -> BacktestResult:
    """Evaluate one position series.

    `position`: target exposure in units of equity from each bar's info; lagged one bar here
    unless `already_lagged`. `funding`: per-bar rate paid by longs, zero off-settlement.
    """
    costs = costs or BINANCE_RETAIL
    position, gross, cost, funding_cost, traded = _legs(
        close, position, costs, already_lagged, funding)
    net = gross - cost

    equity = np.cumprod(1.0 + net)
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0

    bars = BARS_PER_YEAR.get(timeframe, 8_760)
    std = net.std(ddof=1)
    gross_std = gross.std(ddof=1)
    entries = int(np.sum((traded > 0) & (np.abs(position) > 0)))
    exposure = float(np.mean(np.abs(position) > 0))

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
        funding_drag=float(np.sum(funding_cost)),
        exposure=exposure,
        effective_n=effective_n,
    )


def threshold_position(signal: np.ndarray, entry: float, exit_: float | None = None) -> np.ndarray:
    """Long above `entry`, flat below `exit_`, holding in between.

    Hysteresis stops a signal resting on its threshold flipping every bar. Vectorised as a
    forward-fill of the last crossing; a per-bar loop is far too slow for §9.1.
    """
    exit_ = entry if exit_ is None else exit_

    decision = np.where(signal > entry, 1, np.where(signal <= exit_, -1, 0)).astype(np.int8)
    decision[np.isnan(signal)] = -1

    made = decision != 0
    last = np.maximum.accumulate(np.where(made, np.arange(decision.size), -1))
    held = np.where(last >= 0, decision[last], -1)
    return (held > 0).astype(np.float64)


