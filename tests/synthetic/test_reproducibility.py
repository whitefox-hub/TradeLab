"""Identity and reproducibility. Plan.md §5.1, §16.1.

`config_hash` decides whether two experiments are the same experiment. Too coarse
and a changed study silently reuses a stale record; too fine and every re-run
recomputes what it already had. Both failures are quiet.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.features.materialise import feature_set_hash
from tradelab.orchestration.study import StudySpec, run_study
from tradelab.validation.synthetic import make_noise


def _spec(**overrides) -> StudySpec:
    base = {
        "name": "repro", "params": (10, 20), "entries": (58.0, 62.0),
        "min_trades": 5, "timeframe": "4h", "min_detectable_sharpe": 0.05,
        # These verify identity and cost handling, not power. The §8.8 check is
        # waived explicitly rather than by accident, which is the distinction it
        # was added to enforce.
        "require_power": False,
    }
    return StudySpec(**{**base, **overrides})


@pytest.mark.medium
def test_the_same_study_twice_gives_the_same_answer() -> None:
    """Determinism at the study level, not just the golden run (§9.4)."""
    bars = make_noise(n=6_000, seed=51)
    first, second = run_study(bars, _spec()), run_study(bars, _spec())

    assert first.best_params == second.best_params
    assert first.verdict == second.verdict
    assert first.executed_trials == second.executed_trials
    np.testing.assert_allclose(
        first.gate.metrics["sharpe_annualised"],
        second.gate.metrics["sharpe_annualised"], rtol=1e-12,
    )


@pytest.mark.fast
def test_feature_set_identity_covers_parameters_and_code() -> None:
    """A stale cached matrix served for changed code is invisible."""
    base = [("rsi", {"period": 14, "source": "close"})]
    assert feature_set_hash(base) == feature_set_hash(base)
    assert feature_set_hash(base) != feature_set_hash(
        [("rsi", {"period": 15, "source": "close"})])
    assert feature_set_hash(base) != feature_set_hash(
        [("ema", {"span": 14})])


@pytest.mark.medium
def test_a_changed_grid_is_a_different_study() -> None:
    """Widening the search changes the trial count, and the bar with it.

    Reusing the previous record would quote a verdict reached under a lower bar,
    which is the specific way §8.3 gets circumvented by accident.
    """
    bars = make_noise(n=6_000, seed=53)
    narrow = run_study(bars, _spec(params=(10, 20)))
    wide = run_study(bars, _spec(params=(10, 20, 30, 40)))

    from tradelab.validation.deflated import required_sharpe

    assert wide.executed_trials > narrow.executed_trials
    assert wide.gate.metrics["trials_considered"] == wide.executed_trials
    assert narrow.gate.metrics["trials_considered"] == narrow.executed_trials

    # Asserted against the arithmetic rather than against a gate metric that is
    # only written when the gate reaches its late path: a test that depends on
    # which branch ran is testing the branch, not the invariant.
    obs = 1_000
    assert required_sharpe(wide.executed_trials, obs) > required_sharpe(
        narrow.executed_trials, obs
    ), "a wider search must raise the bar its survivor has to clear"


@pytest.mark.medium
def test_costs_are_part_of_the_question_not_a_postprocessing_step() -> None:
    """The same data under two cost models must give different verdicts.

    If costs were applied after selection, the chosen configuration would be
    identical and only its reported figure would move.
    """
    from tradelab.engines.tier_a.costs import PESSIMISTIC

    bars = make_noise(n=8_000, seed=57)
    cheap = run_study(bars, _spec(params=(8, 14, 20), entries=(50.0, 54.0, 58.0)))
    dear = run_study(
        bars, _spec(params=(8, 14, 20), entries=(50.0, 54.0, 58.0), costs=PESSIMISTIC))

    assert cheap.gate.metrics["sharpe_annualised"] > dear.gate.metrics["sharpe_annualised"]
