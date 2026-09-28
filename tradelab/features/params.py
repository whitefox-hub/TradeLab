"""Parameter spaces for feature definitions; grids expand at experiment time. Plan.md §6."""

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
