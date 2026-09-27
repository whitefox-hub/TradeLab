"""The trend book, as registered. research/PREREG-009-trend-book.md.

One book, two guides. When the operator's trend is not 5, their reading sizes
the book and it is held until a barrier or a change. When the trend is 5, the
algorithm's rule does: seven-day momentum at a fixed ±20%. Every decision says
which guide made it, because PREREG-009 is judged by guide.

Pure: no network, no database, no clock of its own. The runner feeds it bars
and the current setting, and it returns the exposure the book should hold.
That is what lets every rule be tested on a constructed path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from tradelab.scenario.tilt import NEUTRAL, strength

WEIGHTS = {"BTCUSDT": 0.6, "ETHUSDT": 0.4}
TAKE_PROFIT_ATR = 2.0
STOP_ATR = 1.0
ATR_DAYS = 14
MOMENTUM_DAYS = 7
ALGORITHM_EXPOSURE = 0.20


@dataclass(frozen=True, slots=True)
class Setting:
    """The operator's slider as recorded, and the moment it was last changed.

    `changed_at` is the key that ends a post-exit flat spell: a view that hit
    its barrier is not re-entered until the operator records a new one.
    """

    trend: int
    source: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class Bar:
    """One 5-minute bar of the basket index."""

    at: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class State:
    guide: str = "algorithm"            # operator | algorithm
    exposure: float = 0.0               # signed fraction of equity
    trend: int = NEUTRAL
    source: str = "manual"
    entry: float | None = None          # index level at entry, operator guide
    atr: float | None = None            # daily ATR as a fraction, at entry
    setting_at: datetime | None = None  # the setting this state acted on
    exited: bool = False                # a barrier closed the operator's view
    exit_reason: str | None = None
    algorithm_day: str | None = None    # the UTC day the algorithm last decided


@dataclass(frozen=True, slots=True)
class Decision:
    state: State
    changed: bool
    reason: str


def basket(prices: dict[str, np.ndarray], anchor: dict[str, float]) -> np.ndarray:
    """0.6 x BTC/BTC_0 + 0.4 x ETH/ETH_0. The anchor only scales; returns and
    barriers, being ratios, do not depend on it."""
    out = np.zeros_like(next(iter(prices.values())), dtype=np.float64)
    for symbol, weight in WEIGHTS.items():
        out = out + weight * prices[symbol] / anchor[symbol]
    return out


def daily_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    """14-day average true range of the basket's daily bars, as a fraction of
    the last close. The bars passed must all be closed days."""
    if close.size < ATR_DAYS + 1:
        return float("nan")
    previous = close[:-1]
    ranges = np.maximum(high[1:], previous) - np.minimum(low[1:], previous)
    return float(ranges[-ATR_DAYS:].mean() / close[-1])


def momentum(daily_close: np.ndarray) -> int:
    """Sign of the basket's return over the previous seven closed days."""
    if daily_close.size < MOMENTUM_DAYS + 1:
        return 0
    return int(np.sign(daily_close[-1] / daily_close[-1 - MOMENTUM_DAYS] - 1.0))


def step(state: State, setting: Setting, bar: Bar, atr: float, signal: int) -> Decision:
    """The book after one closed 5-minute bar.

    Order of precedence: a new operator setting, then the operator's barriers,
    then the algorithm's daily rule. Barriers are judged on this bar against
    levels fixed at entry; the stop is checked first, and a bar that opens
    through the stop exits at the bar's worst price.
    """
    new_setting = setting.changed_at != state.setting_at

    if setting.trend != NEUTRAL:
        if new_setting:
            return Decision(State(
                guide="operator", exposure=strength(setting.trend), trend=setting.trend,
                source=setting.source, entry=bar.close, atr=atr,
                setting_at=setting.changed_at), True, "operator setting")
        if state.guide == "operator" and not state.exited and state.entry and state.atr:
            hit = _barrier(state, bar)
            if hit is not None:
                return Decision(replace(state, exposure=0.0, exited=True, exit_reason=hit),
                                True, hit)
        return Decision(state, False, "hold")

    # Trend 5: the algorithm guides, deciding once per UTC day and on arrival.
    day = bar.at.strftime("%Y-%m-%d")
    if new_setting or state.guide != "algorithm" or state.algorithm_day != day:
        target = ALGORITHM_EXPOSURE * signal
        fresh = State(guide="algorithm", exposure=target, trend=NEUTRAL,
                      source=setting.source, setting_at=setting.changed_at,
                      algorithm_day=day)
        return Decision(fresh, target != state.exposure or new_setting,
                        f"algorithm momentum {signal:+d}")
    return Decision(state, False, "hold")


def _barrier(state: State, bar: Bar) -> str | None:
    assert state.entry is not None and state.atr is not None
    side = 1 if state.exposure > 0 else -1
    stop = state.entry * (1 - side * STOP_ATR * state.atr)
    target = state.entry * (1 + side * TAKE_PROFIT_ATR * state.atr)
    worst = bar.low if side > 0 else bar.high
    best = bar.high if side > 0 else bar.low
    if (worst <= stop) if side > 0 else (worst >= stop):
        return "stop"
    if (best >= target) if side > 0 else (best <= target):
        return "take profit"
    return None


def exit_level(state: State, bar: Bar, reason: str) -> float:
    """Where a barrier exit executes, in index units: at the level, or at the
    bar's worst price when the bar opened through the stop."""
    assert state.entry is not None and state.atr is not None
    side = 1 if state.exposure > 0 else -1
    if reason == "take profit":
        return state.entry * (1 + side * TAKE_PROFIT_ATR * state.atr)
    stop = state.entry * (1 - side * STOP_ATR * state.atr)
    gapped = (bar.open <= stop) if side > 0 else (bar.open >= stop)
    return (bar.low if side > 0 else bar.high) if gapped else stop
