"""End-to-end study execution. Plan.md §9.1, §8, §15.

Invariants: every configuration evaluated counts as a trial (deflates Sharpe, §8.3), and
statistics use the effective observation count, never the bar count (§7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL, CostModel
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

    `param_name`/`params` are the feature's own axis; `entries` the signal threshold.
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
    # Below this, excluded from selection, not merely rejected afterwards (§8.7):
    # otherwise the noisiest low-trade Sharpe wins.
    min_trades: int = 100
    # Target annualised Sharpe; power is checked before the grid runs (§8.8), since
    # trials spent are never refunded.
    target_sharpe_annual: float = 1.0
    require_power: bool = True
    # 1: long above the threshold; -1: short.
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
    # Held-out evaluation. Read OOS figures from here, not `gate.metrics`, which lacks
    # them when the gate short-circuits below the trade minimum.
    oos: BacktestResult | None = None
    stability: dict = field(default_factory=dict)
    robustness: dict = field(default_factory=dict)
    pbo: float | None = None
    # Every screened configuration; to Parquet, never Postgres (§5.7).
    tier_a_rows: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        return self.gate.verdict


def load_bars(
    root: Path, asset: str, timeframe: str = "1h",
    years: float | None = None, limit_days: int | None = None,
    with_funding_series: bool = True,
):
    """Resample the 1m archive to the study timeframe (derived, §5.7; UTC day boundary).

    `years` trims to the recent span; the §8.8 detectable floor scales ~1/sqrt(n).
    """
    from tradelab.storage.materialise_bars import load_materialised
    from tradelab.storage.query import aggregate_bars

    # Prefer a fresh materialised view: deriving from 1m costs minutes per asset (§9.6).
    cached = load_materialised(root, asset, timeframe)
    if cached is not None:
        bars = cached.sort("event_time")
        if years is not None:
            limit_days = int(years * 365)
        if limit_days:
            bars = bars.tail(limit_days * _bars_per_day(timeframe))
        return bars

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

    Zero-filled, never forward-filled: funding is a payment at an instant, not a level.
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


def _check_power(spec: StudySpec, features: pl.DataFrame, close: np.ndarray,
                 funding: np.ndarray | None) -> None:
    """Size the study from one pilot at the median parameter and threshold.

    Reads holding structure only, never returns, so it is not a trial (§8.3).
    """
    from tradelab.engines.tier_a.engine import BARS_PER_YEAR
    from tradelab.validation.power import pilot_effective_n, require_power

    mid_value = sorted(spec.params)[len(spec.params) // 2]
    mid_entry = sorted(spec.entries)[len(spec.entries) // 2]
    signal = features[spec.column(mid_value)].to_numpy().astype(np.float64)
    position = threshold_position(signal, float(mid_entry)) * spec.direction

    available = pilot_effective_n(close, position, spec.oos_fraction)
    require_power(
        spec.target_sharpe_annual, spec.planned_trials, available,
        BARS_PER_YEAR.get(spec.timeframe, 8_760),
    )


def _funding_series(bars: pl.DataFrame) -> np.ndarray | None:
    """Per-bar funding, zero where no settlement falls in that bar (§9.5)."""
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
    net_matrix: list[np.ndarray] = []
    turnover: list[float] = []
    surface_points: list[tuple[dict, float]] = []

    funding = _funding_series(bars)

    if spec.require_power:
        _check_power(spec, features, close, funding)

    for value in spec.params:
        signal = features[spec.column(value)].to_numpy().astype(np.float64)
        for entry in spec.entries:
            position = threshold_position(signal, float(entry)) * spec.direction
            # Scored on the TRAINING window only; the full series leaks the test set
            # into selection.
            result = run(close[:split], position[:split], spec.costs, spec.timeframe,
                         funding=funding[:split] if funding is not None else None)
            results.append(result)
            params.append({spec.param_name: value, "entry": float(entry)})

            lagged = np.concatenate([[0.0], position[:-1]])
            traded = np.abs(np.diff(np.concatenate([[0.0], lagged])))
            gross_matrix.append(lagged * returns)
            # PBO takes gross (§8.3); the verdict takes net.
            net_row = lagged * returns - traded * spec.costs.per_side
            if funding is not None:
                net_row = net_row - lagged * funding
            net_matrix.append(net_row)
            turnover.append(float(traded.sum()))
            # Selection scores NET (gross picks what costs destroy); PBO stays on gross.
            # Below min_trades: -inf, not omitted, so the surface keeps its shape (§8.5).
            eligible = result.n_trades >= spec.min_trades
            surface_points.append((
                {spec.param_name: value, "entry": float(entry)},
                result.sharpe if eligible else float("-inf"),
            ))

    executed = len(results)
    if not executed:
        raise ValueError("study evaluated nothing")

    # Select the centre of the best smoothed region, not the luckiest point (§8.5).
    surface = Surface.build(surface_points, [spec.param_name, "entry"])
    if not np.isfinite(surface.scores).any():
        raise ValueError(
            f"no configuration reached {spec.min_trades} trades; the grid is "
            f"testing thresholds the signal rarely crosses"
        )
    stability = analyse(surface, radius=1)
    chosen = params.index(stability.peak_params)
    best = results[chosen]

    # NET, not gross: the verdict must pay fees and funding.
    oos = net_matrix[chosen][split:]
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
        best_params=stability.peak_params, gate=gate, oos=oos_result, pbo=pbo.pbo,
        stability={
            "peak_score": stability.peak_score,
            "robust_score": stability.robust_score,
            "is_spike": stability.is_spike,
            "plateau_width": plateau_width(surface, spec.param_name),
        },
        robustness=robustness,
        tier_a_rows=tier_a_rows,
    )
