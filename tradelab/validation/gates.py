"""Promotion gates. Plan.md §8, §15, §20.3.

A verdict is `pass`, `fail` or `inconclusive`, and the third is not a softer
`fail`. It means the data could not resolve the question — and reporting that
honestly is the behaviour the whole system exists to produce, because the
alternative is a confident answer the sample never supported.

Thresholds come from a `gate_config` row, never from code. Every verdict records
which one judged it, so a bar that was lowered travels with the results issued
under it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from tradelab.validation.deflated import (
    SharpeStats,
    deflated_sharpe,
    expected_max_sharpe,
    min_track_record_length,
    required_sharpe,
)
from tradelab.validation.pbo import PBOResult


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


#: Defaults matching §15's `validating → candidate` row. Persisted to
#: `gate_configs` at startup; changing them is an audited event with a mandatory
#: rationale and a 24h cooling-off before any approval may rely on the change.
DEFAULT_THRESHOLDS: dict = {
    "dsr_min": 0.95,
    "pbo_max": 0.30,
    "min_oos_trades": 100,
    "min_baseline_percentile": 0.95,
    "cost_multiple_survived": 2.0,
    "min_regimes_positive": 2,
}


@dataclass
class GateEvaluation:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS


def evaluate(
    stats: SharpeStats, trials: int, n_oos_trades: int,
    pbo: PBOResult | None = None,
    baseline_percentiles: dict[str, float] | None = None,
    thresholds: dict | None = None,
    bars_per_year: float = 8_760,
    min_detectable_sharpe: float | None = None,
) -> GateEvaluation:
    """Judge one candidate against the configured bar.

    `min_detectable_sharpe` is the per-observation effect the study declared it
    was looking for, before searching (§8.8). Supplying it lets the gate separate
    "this does not work" from "this search could never have shown that it did".
    """
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    reasons: list[str] = []
    # Recorded before any early exit. The trial counter is monotonic and feeds
    # every later verdict (§8.3), so a study that stops early still spent its
    # configurations — and a record that omits them makes the count
    # unauditable precisely where it is least obvious that any were spent.
    metrics: dict = {"trials_considered": trials, "n_oos_trades": n_oos_trades}

    # Sample size first. Below the minimum there is nothing to judge, and a
    # verdict of `fail` would imply the evidence pointed the other way.
    if n_oos_trades < t["min_oos_trades"]:
        return GateEvaluation(
            Verdict.INCONCLUSIVE,
            [f"{n_oos_trades} out-of-sample trades, below the {t['min_oos_trades']} minimum"],
            metrics,
        )

    dsr = deflated_sharpe(stats, trials=trials)
    required_obs = min_track_record_length(stats, trials=trials)
    variance = (1 + 0.5 * stats.sharpe**2) / max(stats.n_obs - 1, 1)
    benchmark = expected_max_sharpe(trials, variance)
    metrics |= {
        "deflated_sharpe": dsr,
        "min_trl_obs": required_obs,
        "n_obs": stats.n_obs,
        "sharpe_annualised": stats.sharpe * float(np.sqrt(bars_per_year)),
        "null_benchmark_sharpe": benchmark,
    }

    # The smallest effect this sample and trial count can resolve at all (§8.8).
    detectable_floor = required_sharpe(trials, stats.n_obs)
    metrics["detectable_floor_sharpe"] = detectable_floor
    underpowered = (
        min_detectable_sharpe is not None and min_detectable_sharpe < detectable_floor
    )

    # Two very different situations that both make MinTRL infinite, and
    # collapsing them would cost the system its most useful distinction.
    if stats.sharpe <= benchmark:
        # Performance at or below what this many trials produce by chance. What
        # that means depends entirely on whether the search could have seen
        # anything: a negative result from an underpowered search says nothing
        # about the hypothesis, only about the sample.
        #
        # The check qualifies a negative result and only a negative one. A study
        # that declared too small a target but found something clearly resolvable
        # made a planning error, not a discovery to be discarded.
        if underpowered:
            return GateEvaluation(
                Verdict.INCONCLUSIVE,
                [
                    f"underpowered: the study targeted a Sharpe of "
                    f"{min_detectable_sharpe:.4f} but {stats.n_obs:,} observations at "
                    f"{trials:,} trials can only resolve {detectable_floor:.4f}. The "
                    f"negative result reflects the sample, not the hypothesis."
                ],
                metrics,
            )
        return GateEvaluation(
            Verdict.FAIL,
            [
                f"Sharpe {stats.sharpe:.4f} does not exceed the {benchmark:.4f} that "
                f"{trials:,} trials produce by chance"
            ],
            metrics,
        )

    if required_obs > stats.n_obs:
        # A positive signal the sample is too small to confirm. The question is
        # open, and saying so is the point (§8.8).
        return GateEvaluation(
            Verdict.INCONCLUSIVE,
            [
                f"minimum track record is {required_obs:,.0f} observations against "
                f"{stats.n_obs:,} available at {trials:,} trials"
            ],
            metrics,
        )

    if dsr < t["dsr_min"]:
        reasons.append(
            f"deflated Sharpe {dsr:.3f} below {t['dsr_min']} after {trials:,} trials"
        )

    if pbo is not None:
        metrics["pbo"] = pbo.pbo
        metrics["turnover_correlation"] = pbo.turnover_correlation
        if pbo.measures_turnover_not_alpha:
            # A low PBO here is about trading frequency, not skill (§8.3), so it
            # cannot be read as evidence either way.
            reasons.append(
                f"PBO is turnover-driven (correlation {pbo.turnover_correlation:.2f}); "
                f"it carries no information about selection quality here"
            )
        elif pbo.pbo > t["pbo_max"]:
            reasons.append(f"PBO {pbo.pbo:.3f} above {t['pbo_max']}")

    for name, percentile in (baseline_percentiles or {}).items():
        metrics[f"baseline_{name}"] = percentile
        if percentile < t["min_baseline_percentile"]:
            reasons.append(
                f"does not beat the {name} null: {percentile:.1%} percentile, "
                f"below {t['min_baseline_percentile']:.0%}"
            )

    return GateEvaluation(Verdict.FAIL if reasons else Verdict.PASS, reasons, metrics)
