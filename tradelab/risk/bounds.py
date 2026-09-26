"""What a risk limit may be set to. Plan.md §14.1, §12.4, §20.4.

Server side, and that is the point rather than a detail: a bound in the form is
a suggestion, because the endpoint is reachable without the form.

**These are bounds on the bounds.** A risk limit is the thing that stops a bad
decision, so a limit set loosely enough is a limit that has been removed while
still appearing on the screen — `max_daily_loss` at 1.0 is "halt after losing
everything", which reads like a configured control and is not one. The ceilings
below are what keeps a limit a limit.

They are deliberately not tight. §12.4 puts sizing and risk limits in the
operator's hands on purpose, and a bound that forces one value is a setting
pretending to be a choice. What is refused is the value that disables the
control, not the value that is merely aggressive.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class Invalid(ValueError):
    """A limit the system refuses, with a reason a human can act on."""


@dataclass(frozen=True, slots=True)
class Bound:
    low: Decimal
    high: Decimal
    whole: bool
    describes: str


def _d(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise Invalid(f"{field}: {value!r} is not a number") from None


#: One entry per field of `RiskLimits`. A field with no bound cannot be set,
#: which is the safe direction: a limit nobody bounded is one anybody can
#: disable. A test asserts the two lists match.
BOUNDS: dict[str, Bound] = {
    "max_open_positions": Bound(
        Decimal(1), Decimal(200), True,
        "1 to 200 positions. Zero would refuse every order, which is a halt "
        "wearing a limit's name."),
    "max_open_orders": Bound(
        Decimal(1), Decimal(500), True, "1 to 500 orders."),
    "max_positions_per_cluster": Bound(
        Decimal(1), Decimal(50), True,
        "1 to 50. Above the position cap it stops constraining anything."),
    "min_position_notional": Bound(
        Decimal(1), Decimal(100_000), False,
        "1 to 100000. Below the venue minimum the order is rejected there "
        "instead, which is the same refusal without the reason."),
    "max_position_notional": Bound(
        Decimal(10), Decimal(10_000_000), False, "10 to 10000000."),
    "min_position_pct_equity": Bound(
        Decimal("0.0001"), Decimal("0.5"), False,
        "0.0001 to 0.5 of equity."),
    "max_position_pct_equity": Bound(
        Decimal("0.01"), Decimal(1), False,
        "0.01 to 1.0 of equity. Above 1.0 one name may exceed the book."),
    "min_seconds_between_entries": Bound(
        Decimal(0), Decimal(86_400), True, "0 to 86400 seconds."),
    "max_gross_exposure": Bound(
        Decimal("0.1"), Decimal(10), False,
        "0.1 to 10 times equity. Ten is already far past what this venue's "
        "margin allows; the cap is here so a typo cannot ask for a hundred."),
    "max_net_exposure": Bound(
        Decimal("0.1"), Decimal(10), False, "0.1 to 10 times equity."),
    "max_leverage": Bound(
        Decimal(1), Decimal(20), False,
        "1 to 20. One is unlevered; the ceiling is a firebreak, not advice."),
    "max_daily_loss": Bound(
        Decimal("0.005"), Decimal("0.5"), False,
        "0.005 to 0.5 of the day's opening equity. Half is already a very bad "
        "day; at 1.0 the halt fires after losing everything and the control "
        "is decorative."),
    "max_drawdown_from_peak": Bound(
        Decimal("0.01"), Decimal("0.5"), False,
        "0.01 to 0.5 from peak. This one halts and needs a person to clear "
        "(§14.1), so a value that never fires removes the review as well."),
    "max_orders_per_minute": Bound(
        Decimal(1), Decimal(600), True,
        "1 to 600 a minute. It is a firebreak against a loop, so a ceiling no "
        "loop would reach defeats it."),
    "max_consecutive_errors": Bound(
        Decimal(1), Decimal(100), True,
        "1 to 100. A system failing this many times does not know what it "
        "holds."),
    "max_price_deviation_bps": Bound(
        Decimal(1), Decimal(1000), False,
        "1 to 1000 bps. At a thousand an order may be ten percent from mid, "
        "which is the fat finger this is meant to catch."),
}


def validate(values: dict[str, Any]) -> dict[str, Decimal]:
    """Refuse anything the engine cannot act on. Raises `Invalid` with the why.

    Returns the parsed values, so a caller cannot validate one representation
    and store another.
    """
    import dataclasses

    from tradelab.risk.limits import RiskLimits

    expected = {f.name for f in dataclasses.fields(RiskLimits)}
    missing = sorted(expected - set(values))
    if missing:
        raise Invalid(f"missing {missing}: a configuration sets every limit, "
                      "because one left out would silently take a default "
                      "nobody chose")
    # An unknown field is a typo or a misunderstanding, and accepting it
    # silently is how an operator comes to believe a limit exists.
    unknown = sorted(set(values) - expected)
    if unknown:
        raise Invalid(f"unknown {unknown}: no such limit")

    parsed: dict[str, Decimal] = {}
    for name in sorted(expected):
        bound = BOUNDS[name]
        number = _d(values[name], name)
        if bound.whole and number != number.to_integral_value():
            raise Invalid(f"{name}: must be a whole number. {bound.describes}")
        if not bound.low <= number <= bound.high:
            raise Invalid(f"{name}: {number} is outside {bound.low} to "
                          f"{bound.high}. {bound.describes}")
        parsed[name] = number

    # Pairs that are individually fine and jointly meaningless. A minimum above
    # its maximum refuses every order, which is a halt that reports itself as a
    # sizing rule.
    for low_name, high_name in (
        ("min_position_notional", "max_position_notional"),
        ("min_position_pct_equity", "max_position_pct_equity"),
    ):
        if parsed[low_name] >= parsed[high_name]:
            raise Invalid(f"{low_name} ({parsed[low_name]}) is not below "
                          f"{high_name} ({parsed[high_name]}); together they "
                          "refuse every order")
    if parsed["max_net_exposure"] > parsed["max_gross_exposure"]:
        raise Invalid("max_net_exposure above max_gross_exposure cannot bind: "
                      "net exposure is bounded by gross by construction")
    return parsed
