"""Run the decision sequence under operator-set conditions. Plan.md §20, §15.

This is what the Save button starts. It is a **simulation**: it reads bars,
builds a reference position, applies the market trend, evaluates both arms and puts the
result through the same gate a study goes through. Nothing it does can reach a
venue — there is no mode here to set, and the row it writes is in
`scenario_runs` rather than in `runs`.

**It stops where the sequence stops.** Steps 8 to 13 are execution and need a
strategy that has cleared §15. Nothing has: PREREG-001 and 002 were refuted,
003 and 004 did not reach the resolvable threshold. So a run reports the step it
reached and why it went no further, rather than producing a number that looks
like a decision. The stopping point is derived from the gate each time and not
written down here, because a hardcoded stop would keep saying "the gate" long
after something had passed it.

**The reference is a benchmark, not a strategy.** Buy-and-hold is what §15
measures a strategy *against*, and it is used here because it is the one
position series that exists without a promoted strategy to generate one. Its
Sharpe is not a result. What the run is for is the difference between the two
arms, which is the part the operator changed.
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

#: Where the sequence gets to when the gate does not open. §15 is step 7, and
#: steps 8 onward are execution.
GATE_STEP = 7

#: The steps a scenario run actually executes.
#:
#: Load bars, check power, evaluate, judge. Not a subset chosen for speed: a
#: scenario run asks what a given set of conditions does to a known position,
#: and the search is a different question.
EXECUTED_STEPS = (1, 2, 6, 7)

#: The steps it substitutes a declared reference for.
#:
#: Materialise, score, select — the search. A scenario run does not redo one,
#: which is exactly why it cannot report a strategy result, and saying so here
#: keeps the screen from showing them as having run.
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
    #: Derived from the trend, carried so a reader need not re-derive it.
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
    # Deduplicated on event_time: the archive is written per day and a re-run
    # leaves a second revision of the same bar, which would otherwise be counted
    # as a second observation.
    frame = frame.unique(subset=["event_time"], keep="last").sort("event_time")
    return frame["close"].to_numpy().astype(np.float64)


def _summary(result: BacktestResult) -> dict[str, Any]:
    return {
        "sharpe": round(result.sharpe, 4),
        "total_return": round(result.total_return, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "exposure": round(result.exposure, 4),
        "cost_drag": round(result.cost_drag, 6),
        # Carried because it is the figure that says whether the rest can be
        # read at all. Bars are not observations (§7).
        "effective_n": round(result.effective_n, 2),
    }


def execute(trend: int, sentiment: int,
            archive: Path = ARCHIVE) -> ScenarioRun:
    """One scenario run: both arms, the gate, and where it stopped."""
    close = _bars(archive)
    notes: list[str] = []

    # Steps 3 to 5 — materialise, score, select — are the search, and a scenario
    # run does not redo one. It takes the declared reference position instead,
    # which is why this cannot report a strategy result.
    baseline_position = np.ones(close.size)
    conditioned_position = apply(baseline_position, trend)
    long_multiplier, short_multiplier = exposure_multipliers(trend)

    baseline = backtest(close, baseline_position, timeframe=TIMEFRAME)
    conditioned = backtest(close, conditioned_position, timeframe=TIMEFRAME)

    # Step 2, run after the fact here rather than before: the span is whatever
    # the archive holds, so there is nothing to abandon early, but the answer
    # still has to be reported alongside the result it qualifies.
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

    # Step 7. Derived every time rather than written down, so this stops saying
    # "the gate" as soon as something gets through it.
    # The conditioned arm's own net returns, not the asset's price changes. The
    # first version gated on `diff(close)/close`, which is a statistic about
    # BTCUSDT and not about anything the run did — identical to within 0.04 of
    # Sharpe for a constant long position, and wrong by twelve or more for any
    # position that is not one. The reference being buy-and-hold is exactly what
    # kept it invisible.
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
