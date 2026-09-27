"""Fire every limit at its threshold. Plan.md §16.8, §14.1.

§16.8 is unambiguous: *each rule in `risk_limit_configs` has a test that fires
it at its threshold. A limit without a test that has seen it fire is a limit
that has never run.*

Three of the sixteen had never been seen to fire when this was written —
`max_open_orders`, `max_position_notional` and `min_position_pct_equity`. A
limit nobody has watched refuse is indistinguishable, from the outside, from one
that silently allows everything, and the first time anyone finds out is the
first time it was needed.

Each drill builds the smallest state that should breach exactly one limit, and
asserts both that the order is refused **and which limit refused it**. The second
half matters more than the first: an order stopped by the wrong rule is a rule
that has not been tested, hiding behind one that has.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradelab.execution.types import (
    AccountState,
    Instrument,
    Order,
    OrderType,
    Position,
    Side,
)
from tradelab.risk.limits import Breach, RiskEngine, RiskLimits

AT = datetime(2026, 1, 1, tzinfo=UTC)
PRICE = Decimal(1000)
INSTRUMENT = Instrument("AAAUSDT", Decimal("0.01"), Decimal("0.001"), Decimal(1))

#: Loose enough that nothing binds by accident, so each drill tightens exactly
#: the one limit it is about. A drill that fires on a neighbour's threshold has
#: tested the neighbour.
OPEN = RiskLimits(
    max_open_positions=100, max_open_orders=100, max_positions_per_cluster=100,
    min_position_notional=Decimal("0.01"), max_position_notional=Decimal(10**9),
    min_position_pct_equity=Decimal("0.000001"),
    max_position_pct_equity=Decimal("0.99"),
    min_seconds_between_entries=0, max_gross_exposure=Decimal(1000),
    max_net_exposure=Decimal(1000), max_leverage=Decimal(1000),
    max_daily_loss=Decimal("0.99"), max_drawdown_from_peak=Decimal("0.99"),
    max_orders_per_minute=10_000, max_consecutive_errors=10_000,
    max_price_deviation_bps=Decimal(10_000),
)


def account(**changes: object) -> AccountState:
    base = AccountState(equity=Decimal(100_000), peak_equity=Decimal(100_000),
                        day_start_equity=Decimal(100_000))
    for key, value in changes.items():
        setattr(base, key, value)
    return base


def order(quantity: str = "1") -> Order:
    return Order("AAAUSDT", Side.BUY, Decimal(quantity), OrderType.MARKET, None)


def held(asset: str, quantity: str = "1") -> Position:
    return Position(asset=asset, quantity=Decimal(quantity), avg_price=PRICE,
                    opened_at=AT, last_entry_at=AT)


def fire(limits: RiskLimits, state: AccountState, **kwargs: object):
    engine = RiskEngine(limits=limits, account=state,
                        clusters=kwargs.pop("clusters", None))
    for _ in range(int(kwargs.pop("recent_orders", 0))):
        engine.record_order(AT)
    prices = {"AAAUSDT": PRICE, **kwargs.pop("prices", {})}
    # The corrupt-data drills need a price that is not the honest one.
    at_price = kwargs.pop("price_override", PRICE)
    return engine.check(kwargs.pop("order", order()), at_price, INSTRUMENT,
                        prices, AT, **kwargs)


# Each entry: the limit, the state that breaches it, the breach expected.
DRILLS = {
    "max_open_orders": (
        dataclasses.replace(OPEN, max_open_orders=3),
        {"state": account(open_order_count=3)}, Breach.MAX_OPEN_ORDERS),
    "max_position_notional": (
        dataclasses.replace(OPEN, max_position_notional=Decimal(500)),
        {"state": account(), "order": order("1")}, Breach.MAX_POSITION_NOTIONAL),
    "min_position_pct_equity": (
        dataclasses.replace(OPEN, min_position_pct_equity=Decimal("0.5")),
        {"state": account(), "order": order("1")}, Breach.MIN_POSITION_PCT_EQUITY),
    "max_open_positions": (
        dataclasses.replace(OPEN, max_open_positions=1),
        {"state": account(positions={"BBBUSDT": held("BBBUSDT")})},
        Breach.MAX_OPEN_POSITIONS),
    "max_positions_per_cluster": (
        dataclasses.replace(OPEN, max_positions_per_cluster=1),
        {"state": account(positions={"BBBUSDT": held("BBBUSDT")}),
         "clusters": {"AAAUSDT": "one", "BBBUSDT": "one"},
         "prices": {"BBBUSDT": PRICE}},
        Breach.MAX_POSITIONS_PER_CLUSTER),
    "min_position_notional": (
        dataclasses.replace(OPEN, min_position_notional=Decimal(5000)),
        {"state": account(), "order": order("1")}, Breach.MIN_POSITION_NOTIONAL),
    "max_position_pct_equity": (
        dataclasses.replace(OPEN, max_position_pct_equity=Decimal("0.001")),
        {"state": account(), "order": order("1")}, Breach.MAX_POSITION_PCT_EQUITY),
    "min_seconds_between_entries": (
        dataclasses.replace(OPEN, min_seconds_between_entries=3600),
        {"state": account(positions={"AAAUSDT": held("AAAUSDT")}),
         "prices": {"AAAUSDT": PRICE}},
        Breach.MIN_SECONDS_BETWEEN_ENTRIES),
    "max_gross_exposure": (
        dataclasses.replace(OPEN, max_gross_exposure=Decimal("0.005")),
        {"state": account(), "order": order("1")}, Breach.MAX_GROSS_EXPOSURE),
    "max_net_exposure": (
        dataclasses.replace(OPEN, max_net_exposure=Decimal("0.005"),
                            max_gross_exposure=Decimal(1000)),
        {"state": account(), "order": order("1")}, Breach.MAX_NET_EXPOSURE),
    "max_leverage": (
        dataclasses.replace(OPEN, max_leverage=Decimal("0.005"),
                            max_gross_exposure=Decimal(1000)),
        {"state": account(), "order": order("1")}, Breach.MAX_LEVERAGE),
    "max_daily_loss": (
        dataclasses.replace(OPEN, max_daily_loss=Decimal("0.01")),
        {"state": account(equity=Decimal(90_000))}, Breach.MAX_DAILY_LOSS),
    "max_drawdown_from_peak": (
        dataclasses.replace(OPEN, max_drawdown_from_peak=Decimal("0.01"),
                            max_daily_loss=Decimal("0.99")),
        {"state": account(equity=Decimal(90_000),
                          day_start_equity=Decimal(90_000))},
        Breach.MAX_DRAWDOWN_FROM_PEAK),
    "max_orders_per_minute": (
        dataclasses.replace(OPEN, max_orders_per_minute=2),
        {"state": account(), "recent_orders": 2}, Breach.MAX_ORDERS_PER_MINUTE),
    "max_consecutive_errors": (
        dataclasses.replace(OPEN, max_consecutive_errors=2),
        {"state": account(consecutive_errors=2)}, Breach.MAX_CONSECUTIVE_ERRORS),
    "max_price_deviation_bps": (
        dataclasses.replace(OPEN, max_price_deviation_bps=Decimal(10)),
        {"state": account(), "mid_price": Decimal(900)}, Breach.PRICE_DEVIATION),
}


@pytest.mark.fast
@pytest.mark.parametrize("name", sorted(DRILLS))
def test_every_limit_fires_at_its_threshold(name: str) -> None:
    """Refused, and refused by the limit under test.

    An order stopped by the wrong rule is a rule that has not been tested,
    hiding behind one that has — which is the failure this whole file exists to
    make impossible.
    """
    limits, state, expected = DRILLS[name]
    decision = fire(limits, **state)

    assert not decision.allowed, f"{name} did not refuse at its threshold"
    assert decision.breach is expected, (
        f"{name} was refused by {decision.breach} instead of {expected}: the "
        "drill is testing a different limit from the one it names")


@pytest.mark.fast
def test_the_drills_cover_every_limit() -> None:
    """A limit with no drill is one nobody has watched refuse, and from the
    outside that is indistinguishable from one that allows everything."""
    fields = {f.name for f in dataclasses.fields(RiskLimits)}
    assert set(DRILLS) == fields, (
        f"undrilled: {sorted(fields - set(DRILLS))}; "
        f"drilled but gone: {sorted(set(DRILLS) - fields)}")


@pytest.mark.fast
@pytest.mark.parametrize("name", sorted(DRILLS))
def test_the_same_state_passes_with_the_limit_relaxed(name: str) -> None:
    """The control, and the half that makes the drill mean something.

    Without it a drill passes when the state is refused for any reason at all,
    including a reason present before the limit was tightened — which is a test
    that would still pass if the limit were deleted.
    """
    _, state, _ = DRILLS[name]
    decision = fire(OPEN, **state)
    assert decision.allowed, (
        f"the {name} state is refused even with every limit open, by "
        f"{decision.breach}: the drill proves nothing about {name}")


# -- §16.7 corrupt data: zero price, a fifty percent jump, a broken feed ------


@pytest.mark.fast
@pytest.mark.parametrize(
    ("label", "price", "mid"),
    [
        ("zero price", Decimal(0), Decimal(1000)),
        ("negative price", Decimal(-1000), Decimal(1000)),
        ("fifty percent jump", Decimal(1500), Decimal(1000)),
        ("ten times the price", Decimal(10_000), Decimal(1000)),
        ("mid of zero, a broken feed", Decimal(10_000), Decimal(0)),
        ("negative mid", Decimal(10_000), Decimal(-1)),
    ],
)
def test_corrupt_data_is_refused_by_the_guard_meant_for_it(
    label: str, price: Decimal, mid: Decimal,
) -> None:
    """§16.7's corrupt-data drill, and the defect it found.

    A mid of zero is *itself* corrupt data — it is what a broken feed reports.
    The guard used to skip on `mid_price > 0`, which disabled the protection
    against corrupt data using one particular kind of corrupt data: a price ten
    times the real one was refused with a good mid and **allowed** with a mid of
    zero, at a size that cleared every other limit.

    Refused is not enough; it has to be refused by *this* guard. In the original
    probe the broken-feed order happened to be caught by a position-size
    minimum, which is luck and not a control — a different size walks past it.
    """
    # The shipped defaults, not the loosened ones the other drills use: a
    # corrupt-data guard has to be tested at the deviation limit that actually
    # runs. At the loosened 10,000bps a fifty percent jump is inside the band,
    # which is true and is not what this drill is about.
    decision = fire(RiskLimits(), state=account(), order=order("1"),
                    mid_price=mid, price_override=price)
    assert not decision.allowed, f"{label} was allowed"
    assert decision.breach is Breach.PRICE_DEVIATION, (
        f"{label} was refused by {decision.breach}, not by the guard §14.1 "
        "provides for it — which is luck, and a different size walks past it")


@pytest.mark.fast
def test_a_stated_absence_of_a_mid_is_not_a_broken_feed() -> None:
    """`None` and zero are different statements and stay different.

    None is the caller saying it has no reference, which a backtest without book
    data legitimately does. Zero is a feed reporting that the price is nothing.
    Collapsing the two would either break every caller without a book or leave
    the hole this file just closed.
    """
    decision = fire(RiskLimits(), state=account(), order=order("1"),
                    mid_price=None, price_override=Decimal(10_000))
    assert decision.allowed, (
        "a caller with no reference is now refused, which breaks every backtest "
        "without book data")
