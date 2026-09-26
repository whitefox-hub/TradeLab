"""News conditioning a decision. Plan.md §11.7, §14.1.

What events earn here is a worse prior, not a direction. §11.7 is explicit that
sign is an input to an expected-reaction model and never a signal in itself, so
the context damps size and produces no entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradelab.execution.types import AccountState, Instrument, Order, Side
from tradelab.news.context import (
    ANTICIPATION_MULTIPLIER,
    REGIME_SIZE_MULTIPLIER,
    EventContext,
)
from tradelab.risk.limits import Breach, RiskEngine, RiskLimits

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
BTC = Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal(50))
PRICE = Decimal(80_000)


def _context(**kw) -> EventContext:
    base = {"asset": "BTCUSDT", "as_of": NOW, "regime_shifts": 0,
            "pending_schedules": 0, "active_shocks": 0, "net_direction": 0}
    return EventContext(**{**base, **kw})


def _engine(context: EventContext | None) -> RiskEngine:
    account = AccountState(equity=Decimal(100_000), peak_equity=Decimal(100_000),
                           day_start_equity=Decimal(100_000))
    return RiskEngine(
        RiskLimits(max_position_pct_equity=Decimal(1)), account,
        event_context=(lambda asset, at: context) if context else None,
    )


@pytest.mark.fast
def test_a_quiet_asset_is_not_damped() -> None:
    """The control. Without it every test below passes by damping always."""
    decision = _engine(_context()).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.05")), PRICE, BTC, {}, NOW)
    assert decision.allowed
    assert not decision.damped


@pytest.mark.fast
def test_a_live_regime_shift_halves_the_position() -> None:
    """Not a veto. Refusing to trade because a war is on sits out the whole
    period, and §11.7's point is that a regime shift replaces the baseline
    rather than removing the market — the prior is worse, not absent."""
    assert REGIME_SIZE_MULTIPLIER == 0.5
    decision = _engine(_context(regime_shifts=1)).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.05")), PRICE, BTC, {}, NOW)
    assert decision.allowed
    assert decision.damped
    assert "50%" in decision.detail
    assert "baseline" in decision.detail


@pytest.mark.fast
def test_the_anticipation_window_damps_too() -> None:
    """§11.6: for a scheduled event the tradeable object is the approach, so a
    position opened into one is a bet nobody sized."""
    context = _context(pending_schedules=1)
    assert context.size_multiplier == pytest.approx(ANTICIPATION_MULTIPLIER)
    decision = _engine(context).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.05")), PRICE, BTC, {}, NOW)
    assert decision.damped


@pytest.mark.fast
def test_the_two_conditions_compound() -> None:
    """An asset under a regime shift *and* days from a scheduled event is a
    worse prior than either alone."""
    both = _context(regime_shifts=1, pending_schedules=1)
    assert both.size_multiplier == pytest.approx(
        REGIME_SIZE_MULTIPLIER * ANTICIPATION_MULTIPLIER)
    assert len(both.reasons) == 2


@pytest.mark.fast
def test_damping_happens_before_the_minimum_viable_check() -> None:
    """Otherwise an order is approved at a size it will never be sent at."""
    # 0.002 BTC is 160, above min_viable of 80. Halved it is 80, still at the
    # boundary; quartered by both conditions it is 56 and must be refused.
    decision = _engine(_context(regime_shifts=1, pending_schedules=1)).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.002")), PRICE, BTC, {}, NOW)
    assert decision.breach is Breach.BELOW_MIN_VIABLE
    # And the refusal says news caused it, because "below min_viable" on an
    # order the strategy sized correctly is otherwise unaccountable.
    assert "event damping" in decision.detail


@pytest.mark.fast
def test_an_allowed_but_damped_decision_is_not_read_as_a_refusal() -> None:
    """It is the one case where `allowed` and `breach` are both set, so it has
    a name rather than being inferred."""
    decision = _engine(_context(regime_shifts=1)).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.05")), PRICE, BTC, {}, NOW)
    assert decision.allowed and decision.breach is Breach.EVENT_CONTEXT
    assert decision.damped
    assert not decision.halts


@pytest.mark.fast
def test_direction_is_reported_and_not_acted_on() -> None:
    """The residual is what is tradeable; the sign is what everyone has priced.
    A context that entered positions on direction would be trading the priced
    half."""
    bullish = _context(net_direction=1, regime_shifts=0)
    bearish = _context(net_direction=-1, regime_shifts=0)
    assert bullish.size_multiplier == bearish.size_multiplier == 1.0


@pytest.mark.fast
def test_an_engine_with_no_context_behaves_exactly_as_before() -> None:
    """Backtests predating the event corpus must not change behaviour."""
    decision = _engine(None).check(
        Order("BTCUSDT", Side.BUY, Decimal("0.05")), PRICE, BTC, {}, NOW)
    assert decision.allowed and decision.breach is None


@pytest.mark.fast
def test_the_lookup_is_point_in_time() -> None:
    """An event is visible when a subscriber could have seen it, not when it
    happened — and reading without a cutoff lets a backtest act on tomorrow's
    news, which looks entirely reasonable in the output."""
    from pathlib import Path

    import tradelab.news.context as module

    source = Path(module.__file__).read_text()
    assert "e.first_seen_at <= :as_of" in source
    assert "s.available_at <= :as_of" in source


@pytest.mark.fast
def test_a_reduce_only_order_is_never_damped() -> None:
    """Cutting an exit because news is bad is the opposite of what news should
    do to it."""
    decision = _engine(_context(regime_shifts=1, pending_schedules=1)).check(
        Order("BTCUSDT", Side.SELL, Decimal("0.05"), reduce_only=True),
        PRICE, BTC, {}, NOW)
    assert decision.allowed
    assert not decision.damped


@pytest.mark.fast
def test_the_reason_reaches_the_audit_trail(_unused=None) -> None:
    """A size that changed for a reason nobody recorded is one nobody can
    review."""
    context = _context(regime_shifts=2, pending_schedules=1)
    assert all(isinstance(reason, str) and reason for reason in context.reasons)
    assert "regime shift" in context.reasons[0]
    assert "anticipation" in context.reasons[1]
