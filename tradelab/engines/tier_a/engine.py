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
    cost_drag: float            # turnover plus funding
    funding_drag: float
    exposure: float
    # Independent bets, not bars. A position held for 240 bars produces 240
    # serially dependent returns, not 240 observations, and every significance
    # figure computed from the bar count inherits that inflation (§7).
    effective_n: float


def _legs(
    close: np.ndarray, position: np.ndarray, costs: CostModel,
    already_lagged: bool, funding: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The lag, the gross leg and every cost leg. Returns them unreduced.

    Split out so `net_returns` below and `run` cannot disagree about what a net
    return is. They did: the scenario runner judged a gate on the asset's own
    price changes instead of the strategy's net returns, which for a constant
    long position differ by 0.04 of Sharpe and for anything else by twelve or
    more — a figure that looked right because the one case in use made two
    different things coincide.
    """
    if not already_lagged:
        position = np.concatenate([[0.0], position[:-1]])

    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    gross = position * returns
    traded = np.abs(np.diff(np.concatenate([[0.0], position])))

    # Charged on the position held, signed: longs pay, shorts receive. A flat
    # rate is a fallback; the real series belongs here whenever it is available.
    if funding is not None:
        funding_cost = position * np.asarray(funding, dtype=np.float64)
    else:
        funding_cost = position * costs.funding_per_bar

    return position, gross, traded * costs.per_side + funding_cost, funding_cost, traded


def net_returns(
    close: np.ndarray, position: np.ndarray, costs: CostModel | None = None,
    already_lagged: bool = False, funding: np.ndarray | None = None,
) -> np.ndarray:
    """The per-bar net return series `run` reduces to its summary figures.

    Exposed because the gate needs the series and not the summary: a deflated
    Sharpe is computed from returns, and anything else handed to it is a
    statistic about something the strategy did not earn.
    """
    costs = costs or BINANCE_RETAIL
    _, gross, cost, _, _ = _legs(close, position, costs, already_lagged, funding)
    return gross - cost


def run(
    close: np.ndarray, position: np.ndarray, costs: CostModel | None = None,
    timeframe: str = "1h", already_lagged: bool = False,
    funding: np.ndarray | None = None,
) -> BacktestResult:
    """Evaluate one position series.

    `position` is the target exposure in units of equity, as decided from each
    bar's information. It is lagged by one bar here unless the caller states it
    has already been lagged — and stating that is deliberately awkward, because
    doing it twice is harmless and forgetting it once is fatal.

    `funding` is the per-bar rate paid by longs, zero where no settlement falls
    in that bar. It is charged against the position held, so a long pays it and a
    short receives it.

    Funding is not a detail of perpetuals, it is a large recurring cost that
    §0.1's arithmetic did not contain: measured on BTCUSDT from 2020 to 2026 it
    averaged 0.0108% every eight hours — **11.8% a year**, and 78.7% cumulative
    over the period. That is comparable to the entire turnover drag of a
    three-day holding period, so omitting it roughly halves the measured cost of
    any long-biased strategy.
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
        funding_drag=float(np.sum(funding_cost)),
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


