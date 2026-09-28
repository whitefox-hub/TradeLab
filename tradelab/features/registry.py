"""Feature registry. Plan.md §6.

Declared lookback drives warmup and the CV purge (§8.2); understating it breaks purging
silently. Causality: no feature may read a row with `available_at` later than the instant
computed; verified by truncation invariance (§16.3).
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import product
from typing import Any

import polars as pl

from tradelab.features.params import ParamSpace

#: Families from §6. Closed (like §11.1) so importance clustering does not fragment.
FAMILIES = frozenset({
    "momentum", "trend", "volatility", "volume", "structure", "vwap",
    "orderflow", "open_interest", "funding", "liquidations", "basis",
    "options", "macro", "onchain", "news", "cross_asset", "levels",
})


@dataclass(frozen=True, slots=True)
class FeatureDef:
    name: str
    family: str
    fn: Callable[..., pl.Expr]
    lookback: Callable[[dict], int]
    params: dict[str, ParamSpace]
    code_hash: str
    requires: tuple[str, ...] = ()

    def lookback_bars(self, params: dict) -> int:
        return int(self.lookback(params))

    def validate_params(self, params: dict) -> None:
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f"{self.name}: unknown parameters {sorted(unknown)}")
        missing = set(self.params) - set(params)
        if missing:
            raise ValueError(f"{self.name}: missing parameters {sorted(missing)}")
        for key, space in self.params.items():
            if not space.contains(params[key]):
                raise ValueError(f"{self.name}.{key}={params[key]!r} outside its declared space")

    def expr(self, **params: Any) -> pl.Expr:
        self.validate_params(params)
        return self.fn(**params).alias(self.column(params))

    def column(self, params: dict) -> str:
        suffix = "_".join(f"{k}{params[k]}" for k in sorted(params))
        return f"{self.name}__{suffix}" if suffix else self.name

    def grid(self) -> Iterator[dict]:
        """Every combination of the declared parameter space."""
        if not self.params:
            yield {}
            return
        keys = sorted(self.params)
        for combo in product(*(list(self.params[k].values()) for k in keys)):
            yield dict(zip(keys, combo, strict=True))


@dataclass
class Registry:
    features: dict[str, FeatureDef] = field(default_factory=dict)

    def add(self, definition: FeatureDef) -> None:
        if definition.name in self.features:
            raise ValueError(f"duplicate feature name {definition.name!r}")
        self.features[definition.name] = definition

    def get(self, name: str) -> FeatureDef:
        return self.features[name]


    def max_lookback(self, feature_set: list[tuple[str, dict]]) -> int:
        """Largest lookback in a feature set; purge covers it plus label horizon (§8.2)."""
        return max(
            (self.get(name).lookback_bars(params) for name, params in feature_set),
            default=0,
        )

    def __len__(self) -> int:
        return len(self.features)


#: Residual weight below which an EWM's memory counts as exhausted (sets its lookback).
MEMORY_EPSILON = 1e-6


def ewm_lookback(span: int, epsilon: float = MEMORY_EPSILON) -> int:
    """Bars after which an EWM of this span retains less than `epsilon` weight.

    alpha = 2/(span+1); remaining weight after n bars is (1-alpha)^n.
    """
    import math

    alpha = 2.0 / (span + 1.0)
    return math.ceil(math.log(epsilon) / math.log(1.0 - alpha))


REGISTRY = Registry()


def feature(
    *, family: str, lookback: Callable[[dict], int] | int,
    params: dict[str, ParamSpace] | None = None,
    requires: tuple[str, ...] = ("close",),
    registry: Registry | None = None,
) -> Callable[[Callable[..., pl.Expr]], FeatureDef]:
    """Register a feature definition. `lookback` is a function of params (checked by §16.3)."""
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; extend FAMILIES deliberately")

    def decorate(fn: Callable[..., pl.Expr]) -> FeatureDef:
        source = inspect.getsource(fn)
        definition = FeatureDef(
            name=fn.__name__,
            family=family,
            fn=fn,
            lookback=lookback if callable(lookback) else (lambda _p, n=lookback: n),
            params=params or {},
            code_hash=hashlib.sha256(source.encode()).hexdigest(),
            requires=requires,
        )
        # `is not None`, never `or`: an empty Registry is falsy (__len__), so `or` would
        # register test/canary features in the global REGISTRY.
        target = registry if registry is not None else REGISTRY
        target.add(definition)
        return definition

    return decorate
