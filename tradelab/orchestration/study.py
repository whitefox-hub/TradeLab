"""End-to-end study execution. Plan.md §9.1, §8, §15.

This is the funnel as one callable: load data, build features, label, screen,
validate, judge. Every component it uses was tested in isolation; what this adds
is the wiring, and the wiring is where the trial counter and the effective sample
size have to survive the journey intact.

Two invariants it exists to hold:

**Every configuration evaluated counts as a trial**, including the ones tier A
discards. That number is what deflates the Sharpe of whatever survives (§8.3),
and losing it anywhere between the grid and the verdict silently inflates the
result.

**The observation count that reaches the statistics is the effective one** (§7),
never the number of bars. A strategy holding for days produces serially dependent
returns, and treating them as independent overstates every significance figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, PESSIMISTIC, CostModel
from tradelab.engines.tier_a.engine import BacktestResult, run, threshold_position
from tradelab.features.materialise import materialise
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import GateEvaluation, Verdict, evaluate
from tradelab.validation.pbo import probability_of_backtest_overfitting
from tradelab.validation.robustness import sharpe_interval
from tradelab.validation.stability import Surface, analyse, plateau_width


@dataclass
class StudySpec:
    name: str
    feature: str = "rsi"
    periods: tuple[int, ...] = tuple(range(5, 41))
    entries: tuple[float, ...] = tuple(np.arange(52.0, 80.0, 1.0))
    timeframe: str = "1h"
    costs: CostModel = BINANCE_RETAIL
    min_detectable_sharpe: float | None = None
    oos_fraction: float = 0.3
    years: float | None = None      # None uses the whole archive

    @property
    def planned_trials(self) -> int:
        return len(self.periods) * len(self.entries)


@dataclass
class StudyResult:
    spec: StudySpec
    executed_trials: int
    n_bars: int
    best: BacktestResult | None
    best_params: dict
    gate: GateEvaluation
    stability: dict = field(default_factory=dict)
    robustness: dict = field(default_factory=dict)
    pbo: float | None = None
    # Every screened configuration. Written to Parquet, never to Postgres (§5.7),
    # but retained so a verdict citing N trials can be checked against N rows
    # rather than taken on trust.
    tier_a_rows: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        return self.gate.verdict


def load_bars(
    root: Path, asset: str, timeframe: str = "1h",
    years: float | None = None, limit_days: int | None = None,
):
    """Resample the 1m archive to the study timeframe.

    Derived, not stored (§5.7), and the day boundary is UTC because funding
    settles on UTC marks.

    `years` trims to the most recent span. It is offered because the tension is
    real — 2020 crypto is not the market of 2026, and a strategy validated
    against a regime that no longer exists is validated against nothing — but it
    is worth knowing what it costs. The detectable floor of §8.8 scales roughly
    as 1/sqrt(n): halving the history raises the smallest resolvable effect by
    about 40%, on a search that already came back underpowered at seven years.

    Shortening the window is therefore not the way to make a study easier. The
    resolution for non-stationarity is walk-forward (§8.1), which uses all the
    history without ever assuming one regime spans it.
    """
    from tradelab.storage.query import aggregate_bars

    # `aggregate_bars` carries the last source `event_time` alongside the bucket;
    # the bucket is the bar's own timestamp, so the source column is dropped.
    bars = (
        aggregate_bars(root, asset, timeframe)
        .drop("event_time", "available_at")
        .rename({"bucket": "event_time"})
    )
    if years is not None:
        limit_days = int(years * 365)
    if limit_days:
        bars = bars.tail(limit_days * _bars_per_day(timeframe))
    return bars.drop_nulls(subset=["open", "high", "low", "close"])


def _bars_per_day(timeframe: str) -> int:
    unit, amount = timeframe[-1], int(timeframe[:-1])
    return {"m": 1440 // amount, "h": 24 // amount, "d": 1}[unit]


def run_study(bars: pl.DataFrame, spec: StudySpec) -> StudyResult:
    """Screen the grid, validate the survivor, return a verdict."""
    features = materialise(
        bars,
        [(spec.feature, {"period": p, "source": "close"}) for p in spec.periods],
        keep=("event_time", "close"),
    )
    close = bars["close"].to_numpy().astype(np.float64)
    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    split = int(len(close) * (1 - spec.oos_fraction))

    results: list[BacktestResult] = []
    params: list[dict] = []
    gross_matrix: list[np.ndarray] = []
    turnover: list[float] = []
    surface_points: list[tuple[dict, float]] = []

    for period in spec.periods:
        signal = features[f"{spec.feature}__period{period}_sourceclose"].to_numpy()
        signal = signal.astype(np.float64)
        for entry in spec.entries:
            position = threshold_position(signal, float(entry))
            result = run(close, position, spec.costs, spec.timeframe)
            results.append(result)
            params.append({"period": period, "entry": float(entry)})

            lagged = np.concatenate([[0.0], position[:-1]])
            traded = np.abs(np.diff(np.concatenate([[0.0], lagged])))
            gross_matrix.append(lagged * returns)
            turnover.append(float(traded.sum()))
            # In-sample score only: selecting on the full series would be
            # choosing with the test set in hand.
            surface_points.append(({"period": period, "entry": float(entry)},
                                   result.gross_sharpe))

    executed = len(results)
    if not executed:
        raise ValueError("study evaluated nothing")

    # Selection on the smoothed surface: the centre of the best region rather
    # than the luckiest single draw (§8.5).
    surface = Surface.build(surface_points, ["period", "entry"])
    stability = analyse(surface, radius=1)
    chosen = params.index(stability.peak_params)
    best = results[chosen]

    net = gross_matrix[chosen].copy()
    oos = net[split:]
    oos_result = run(close[split:], threshold_position(
        features[f"{spec.feature}__period{stability.peak_params['period']}_sourceclose"]
        .to_numpy().astype(np.float64)[split:],
        stability.peak_params["entry"]), spec.costs, spec.timeframe)

    stats = sharpe_stats(oos, effective_n=oos_result.effective_n)
    pbo = probability_of_backtest_overfitting(
        np.column_stack(gross_matrix), n_blocks=8, turnover=np.asarray(turnover)
    )

    gate = evaluate(
        stats, trials=executed, n_oos_trades=oos_result.n_trades, pbo=pbo,
        thresholds=None, bars_per_year=8_760,
        min_detectable_sharpe=spec.min_detectable_sharpe,
    )

    robustness: dict = {}
    if oos_result.n_trades >= 2:
        interval = sharpe_interval(oos[oos != 0], n_samples=500)
        robustness = {
            "sharpe_ci_low": interval.low,
            "sharpe_ci_high": interval.high,
            "ci_excludes_zero": interval.excludes_zero,
        }

    tier_a_rows = [
        {
            "period": p["period"], "entry": p["entry"],
            "sharpe": r.sharpe, "gross_sharpe": r.gross_sharpe,
            "n_trades": r.n_trades, "turnover": t,
            "effective_n": r.effective_n, "max_drawdown": r.max_drawdown,
            "config_hash": f"{spec.name}:{p['period']}:{p['entry']}",
        }
        for p, r, t in zip(params, results, turnover, strict=True)
    ]

    return StudyResult(
        spec=spec, executed_trials=executed, n_bars=len(close), best=best,
        best_params=stability.peak_params, gate=gate, pbo=pbo.pbo,
        stability={
            "peak_score": stability.peak_score,
            "robust_score": stability.robust_score,
            "is_spike": stability.is_spike,
            "plateau_width_period": plateau_width(surface, "period"),
        },
        robustness=robustness,
        tier_a_rows=tier_a_rows,
    )


def cost_sensitivity(bars: pl.DataFrame, spec: StudySpec) -> dict[str, Verdict]:
    """The same study under realistic and pessimistic fills (§9.5).

    Promotion requires surviving the pessimistic column; a strategy that only
    works under optimistic fills only works in the backtest.
    """
    out = {}
    for label, costs in (("realistic", BINANCE_RETAIL), ("pessimistic", PESSIMISTIC)):
        variant = StudySpec(**{**spec.__dict__, "costs": costs})
        out[label] = run_study(bars, variant).verdict
    return out
