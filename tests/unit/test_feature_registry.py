"""Registry population and contract. Plan.md §6."""

from __future__ import annotations

import pytest

from tradelab.features import REGISTRY


@pytest.mark.fast
def test_importing_the_package_populates_the_registry() -> None:
    """A registry that is empty unless someone remembers an import fails deep
    inside a study, not at the point of the mistake."""
    assert len(REGISTRY) >= 10
    assert "rsi" in REGISTRY.features


@pytest.mark.fast
def test_every_definition_declares_a_positive_lookback() -> None:
    """The purge in §8.2 is derived from this. Zero would silently disable it."""
    for definition in REGISTRY.features.values():
        params = next(definition.grid())
        assert definition.lookback_bars(params) > 0, definition.name


@pytest.mark.fast
def test_max_lookback_drives_the_purge() -> None:
    """The feature set's largest lookback is what the CV scheme must cover."""
    feature_set = [("rsi", {"period": 14, "source": "close"}),
                   ("ema", {"span": 50})]
    largest = REGISTRY.max_lookback(feature_set)
    assert largest == max(REGISTRY.get(n).lookback_bars(p) for n, p in feature_set)
    assert largest > 50, "an EWM lookback should exceed its span"
