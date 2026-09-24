"""Feature registry. Plan.md §6.

Two properties are enforced here rather than reviewed by hand, because both are
correctness issues that produce plausible numbers when violated:

**Declared lookback.** It drives the warmup and, more importantly, the purge size
in cross-validation (§8.2). A feature that reaches further back than it declares
silently breaks purging, and a broken purge produces a validation report that
looks entirely normal.

**Causality.** No feature may read a row whose `available_at` is later than the
instant being computed. Verified by truncation invariance (§16.3), not by review.
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

#: Families from §6. Closed, for the same reason the event taxonomy is (§11.1):
#: free-form families fragment feature-importance clustering into noise.
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

    def by_family(self, family: str) -> list[FeatureDef]:
        return [f for f in self.features.values() if f.family == family]

    def max_lookback(self, feature_set: list[tuple[str, dict]]) -> int:
        """Largest lookback in a feature set.

        This is the number the CV scheme needs: purge must cover it plus the
        label horizon (§8.2), and neither is a value anyone tunes by hand.
        """
        return max(
            (self.get(name).lookback_bars(params) for name, params in feature_set),
            default=0,
        )

    def __len__(self) -> int:
        return len(self.features)


#: Residual weight below which a feature's memory is treated as exhausted.
#: Exponential kernels never truly forget, so "the lookback" is a choice of
#: threshold rather than a fact, and leaving that threshold implicit is how an
#: understated lookback slips through — the purge derived from it is then too
#: small, and the validation report that follows looks entirely normal.
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
    """Register a feature definition.

    `lookback` is mandatory and takes the parameters, because for most features
    it depends on them. Declaring it as a constant when it is not is the lie the
    honesty property in §16.3 exists to catch.
    """
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
        # `is not None`, never `or`: Registry defines __len__, so an EMPTY
        # registry is falsy and `registry or REGISTRY` silently falls through to
        # the global one. That is how the leakage canaries — features written to
        # cheat on purpose — ended up registered in the production registry,
        # available to real studies.
        target = registry if registry is not None else REGISTRY
        target.add(definition)
        return definition

    return decorate
