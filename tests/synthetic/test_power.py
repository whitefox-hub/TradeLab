"""Power as a precondition. Plan.md §8.8.

§8.8 requires the minimum detectable effect to be fixed before searching. It was
implemented as a diagnosis afterwards, which is not the same thing: the gate
reported "underpowered" once the trials had been spent, and the counter is
monotonic, so a study that could never have concluded still raised the bar for
every study after it.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.orchestration.study import StudySpec, run_study
from tradelab.validation.power import (
    UnderpoweredStudy,
    assess,
    effective_n_needed,
    pilot_effective_n,
    resolvable_sharpe,
)
from tradelab.validation.synthetic import make_noise


@pytest.mark.fast
def test_more_trials_need_more_observations() -> None:
    """Both levers move the same way, which is why neither alone is a fix."""
    needed = [effective_n_needed(1.0, t) for t in (9, 54, 500)]
    assert needed == sorted(needed)


@pytest.mark.fast
def test_a_realistic_target_needs_tens_of_thousands_of_observations() -> None:
    """The arithmetic that reframed the campaign.

    §0.3 expects an out-of-sample Sharpe of 0.7-1.3 from a real edge. Confirming
    one at 54 trials takes roughly 34,000 effective observations — which the
    threshold studies reached at low thresholds and the cross-sectional studies
    missed by more than an order of magnitude.
    """
    needed = effective_n_needed(1.0, trials=54)
    assert 20_000 < needed < 60_000


@pytest.mark.fast
def test_a_small_sample_can_only_confirm_an_implausible_edge() -> None:
    """2,500 observations is not a hypothesis test, it is a formality."""
    assert resolvable_sharpe(2_500, trials=36) > 3.0
    assert resolvable_sharpe(60_000, trials=18) < 1.0


@pytest.mark.fast
def test_past_designs_are_judged_correctly_in_hindsight() -> None:
    """The four designs actually run, sized after the fact."""
    assert assess(1.0, 18, 59_207).adequate
    assert not assess(1.0, 18, 4_039).adequate
    assert not assess(1.0, 36, 2_100).adequate


@pytest.mark.fast
def test_the_pilot_reads_structure_and_not_performance() -> None:
    """A pilot whose Sharpe is looked at is a trial and would have to be counted.

    This one takes only prices and positions and returns a count, so there is
    nothing in its output that could be selected on.
    """
    close = np.linspace(100.0, 120.0, 1_000)
    position = np.zeros(1_000)
    position[100:300] = 1.0
    position[500:700] = 1.0

    count = pilot_effective_n(close, position, oos_fraction=1.0)
    assert count > 0
    assert count == pilot_effective_n(np.linspace(200.0, 50.0, 1_000), position,
                                      oos_fraction=1.0)


@pytest.mark.medium
def test_an_underpowered_study_is_refused_before_the_grid_runs() -> None:
    """Refused, not diagnosed. The distinction is the spent trial count."""
    bars = make_noise(n=6_000, seed=61)
    spec = StudySpec(name="p", params=(10, 20, 30), entries=(58.0, 62.0, 66.0),
                     min_trades=5, timeframe="4h", target_sharpe_annual=1.0)
    with pytest.raises(UnderpoweredStudy, match="underpowered"):
        run_study(bars, spec)


@pytest.mark.medium
def test_the_check_can_be_waived_deliberately() -> None:
    """Exploratory runs are legitimate; they must be an explicit choice."""
    bars = make_noise(n=6_000, seed=61)
    spec = StudySpec(name="p", params=(10, 20, 30), entries=(58.0, 62.0, 66.0),
                     min_trades=5, timeframe="4h", require_power=False)
    assert run_study(bars, spec).executed_trials == 9
