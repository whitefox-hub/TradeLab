"""Feature definitions and registry. Plan.md §6.

Importing this package populates the registry; otherwise lookups KeyError deep in a study.
"""

from tradelab.features import (  # noqa: F401
    funding,
    momentum,
    participation,
    volatility,
)
from tradelab.features.registry import REGISTRY, Registry, feature

__all__ = ["REGISTRY", "Registry", "feature"]
