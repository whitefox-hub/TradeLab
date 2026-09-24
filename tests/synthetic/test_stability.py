"""Parameter surface stability. Plan.md §8.5, §15."""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.engines.tier_a.engine import run, threshold_position
from tradelab.features import momentum  # noqa: F401
from tradelab.features.materialise import materialise
from tradelab.validation.stability import Surface, analyse, plateau_width
from tradelab.validation.synthetic import make_noise

PERIODS = list(range(5, 31))


@pytest.mark.fast
def test_a_spike_and_a_plateau_with_the_same_peak_are_distinguished() -> None:
    """The example from §8.5, made mechanical.

    Both surfaces peak at exactly 3.0. The peak cannot tell them apart; the
    neighbourhood can, which is why the neighbourhood is what gets promoted.
    """
    spike = Surface.build(
        [({"period": p}, 3.0 if p == 14 else 0.2) for p in PERIODS], ["period"])
    plateau = Surface.build(
        [({"period": p}, 3.0 if 12 <= p <= 18 else 0.2) for p in PERIODS], ["period"])

    spiky, flat = analyse(spike, radius=2), analyse(plateau, radius=2)
    assert spiky.peak_score == flat.peak_score == 3.0
    assert spiky.is_spike
    assert not flat.is_spike
    assert flat.robust_score > spiky.robust_score * 10


@pytest.mark.fast
def test_the_robust_score_is_what_survives_selection_bias() -> None:
    """The maximum of a noisy surface is biased upward by selection.

    On a flat true surface the sampled peak is an outlier, so peak-based scores
    always flatter. The neighbourhood median needs no correction: it is not the
    quantity that was maximised.
    """
    rng = np.random.default_rng(4)
    flat_truth = Surface.build(
        [({"period": p}, float(rng.normal(1.0, 0.3))) for p in PERIODS], ["period"])
    result = analyse(flat_truth, radius=2)

    assert result.peak_score > result.robust_score, "peak should be the outlier"
    assert abs(result.robust_score - 1.0) < abs(result.peak_score - 1.0), (
        "the robust score should sit closer to the true value than the peak does"
    )


@pytest.mark.fast
def test_plateau_width_answers_the_rsi_question() -> None:
    """A width of one is a spike. §8.5's own example: 12-18 is seven."""
    spike = Surface.build(
        [({"period": p}, 3.0 if p == 14 else 0.2) for p in PERIODS], ["period"])
    plateau = Surface.build(
        [({"period": p}, 3.0 if 12 <= p <= 18 else 0.2) for p in PERIODS], ["period"])
    assert plateau_width(spike, "period") == 1
    assert plateau_width(plateau, "period") == 7


@pytest.mark.fast
def test_two_dimensional_surfaces_are_handled() -> None:
    """Real parameter spaces have more than one axis."""
    points = [
        ({"fast": f, "slow": s}, 2.0 if (10 <= f <= 14 and 40 <= s <= 50) else 0.1)
        for f in range(5, 20) for s in range(30, 60, 2)
    ]
    result = analyse(Surface.build(points, ["fast", "slow"]), radius=1)
    assert not result.is_spike
    assert result.n_neighbours >= 3


@pytest.mark.slow
def test_searching_noise_produces_spikes_not_plateaus() -> None:
    """The reason this metric is a gate.

    A grid over data with no edge yields a best configuration that looks strong
    and whose neighbours do not agree with it. That disagreement is the signature
    of having selected noise, and it is visible without knowing the data was
    noise.
    """
    bars = make_noise(n=20_000, seed=31)
    features = materialise(
        bars, [("rsi", {"period": p, "source": "close"}) for p in PERIODS])
    close = bars["close"].to_numpy().astype(np.float64)

    points = []
    for period in PERIODS:
        signal = features[f"rsi__period{period}_sourceclose"].to_numpy().astype(np.float64)
        for entry in np.arange(55.0, 75.0, 2.5):
            result = run(close, threshold_position(signal, entry), BINANCE_RETAIL, "1h")
            points.append(({"period": period, "entry": float(entry)}, result.gross_sharpe))

    stability = analyse(Surface.build(points, ["period", "entry"]), radius=1)
    assert stability.robust_score < stability.peak_score, (
        "on noise the peak must not be supported by its neighbourhood"
    )
    assert stability.peak_score > stability.surface_median, "sanity: the peak is the peak"
