"""BTC's trend, long or flat. research/PREREG-015, RESULT-015.

Long BTC while its daily close is above the average of the last 150 daily closes, in USDT
otherwise. Decided on each 00:00 UTC close, from closed days only. Never short. Pure: no
network, database or clock.
"""

from __future__ import annotations

import numpy as np

SMA_DAYS = 150


def long(daily_closes: np.ndarray) -> bool | None:
    """True long, False flat, None until 150 closed days exist."""
    if daily_closes.size < SMA_DAYS:
        return None
    return bool(daily_closes[-1] > daily_closes[-SMA_DAYS:].mean())
