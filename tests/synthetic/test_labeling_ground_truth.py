"""The labeller against data whose answer is known. Plan.md §16.4, §7."""

from __future__ import annotations

import polars as pl
import pytest

from tradelab.labeling.barriers import Barrier, BarrierSpec, triple_barrier
from tradelab.validation.synthetic import make_noise


@pytest.fixture(scope="module")
def noise() -> pl.DataFrame:
    return make_noise(n=20_000, seed=20260101)


@pytest.mark.slow
def test_symmetric_barriers_are_balanced_on_driftless_data(noise: pl.DataFrame) -> None:
    """With equal barriers and no drift, stops and profits must be balanced.

    This is the labeller's own ground truth. Real market fixtures cannot test it,
    because any imbalance there is indistinguishable from the market having
    moved; on driftless synthetic data an imbalance beyond sampling error can
    only be a bug.
    """
    out = triple_barrier(noise, BarrierSpec(profit_mult=1.0, stop_mult=1.0,
                                            max_holding=60, vol_period=100))
    counts = {r["label"]: r["len"] for r in out.group_by("label").len().iter_rows(named=True)}
    stops, profits = counts.get(Barrier.STOP, 0), counts.get(Barrier.PROFIT, 0)
    assert stops + profits > 1000, "too few resolved labels to judge balance"

    share = profits / (stops + profits)
    # The tie-break is deliberately pessimistic — a bar touching both barriers is
    # scored as a stop — so a modest tilt toward stops is expected and correct.
    # A large one is not.
    assert 0.40 <= share <= 0.52, (
        f"profit share {share:.3f} on driftless data. Symmetric barriers should be "
        f"near balanced, with only the conservative tie-break tilting it down."
    )


@pytest.mark.slow
def test_wider_profit_barrier_is_hit_less_often(noise: pl.DataFrame) -> None:
    """Monotonicity: moving the profit barrier further away can only reduce hits."""
    shares = []
    for mult in (1.0, 1.5, 2.5):
        out = triple_barrier(noise, BarrierSpec(profit_mult=mult, stop_mult=1.0,
                                                max_holding=60, vol_period=100))
        profits = out.filter(pl.col("label") == Barrier.PROFIT).height
        shares.append(profits / out.height)
    assert shares[0] > shares[1] > shares[2], f"not monotonic: {shares}"


@pytest.mark.slow
def test_horizon_is_reachable(noise: pl.DataFrame) -> None:
    """Barriers scale with sqrt(holding), so the horizon must actually be reached.

    Without that scaling the barriers sit at one per-bar sigma and every label
    resolves within a few bars — which made a five-day trade resolve in three
    minutes before this was fixed.
    """
    out = triple_barrier(noise, BarrierSpec(profit_mult=2.0, stop_mult=2.0,
                                            max_holding=120, vol_period=100))
    at_horizon = out.filter(pl.col("label") == Barrier.HORIZON).height / out.height
    assert at_horizon > 0.05, f"horizon barrier effectively unreachable ({at_horizon:.3f})"
    assert out["bars_held"].mean() > 10


@pytest.mark.slow
def test_determination_time_is_never_before_entry(noise: pl.DataFrame) -> None:
    """`determined_at` is what the purge covers, so it must be sane (§8.2)."""
    out = triple_barrier(noise, BarrierSpec(max_holding=60, vol_period=100))
    # Timezone must survive the labeller. It did not at first: a numpy round trip
    # stripped it, producing a naive timestamp that would compare wrongly against
    # the tz-aware archive without ever raising (§5).
    assert out.schema["determined_at"].time_zone == "UTC"
    assert (out["determined_at"] >= out["event_time"]).all()
    assert out["bars_held"].min() >= 1


@pytest.mark.slow
def test_incomplete_events_at_the_end_are_dropped(noise: pl.DataFrame) -> None:
    """A label whose horizon runs past the data has no outcome.

    Keeping it as HORIZON would invent a resolution that never happened — the
    label equivalent of survivorship bias.
    """
    spec = BarrierSpec(max_holding=200, vol_period=100)
    out = triple_barrier(noise, spec)

    # Not a simple height bound: an event near the end that settled early DOES
    # have an outcome and must be kept. What must hold is that every surviving
    # label was actually observable within the data.
    assert out["bars_held"].min() >= 1
    last = noise["event_time"].max()
    assert (out["determined_at"] <= last).all()
    # And the tail that could neither settle nor reach its horizon is gone.
    assert out["event_time"].max() < last
