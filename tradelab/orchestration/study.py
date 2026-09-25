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
    """One hypothesis and the grid that tests it.

    `param_name` and `params` describe the feature's own axis; `entries` the
    threshold the signal is compared against. Keeping them separate is what lets
    a single study test any registered feature rather than only the one it was
    written for — and testing only the feature it was written for is how a lab
    ends up having judged nothing but RSI.
    """

    name: str
    feature: str = "rsi"
    param_name: str = "period"
    params: tuple = tuple(range(5, 41))
    entries: tuple[float, ...] = tuple(np.arange(52.0, 80.0, 1.0))
    extra_params: dict = field(default_factory=lambda: {"source": "close"})
    timeframe: str = "1h"
    costs: CostModel = BINANCE_RETAIL
    min_detectable_sharpe: float | None = None
    oos_fraction: float = 0.3
    years: float | None = None      # None uses the whole archive
    # Configurations below this are excluded from SELECTION, not merely rejected
    # afterwards (§8.7). A surface scored on Sharpe alone is won by whichever
    # configuration traded least: few trades give a noisy Sharpe, and the maximum
    # of a noisy quantity is what a search finds. The gate would then reject it
    # and the whole study is spent on a configuration that was never eligible.
    min_trades: int = 100
    # Long above the threshold, or short below it. Funding extremes are read
    # short-side more naturally than momentum is.
    direction: int = 1

    @property
    def planned_trials(self) -> int:
        return len(self.params) * len(self.entries)

    def feature_set(self) -> list[tuple[str, dict]]:
        return [
            (self.feature, {self.param_name: value, **self.extra_params})
            for value in self.params
        ]

    def column(self, value) -> str:
        from tradelab.features import REGISTRY

        return REGISTRY.get(self.feature).column(
            {self.param_name: value, **self.extra_params}
        )


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
    with_funding_series: bool = True,
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
    bars = bars.drop_nulls(subset=["open", "high", "low", "close"])
    return with_funding(root, asset, bars) if with_funding_series else bars


def with_funding(root: Path, asset: str, bars: pl.DataFrame) -> pl.DataFrame:
    """Attach the funding rate, aligned to the bar containing each settlement.

    Settlements land at 00:00, 08:00 and 16:00 UTC, so on hourly bars most bars
    carry zero. Left-joined and filled rather than forward-filled: funding is a
    payment at an instant, not a level that persists, and carrying it forward
    would charge it every bar.
    """
    from tradelab.storage.query import scan

    try:
        funding = scan(root, "funding", asset, columns="event_time, funding_rate")
    except Exception:
        return bars.with_columns(funding_rate=pl.lit(0.0))
    if not funding.height:
        return bars.with_columns(funding_rate=pl.lit(0.0))

    step = bars["event_time"].diff().drop_nulls().median()
    aligned = funding.with_columns(pl.col("event_time").dt.truncate(step))
    return (
        bars.join(aligned.group_by("event_time").agg(pl.col("funding_rate").sum()),
                  on="event_time", how="left")
        .with_columns(pl.col("funding_rate").fill_null(0.0))
    )


def _funding_series(bars: pl.DataFrame) -> np.ndarray | None:
    """Per-bar funding, zero where no settlement falls in that bar.

    Costs 11.8% a year on a long perpetual position (§9.5), so a study that omits
    it understates a long-biased result by roughly half its cost.
    """
    if "funding_rate" not in bars.columns:
        return None
    return bars["funding_rate"].fill_null(0.0).to_numpy().astype(np.float64)


def _bars_per_day(timeframe: str) -> int:
    unit, amount = timeframe[-1], int(timeframe[:-1])
    return {"m": 1440 // amount, "h": 24 // amount, "d": 1}[unit]


def run_study(bars: pl.DataFrame, spec: StudySpec) -> StudyResult:
    """Screen the grid, validate the survivor, return a verdict."""
    features = materialise(bars, spec.feature_set(), keep=("event_time", "close"))
    close = bars["close"].to_numpy().astype(np.float64)
    returns = np.zeros_like(close)
    returns[1:] = np.diff(close) / close[:-1]

    split = int(len(close) * (1 - spec.oos_fraction))

    results: list[BacktestResult] = []
    params: list[dict] = []
    gross_matrix: list[np.ndarray] = []
    turnover: list[float] = []
    surface_points: list[tuple[dict, float]] = []

    funding = _funding_series(bars)

    for value in spec.params:
        signal = features[spec.column(value)].to_numpy().astype(np.float64)
        for entry in spec.entries:
            position = threshold_position(signal, float(entry)) * spec.direction
            # Scored on the TRAINING window only. An earlier version passed the
            # full series here while the comment below asserted it did not,
            # which is the worst shape a defect can take: the documentation
            # states the invariant and the code violates it, so a reader who
            # checks the comment concludes it holds. Every result produced
            # before this was selection-contaminated — the evaluation was out of
            # sample, but the configuration had been chosen with the test set in
            # hand.
            result = run(close[:split], position[:split], spec.costs, spec.timeframe,
                         funding=funding[:split] if funding is not None else None)
            results.append(result)
            params.append({spec.param_name: value, "entry": float(entry)})

            lagged = np.concatenate([[0.0], position[:-1]])
            traded = np.abs(np.diff(np.concatenate([[0.0], lagged])))
            gross_matrix.append(lagged * returns)
            turnover.append(float(traded.sum()))
            # Configurations that did not trade enough are scored -inf rather
            # than omitted, so the surface keeps its shape for the stability
            # analysis (§8.5) while remaining unselectable.
            eligible = result.n_trades >= spec.min_trades
            surface_points.append((
                {spec.param_name: value, "entry": float(entry)},
                result.gross_sharpe if eligible else float("-inf"),
            ))

    executed = len(results)
    if not executed:
        raise ValueError("study evaluated nothing")

    # Selection on the smoothed surface: the centre of the best region rather
    # than the luckiest single draw (§8.5).
    surface = Surface.build(surface_points, [spec.param_name, "entry"])
    if not np.isfinite(surface.scores).any():
        raise ValueError(
            f"no configuration reached {spec.min_trades} trades; the grid is "
            f"testing thresholds the signal rarely crosses"
        )
    stability = analyse(surface, radius=1)
    chosen = params.index(stability.peak_params)
    best = results[chosen]

    net = gross_matrix[chosen].copy()
    oos = net[split:]
    chosen_signal = features[spec.column(stability.peak_params[spec.param_name])]
    oos_result = run(
        close[split:],
        threshold_position(chosen_signal.to_numpy().astype(np.float64)[split:],
                           stability.peak_params["entry"]) * spec.direction,
        spec.costs, spec.timeframe,
        funding=funding[split:] if funding is not None else None,
    )

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
            "param": p[spec.param_name], "entry": p["entry"],
            "sharpe": r.sharpe, "gross_sharpe": r.gross_sharpe,
            "n_trades": r.n_trades, "turnover": t,
            "effective_n": r.effective_n, "max_drawdown": r.max_drawdown,
            "config_hash": f"{spec.name}:{p[spec.param_name]}:{p['entry']}",
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
            "plateau_width": plateau_width(surface, spec.param_name),
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
