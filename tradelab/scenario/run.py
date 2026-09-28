"""Run the decision sequence under operator-set conditions. Plan.md §20, §15.

A simulation only: no execution mode; it writes to `scenario_runs`, never `runs`.
Steps 8+ need a §15-promoted strategy, so a run reports the step reached, derived from the
gate each time. The reference (buy-and-hold) is a benchmark; the output is the arm difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from tradelab.engines.tier_a.engine import BacktestResult, net_returns
from tradelab.engines.tier_a.engine import run as backtest
from tradelab.scenario.tilt import (
    NEUTRAL,
    apply,
    direction,
    exposure_multipliers,
    sentiment_bucket,
)
from tradelab.validation.deflated import sharpe_stats
from tradelab.validation.gates import evaluate
from tradelab.validation.power import assess

#: §15 is step 7; steps 8 onward are execution.
GATE_STEP = 7

#: Steps executed: load bars, check power, evaluate, judge.
EXECUTED_STEPS = (1, 2, 6, 7)

#: The search steps (materialise, score, select), replaced by the declared reference.
SUBSTITUTED_STEPS = (3, 4, 5)
ARCHIVE = Path("data/parquet/ohlcv")
REFERENCE = "buy-and-hold BTCUSDT 1m"
TIMEFRAME = "1m"
BARS_PER_YEAR = 525_600


@dataclass(frozen=True, slots=True)
class ScenarioRun:
    reference: str
    bars: int
    long_multiplier: float
    short_multiplier: float
    sentiment_bucket: str
    #: Derived from the trend.
    trend_direction: str
    baseline: dict[str, Any]
    conditioned: dict[str, Any]
    reached_step: int
    stopped_because: str
    notes: list[str] = field(default_factory=list)


def _bars(archive: Path = ARCHIVE) -> np.ndarray:
    files = sorted(archive.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no bars under {archive}")
    frame = pl.concat([pl.read_parquet(f) for f in files]).sort("event_time")
    # Dedup on event_time: a re-run leaves a second revision of the same bar.
    frame = frame.unique(subset=["event_time"], keep="last").sort("event_time")
    return frame["close"].to_numpy().astype(np.float64)


def _summary(result: BacktestResult) -> dict[str, Any]:
    return {
        "sharpe": round(result.sharpe, 4),
        "total_return": round(result.total_return, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "exposure": round(result.exposure, 4),
        "cost_drag": round(result.cost_drag, 6),
        # Bars are not observations (§7).
        "effective_n": round(result.effective_n, 2),
    }


def execute(trend: int, sentiment: int,
            archive: Path = ARCHIVE) -> ScenarioRun:
    """One scenario run: both arms, the gate, and where it stopped."""
    close = _bars(archive)
    notes: list[str] = []

    # Steps 3-5 (the search) are replaced by the declared reference position.
    baseline_position = np.ones(close.size)
    conditioned_position = apply(baseline_position, trend)
    long_multiplier, short_multiplier = exposure_multipliers(trend)

    baseline = backtest(close, baseline_position, timeframe=TIMEFRAME)
    conditioned = backtest(close, conditioned_position, timeframe=TIMEFRAME)

    # Step 2, after the fact: the span is fixed, but power is reported with the result.
    power = assess(target_sharpe_annual=0.5, trials=1,
                   effective_n_available=float(conditioned.effective_n),
                   bars_per_year=BARS_PER_YEAR)
    if conditioned.effective_n < 30:
        notes.append(
            f"{conditioned.effective_n:.0f} effective observations from "
            f"{close.size} bars. A position held throughout produces one bet, "
            "not one per bar, so neither Sharpe below is a measurement of "
            "anything (§7).")
    notes.append(
        f"at one trial this span resolves a Sharpe of "
        f"{power.resolvable_sharpe_annual:.2f}; anything under it is invisible "
        "however it looks (§0.4a).")

    # Step 7, gated on the conditioned arm's own net returns, not the asset's price changes.
    stats = sharpe_stats(net_returns(close, conditioned_position),
                         effective_n=float(conditioned.effective_n))
    gate = evaluate(stats, trials=1, n_oos_trades=int(conditioned.n_trades or 1),
                    bars_per_year=BARS_PER_YEAR, min_detectable_sharpe=0.5)

    if gate.verdict == "pass":
        reached, because = GATE_STEP + 1, (
            "the gate opened, and execution needs a strategy promoted through "
            "§15 rather than a benchmark")
    else:
        reached = GATE_STEP
        because = (f"the gate returned {gate.verdict.upper()}: "
                   + "; ".join(gate.reasons[:2]) if gate.reasons
                   else f"the gate returned {gate.verdict.upper()}")

    if trend == NEUTRAL:
        notes.append(
            f"the market trend is {NEUTRAL}, which is neutral, so both arms are "
            "the same run and the difference below is zero by construction.")

    return ScenarioRun(
        reference=REFERENCE,
        bars=int(close.size),
        long_multiplier=long_multiplier,
        short_multiplier=short_multiplier,
        sentiment_bucket=sentiment_bucket(sentiment),
        trend_direction=direction(trend),
        baseline=_summary(baseline),
        conditioned=_summary(conditioned),
        reached_step=reached,
        stopped_because=because,
        notes=notes,
    )
