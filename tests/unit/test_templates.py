"""High-impact event templates. Plan.md §11.7, §20.5."""

from __future__ import annotations

import pytest

from tradelab.news.expiry import ASSUMED_DECAY_HOURS
from tradelab.news.strength import SIGMA_BY_STRENGTH
from tradelab.news.templates import BY_KEY, CATALOGUE


@pytest.mark.fast
def test_every_template_names_a_shape_the_system_handles() -> None:
    """Shape decides which machinery applies. A typo here would file an event
    under a fourth kind nothing knows what to do with."""
    for template in CATALOGUE:
        assert template.shape in {"shock", "schedule", "process", "regime_shift"}


@pytest.mark.fast
def test_wars_and_collapses_are_regime_shifts_not_shocks() -> None:
    """They replace the baseline rather than deviating from it, so a decay
    fitted to one estimates a reversion that never happens."""
    for key in ("war_major_powers", "war_us_iran", "banking_crisis",
                "exchange_collapse", "peace_deal"):
        assert BY_KEY[key].shape == "regime_shift", key


@pytest.mark.fast
def test_the_end_of_a_regime_is_its_own_regime_shift() -> None:
    """A peace deal does not return the market to where it was before the war,
    so it is not the reversal of one — it is another replacement."""
    assert BY_KEY["peace_deal"].shape == "regime_shift"
    assert BY_KEY["peace_deal"].direction == 1


@pytest.mark.fast
def test_strengths_are_on_the_scale_the_dialog_uses() -> None:
    """A template outside 1-10 would produce a rating the form cannot show and
    the converter refuses."""
    for template in CATALOGUE:
        assert template.strength in SIGMA_BY_STRENGTH
        assert template.direction in (-1, 1)


@pytest.mark.fast
def test_the_gravest_entries_carry_the_largest_strength() -> None:
    """The catalogue is scanned under time pressure, so severity has to track
    the number rather than the wording."""
    assert BY_KEY["war_major_powers"].strength == 10
    assert BY_KEY["ai_breakthrough"].strength < BY_KEY["banking_crisis"].strength


@pytest.mark.fast
def test_market_wide_is_set_where_no_unaffected_asset_exists() -> None:
    """§11.3's cross-sectional benchmark needs something to difference against,
    and a war leaves nothing unaffected — so the measurement must fall back to
    the weaker time-series counterfactual rather than pretend."""
    assert BY_KEY["banking_crisis"].market_wide
    assert BY_KEY["war_major_powers"].market_wide
    # A sector story is not market-wide and keeps its cross-section.
    assert not BY_KEY["ai_breakthrough"].market_wide


@pytest.mark.fast
def test_every_decaying_template_type_has_a_horizon_someone_chose() -> None:
    """A type with no entry takes the 72-hour default silently, and a default
    nobody decided on is indistinguishable from one somebody did.

    Written as a real assertion after the first version read
    `assert x in ASSUMED_DECAY_HOURS or True`, which is true for every x. It
    found `technology` falling through on the first honest run.
    """
    decaying = [t for t in CATALOGUE if t.shape in ("shock", "schedule", "process")]
    assert decaying, "no decaying templates; the check is looking at nothing"
    for template in decaying:
        assert template.event_type in ASSUMED_DECAY_HOURS, (
            f"{template.event_type} would take the default horizon unnoticed"
        )


@pytest.mark.fast
def test_a_regime_shift_template_needs_no_horizon() -> None:
    """It never expires on a clock, so a horizon for one would be misleading
    even if it were never read."""
    from datetime import UTC, datetime, timedelta

    from tradelab.news.expiry import resolve

    now = datetime(2026, 9, 26, tzinfo=UTC)
    template = BY_KEY["war_major_powers"]
    result = resolve(event_type=template.event_type, shape=template.shape,
                     first_seen_at=now - timedelta(days=400), now=now)
    assert result.active
    assert result.expires_at is None


@pytest.mark.fast
def test_the_keys_are_unique() -> None:
    """They are the provenance stamp on an annotation; a duplicate would make
    two different suggestions indistinguishable afterwards."""
    assert len(BY_KEY) == len(CATALOGUE)
