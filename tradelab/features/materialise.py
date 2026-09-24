"""Feature materialisation. Plan.md §6, §9.6.

Features are lazy Polars expressions so the planner fuses thousands of variants
into one pass over the data: computing forty RSI periods should cost close to
what one costs, not forty times as much.
"""

from __future__ import annotations

import hashlib
import json

import polars as pl

from tradelab.features.registry import REGISTRY, Registry

FeatureSet = list[tuple[str, dict]]


def materialise(
    frame: pl.DataFrame | pl.LazyFrame,
    feature_set: FeatureSet,
    registry: Registry | None = None,
    keep: tuple[str, ...] = ("event_time", "available_at", "asset", "close"),
) -> pl.DataFrame:
    """Compute a feature set in a single pass."""
    reg = registry if registry is not None else REGISTRY
    lazy = frame.lazy() if isinstance(frame, pl.DataFrame) else frame
    exprs = [reg.get(name).expr(**params) for name, params in feature_set]
    columns = [c for c in keep if c in lazy.collect_schema().names()]
    return lazy.select([*columns, *exprs]).collect()


def feature_set_hash(feature_set: FeatureSet, registry: Registry | None = None) -> str:
    """Identity of a feature set: names, parameters and the code behind them.

    Includes each definition's `code_hash`, so editing a feature's implementation
    produces a different set even when the name and parameters are unchanged —
    which is what stops a silent recomputation being mistaken for a cache hit.
    """
    reg = registry if registry is not None else REGISTRY
    payload = sorted(
        {"name": name, "params": params, "code": reg.get(name).code_hash}
        for name, params in feature_set  # type: ignore[type-var]
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def expand(name: str, registry: Registry | None = None) -> FeatureSet:
    """Every parameterisation of one definition."""
    reg = registry if registry is not None else REGISTRY
    return [(name, params) for params in reg.get(name).grid()]
