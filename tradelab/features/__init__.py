"""Feature definitions and registry. Plan.md §6.

Importing this package populates the registry. The definition modules are
imported here rather than left to callers because a registry that is empty unless
someone remembers an import is a registry that will be empty at the worst moment
— and the failure is a KeyError deep inside a study, not at the point of the
mistake.
"""

from tradelab.features import momentum, participation, volatility  # noqa: F401
from tradelab.features.registry import REGISTRY, Registry, feature

__all__ = ["REGISTRY", "Registry", "feature"]
