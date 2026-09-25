"""Bounds on operator settings. Plan.md §12.4, §20.4.

Server side, and that is the point rather than a detail: a rule in the form is a
suggestion, because the endpoint is reachable without the form. Every bound here
is checked on the way in and refused with the reason.

The bounds are not decoration. `{"years": 70}` is not merely large — perpetual
futures history on this venue begins in 2019, so a seventy-year window silently
resolves to whatever exists and every figure computed from it reports a sample
the study did not have. A setting that cannot be satisfied should say so when it
is entered, not produce a quiet answer to a different question.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: USD-M perpetuals launched in September 2019, so nothing earlier exists to ask
#: for. Derived rather than hard-coded, so the ceiling grows with the archive
#: instead of becoming wrong next year.
HISTORY_BEGINS = datetime(2019, 9, 1, tzinfo=UTC)

SYMBOL = re.compile(r"^[A-Z0-9]{2,20}(USDT|USDC|USD)$")


class Invalid(ValueError):
    """A setting the system refuses, with a reason a human can act on."""


@dataclass(frozen=True, slots=True)
class Rule:
    fields: tuple[str, ...]
    check: Callable[[dict], None]
    describes: str


def max_history_years(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    return max(1, int((now - HISTORY_BEGINS).days / 365.25))


def _history_years(value: dict) -> None:
    years = value.get("years")
    if isinstance(years, bool) or not isinstance(years, int):
        raise Invalid(f"years must be a whole number, got {years!r}")
    ceiling = max_history_years()
    if years < 1:
        raise Invalid("years must be at least 1; a study needs a window")
    if years > ceiling:
        raise Invalid(
            f"years must be at most {ceiling}. Perpetual history on this venue "
            f"begins in September 2019, so a longer window resolves to whatever "
            f"exists and reports a sample the study did not have."
        )


def _research_universe(value: dict) -> None:
    assets = value.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        raise Invalid("assets must be a list of symbol strings")
    if not assets:
        raise Invalid("the research universe cannot be empty")
    if len(assets) > 60:
        raise Invalid(
            f"{len(assets)} assets is beyond what the collectors can keep fresh; "
            f"60 is the ceiling. Widening the universe past what can be collected "
            f"daily produces gaps, and a gap looks exactly like a quiet market."
        )
    if len(set(assets)) != len(assets):
        duplicates = sorted({a for a in assets if assets.count(a) > 1})
        raise Invalid(f"duplicate assets would be weighted twice: {duplicates}")
    bad = [a for a in assets if not SYMBOL.match(a)]
    if bad:
        raise Invalid(
            f"not venue symbols: {bad}. Expected the traded pair, such as "
            f"BTCUSDT, rather than the base asset."
        )


def _rebalance_minutes(value: dict) -> None:
    minutes = value.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise Invalid(f"minutes must be a whole number, got {minutes!r}")
    if minutes < 1:
        raise Invalid("minutes must be at least 1")
    if minutes > 10_080:
        raise Invalid("minutes must be at most 10080, which is one week")
    # §0.2 puts the viable holding band at 2-7 days. Re-evaluating far inside it
    # does not make the strategy faster, it makes it pay the spread more often.
    if minutes < 60:
        raise Invalid(
            f"{minutes} minutes is below the hourly floor. §0.2 puts the viable "
            f"holding band at 2-7 days; re-evaluating faster does not make the "
            f"strategy quicker, it makes it pay the spread more often."
        )


def _alert_drawdown(value: dict) -> None:
    pct = value.get("pct")
    if isinstance(pct, bool) or not isinstance(pct, int | float):
        raise Invalid(f"pct must be a number, got {pct!r}")
    if not 0 < pct <= 100:
        raise Invalid("pct must be greater than 0 and at most 100")
    if pct < 1:
        raise Invalid(
            f"{pct}% would alert on ordinary noise. An alert that fires daily is "
            f"one nobody reads, which is worse than none."
        )


RULES: dict[str, Rule] = {
    "history_years": Rule(("years",), _history_years,
                          "a whole number of years, 1 to what the archive holds"),
    "research_universe": Rule(("assets",), _research_universe,
                              "a list of venue symbols, 1 to 60, no duplicates"),
    "rebalance_minutes": Rule(("minutes",), _rebalance_minutes,
                              "whole minutes, 60 to 10080"),
    "alert_drawdown_pct": Rule(("pct",), _alert_drawdown,
                               "a percentage above 1 and at most 100"),
}


def validate(key: str, value: Any) -> None:
    """Refuse anything the system cannot act on. Raises `Invalid` with the why."""
    rule = RULES.get(key)
    if rule is None:
        raise Invalid(f"{key!r} has no validation rule and cannot be accepted")
    if not isinstance(value, dict):
        raise Invalid(f"the value must be an object with {list(rule.fields)}")

    missing = [f for f in rule.fields if f not in value]
    if missing:
        raise Invalid(f"missing {missing}; expected {rule.describes}")
    # An unknown field is a typo or a misunderstanding, and silently dropping it
    # means the operator believes they set something they did not.
    extra = sorted(set(value) - set(rule.fields))
    if extra:
        raise Invalid(f"unknown fields {extra}; expected only {list(rule.fields)}")

    rule.check(value)
