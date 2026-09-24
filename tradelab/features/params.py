"""Parameter spaces for feature definitions. Plan.md §6.

A feature is (definition, parameters), not a column. The definition is registered
once; the parameterisation expands at experiment time — which is what lets the
same indicator be evaluated at thirty periods without thirty definitions.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


class ParamSpace(Protocol):
    def values(self) -> Iterator[Any]: ...
    def contains(self, value: Any) -> bool: ...
    def schema(self) -> dict: ...


@dataclass(frozen=True, slots=True)
class IntRange:
    low: int
    high: int
    step: int = 1

    def values(self) -> Iterator[int]:
        return iter(range(self.low, self.high + 1, self.step))

    def contains(self, value: Any) -> bool:
        return isinstance(value, int) and self.low <= value <= self.high

    def schema(self) -> dict:
        return {"type": "integer", "minimum": self.low, "maximum": self.high}


@dataclass(frozen=True, slots=True)
class FloatRange:
    low: float
    high: float
    step: float

    def values(self) -> Iterator[float]:
        current, out = self.low, []
        while current <= self.high + 1e-12:
            out.append(round(current, 10))
            current += self.step
        return iter(out)

    def contains(self, value: Any) -> bool:
        return isinstance(value, int | float) and self.low <= value <= self.high

    def schema(self) -> dict:
        return {"type": "number", "minimum": self.low, "maximum": self.high}


@dataclass(frozen=True, slots=True)
class Choice:
    options: tuple[Any, ...]

    def __init__(self, *options: Any) -> None:
        object.__setattr__(self, "options", tuple(options))

    def values(self) -> Iterator[Any]:
        return iter(self.options)

    def contains(self, value: Any) -> bool:
        return value in self.options

    def schema(self) -> dict:
        return {"enum": list(self.options)}
