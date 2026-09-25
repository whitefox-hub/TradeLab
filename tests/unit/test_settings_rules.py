"""Bounds on operator settings. Plan.md §12.4, §20.4.

Each case pairs a refusal with an acceptance, because a validator that refuses
everything passes every rejection test.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from tradelab.api.settings_rules import (
    RULES,
    Invalid,
    max_history_years,
    validate,
)


@pytest.mark.fast
def test_seventy_years_of_history_does_not_exist() -> None:
    """The case that prompted this module.

    Seventy is not merely large. Perpetual history on this venue begins in
    September 2019, so the window silently resolves to whatever exists and every
    figure computed from it reports a sample the study did not have.
    """
    with pytest.raises(Invalid, match="at most"):
        validate("history_years", {"years": 70})
    validate("history_years", {"years": 5})


@pytest.mark.fast
def test_the_history_ceiling_is_derived_not_pinned() -> None:
    """Hard-coding 7 makes it wrong next year and silently permissive after."""
    assert max_history_years(datetime(2020, 9, 1, tzinfo=UTC)) == 1
    assert max_history_years(datetime(2026, 9, 1, tzinfo=UTC)) == 7
    assert max_history_years(datetime(2035, 1, 1, tzinfo=UTC)) == 15


@pytest.mark.fast
@pytest.mark.parametrize(
    "value",
    [{"years": 0}, {"years": -3}, {"years": "siete"}, {"years": 7.5}, {"years": True}],
)
def test_a_history_window_must_be_a_usable_whole_number(value: dict) -> None:
    with pytest.raises(Invalid):
        validate("history_years", value)


@pytest.mark.fast
def test_an_empty_universe_is_refused() -> None:
    with pytest.raises(Invalid, match="cannot be empty"):
        validate("research_universe", {"assets": []})
    validate("research_universe", {"assets": ["BTCUSDT"]})


@pytest.mark.fast
def test_base_assets_are_not_venue_symbols() -> None:
    """`BTC` never matches market data; `BTCUSDT` does. Caught on entry rather
    than as an empty result set a week later."""
    with pytest.raises(Invalid, match="not venue symbols"):
        validate("research_universe", {"assets": ["BTC", "ETH"]})
    validate("research_universe", {"assets": ["BTCUSDT", "ETHUSDT"]})


@pytest.mark.fast
def test_a_duplicated_asset_would_be_weighted_twice() -> None:
    with pytest.raises(Invalid, match="duplicate"):
        validate("research_universe", {"assets": ["BTCUSDT", "ETHUSDT", "BTCUSDT"]})


@pytest.mark.fast
def test_a_universe_wider_than_the_collectors_is_refused() -> None:
    """Past what can be collected daily the archive develops gaps, and a gap
    looks exactly like a quiet market."""
    with pytest.raises(Invalid, match="ceiling"):
        validate("research_universe", {"assets": [f"A{n}USDT" for n in range(61)]})
    validate("research_universe", {"assets": [f"A{n}USDT" for n in range(60)]})


@pytest.mark.fast
def test_rebalancing_faster_than_hourly_only_buys_spread() -> None:
    """§0.2 puts the viable band at 2-7 days. Re-evaluating every minute does not
    make the strategy quicker, it makes it pay the spread more often."""
    with pytest.raises(Invalid, match="hourly floor"):
        validate("rebalance_minutes", {"minutes": 1})
    validate("rebalance_minutes", {"minutes": 60})


@pytest.mark.fast
def test_an_alert_that_fires_daily_is_one_nobody_reads() -> None:
    with pytest.raises(Invalid, match="ordinary noise"):
        validate("alert_drawdown_pct", {"pct": 0.2})
    with pytest.raises(Invalid):
        validate("alert_drawdown_pct", {"pct": 150})
    validate("alert_drawdown_pct", {"pct": 15})


@pytest.mark.fast
def test_an_unknown_field_is_refused_rather_than_dropped() -> None:
    """Silently ignoring it means the operator believes they set something they
    did not, which is the failure mode a form cannot show."""
    with pytest.raises(Invalid, match="unknown fields"):
        validate("history_years", {"years": 5, "anios": 3})


@pytest.mark.fast
def test_a_missing_field_names_what_was_expected() -> None:
    with pytest.raises(Invalid, match="missing"):
        validate("history_years", {})


@pytest.mark.fast
def test_a_key_with_no_rule_cannot_be_accepted() -> None:
    """Default-deny. A setting added to the API without a rule should fail
    closed, not slip through unchecked."""
    with pytest.raises(Invalid, match="no validation rule"):
        validate("something_new", {"x": 1})


@pytest.mark.fast
def test_every_editable_key_has_a_rule() -> None:
    """The two lists are declared in different modules and would drift."""
    from pathlib import Path

    import tradelab.api.app as api_module

    source = Path(api_module.__file__).read_text()
    offered = re.findall(r'^\s{8}"([a-z_]+)": "', source, re.M)
    assert offered, "could not read the EDITABLE map"
    for key in offered:
        assert key in RULES, f"{key} is offered for editing with no rule"
