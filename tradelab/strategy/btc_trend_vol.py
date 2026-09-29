"""BTC's 120-day trend, sized by volatility. research/PREREG-016, RESULT-016.

Long while the daily close is above the average of the last 120 daily closes, flat in USDT
otherwise. When long, the size is min(1, 50% / the annualised volatility of the last 30
daily log returns), and it is changed only when the new size differs from the one held by
more than 10 points. Pure: no network, database or clock.
"""

from __future__ import annotations

import numpy as np

SMA_DAYS = 120
VOL_TARGET = 0.50
VOL_DAYS = 30
RESIZE = 0.10


def long(daily_closes: np.ndarray) -> bool | None:
    """True long, False flat, None until 120 closed days exist."""
    if daily_closes.size < SMA_DAYS:
        return None
    return bool(daily_closes[-1] > daily_closes[-SMA_DAYS:].mean())


def target(daily_closes: np.ndarray) -> float | None:
    """The size wanted after the last close, 0 to 1 of a full position."""
    signal = long(daily_closes)
    if signal is None or daily_closes.size < VOL_DAYS + 1:
        return None
    if not signal:
        return 0.0
    returns = np.diff(np.log(daily_closes[-VOL_DAYS - 1:]))
    vol = returns.std(ddof=1) * np.sqrt(365)
    return float(min(1.0, VOL_TARGET / vol))


def resize(wanted: float, held: float) -> float:
    """The size to hold: `wanted` on entry, exit or a move of more than RESIZE."""
    if wanted == 0 or held == 0 or abs(wanted - held) > RESIZE:
        return wanted
    return held
