"""The 1-10 rating control. Plan.md §20.5, §11.3.

The scale is a control, not a unit. What is stored is sigma of pre-event
volatility, because §11.3 normalises every measured reaction to sigma — so a
rating in sigma can be checked against what happened, and the annotator's
calibration becomes knowable. A rating on an arbitrary scale cannot be scored
against anything, and an input nobody can score is the one nobody corrects.
"""

from __future__ import annotations

import pytest

from tradelab.news.strength import (
    MAX_STRENGTH,
    MIN_STRENGTH,
    SIGMA_BY_STRENGTH,
    describe,
    to_sigma,
)


@pytest.mark.fast
def test_direction_carries_the_sign_so_no_negative_is_typed() -> None:
    assert to_sigma(6, -1) == -3.0
    assert to_sigma(6, +1) == 3.0
    assert to_sigma(6, 0) == 0.0


@pytest.mark.fast
def test_the_scale_stays_inside_plausible_reaction_sizes() -> None:
    """A reaction under one sigma is ordinary and five is a day people remember,
    so ten steps over that range put the common readings in the lower half —
    which is where most events belong."""
    assert SIGMA_BY_STRENGTH[MIN_STRENGTH] == 0.5
    assert SIGMA_BY_STRENGTH[MAX_STRENGTH] == 5.0
    values = [SIGMA_BY_STRENGTH[n] for n in range(MIN_STRENGTH, MAX_STRENGTH + 1)]
    assert values == sorted(values), "the scale is not monotonic"


@pytest.mark.fast
def test_every_step_states_the_sigma_it_means() -> None:
    """The dialog shows the unit rather than hiding it: the operator should
    learn that seven means three and a half sigma, not be shielded from the fact
    that sigma is what is kept."""
    for n in SIGMA_BY_STRENGTH:
        assert "sigma" in describe(n)
        assert str(n) in describe(n)


@pytest.mark.fast
@pytest.mark.parametrize("bad", [0, 11, -1, 3.5, "high", None])
def test_a_strength_outside_the_scale_is_refused(bad) -> None:
    with pytest.raises(ValueError, match="strength must be"):
        to_sigma(bad, 1)


@pytest.mark.fast
def test_an_unknown_direction_is_refused() -> None:
    with pytest.raises(ValueError, match="direction must be"):
        to_sigma(5, 2)
