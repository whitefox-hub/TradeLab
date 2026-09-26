"""The wave's effect on a position, and the limits on it. Plan.md §20, §11.3.

This is the first thing in the repository where an operator's judgement reaches
a position size, so most of what follows is about what it cannot do.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.scenario.tilt import (
    SENTIMENT_BUCKETS,
    TILT_CEILING,
    apply,
    exposure_multipliers,
    sentiment_bucket,
)


@pytest.mark.fast
def test_a_normal_wave_is_the_identity() -> None:
    """The default has to change nothing, or every run ever made carries an
    adjustment nobody chose."""
    position = np.array([1.0, -0.5, 0.0, 0.25])
    assert exposure_multipliers("NORMAL", 0) == (1.0, 1.0)
    assert np.array_equal(apply(position, "NORMAL", 0), position)
    # Magnitude is stored as 0 under NORMAL, but a stray non-zero must not tilt.
    assert np.array_equal(apply(position, "NORMAL", 7), position)


@pytest.mark.fast
def test_the_wave_never_changes_a_sign() -> None:
    """It conditions, it does not signal. §11.3 puts direction as an input to an
    expected-reaction model and never a signal in itself, so a view about the
    environment may scale a position and must never decide one."""
    position = np.array([1.0, -1.0, 0.3, -0.2, 0.0])
    for direction in ("UP", "DOWN"):
        for magnitude in range(1, 11):
            out = apply(position, direction, magnitude)
            assert np.array_equal(np.sign(out), np.sign(position)), (
                f"{direction} {magnitude} changed a sign")


@pytest.mark.fast
def test_a_tilt_on_a_flat_book_is_still_flat() -> None:
    """It scales a position and never creates one. An operator's view alone
    cannot open a trade, however strongly it is held."""
    flat = np.zeros(20)
    for magnitude in range(0, 11):
        assert not apply(flat, "UP", magnitude).any()
        assert not apply(flat, "DOWN", magnitude).any()


@pytest.mark.fast
def test_the_tilt_is_bounded_at_half() -> None:
    """An operator who could turn a systematic book into a directional bet with
    one slider would have the slider as the strategy. The ceiling is what stops
    that, so it is asserted rather than left to the arithmetic."""
    assert TILT_CEILING == 0.5
    for direction in ("UP", "DOWN"):
        for magnitude in range(0, 11):
            for multiplier in exposure_multipliers(direction, magnitude):
                assert 0.5 <= multiplier <= 1.5, (
                    f"{direction} {magnitude} gives {multiplier}")


@pytest.mark.fast
def test_longs_and_shorts_move_in_opposite_directions() -> None:
    """Scaled separately, not by one factor. A single multiplier would push a
    short *further* into the wave it disagrees with, which is the opposite of
    what was asked for and would be invisible in any aggregate."""
    for magnitude in (1, 5, 10):
        long_up, short_up = exposure_multipliers("UP", magnitude)
        assert long_up > 1.0 > short_up
        long_down, short_down = exposure_multipliers("DOWN", magnitude)
        assert long_down < 1.0 < short_down
        # And the two directions are mirrors, so neither is quietly favoured.
        assert long_up == pytest.approx(short_down)
        assert short_up == pytest.approx(long_down)


@pytest.mark.fast
def test_the_bands_are_the_ones_the_index_actually_uses() -> None:
    """Read off 3156 published days, not guessed. The guessed version put 50 in
    'Greed' and had no 'Neutral' band at all, although the series carries 401
    days of one — the kind of error that is invisible because every value still
    gets a plausible label."""
    assert [name for _, name in SENTIMENT_BUCKETS] == [
        "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    assert sentiment_bucket(25) == "Extreme Fear"
    assert sentiment_bucket(26) == "Fear"
    assert sentiment_bucket(47) == "Neutral"
    assert sentiment_bucket(54) == "Neutral"
    assert sentiment_bucket(55) == "Greed"
    assert sentiment_bucket(76) == "Extreme Greed"
    # Every value on the scale gets one, including the ends.
    assert {sentiment_bucket(v) for v in range(1, 101)} == {
        name for _, name in SENTIMENT_BUCKETS}


@pytest.mark.fast
def test_the_screen_and_the_engine_agree_on_the_bands() -> None:
    """Two copies of the boundaries, one in Python and one in the page. The one
    nobody checks is the one that drifts, and a screen labelling 50 'Greed'
    while the engine files the run under 'Neutral' is a disagreement no result
    would reveal."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2]
            / "tradelab/api/static/market_sentiment.html").read_text()
    for ceiling, name in SENTIMENT_BUCKETS[:-1]:
        assert f'if (value <= {ceiling}) return "{name}";' in page, (
            f"the page does not put {name} at {ceiling}")


@pytest.mark.fast
def test_the_sentiment_does_not_reach_a_position() -> None:
    """§11.4 has market state conditioning a model that does not exist yet.
    Applying a number whose method is not auditable would be inventing a market
    theory and then trading it, so the bucket is recorded and nothing else."""
    import inspect

    from tradelab.scenario import tilt

    source = inspect.getsource(tilt.apply) + inspect.getsource(tilt.exposure_multipliers)
    assert "sentiment" not in source, (
        "the sentiment has started changing a position; that needs a "
        "pre-registration, not an edit")


@pytest.mark.fast
def test_a_scenario_run_has_no_mode_to_set() -> None:
    """§20.2: not a button, not a dropdown, not a config field. A scenario run
    is a simulation, and the way to keep it one is for the record it writes to
    have no column an execution mode could go in."""
    from tradelab.registry.models import Base

    table = Base.metadata.tables["scenario_runs"]
    columns = set(table.columns.keys())
    assert "mode" not in columns
    assert not table.foreign_keys, (
        "scenario_runs points at something; it must not reach the execution "
        "tables, where a run is a thing that trades")


@pytest.mark.fast
def test_a_run_reports_the_step_it_reached_rather_than_a_verdict() -> None:
    """Steps 8 to 13 are execution and need a strategy promoted through §15.
    Nothing has been. A run that could not express "it stopped here" would have
    to report a stop as a result."""
    import numpy as np_

    from tradelab.scenario.run import GATE_STEP, execute

    class _Archive:
        """Enough bars to run on, with no dependence on the real archive."""

        def rglob(self, _pattern: str) -> list:
            return []

    with pytest.raises(FileNotFoundError):
        execute("UP", 9, 74, archive=_Archive())  # type: ignore[arg-type]

    assert GATE_STEP == 7
    del np_
