"""BTC's trend on a 120-day average: RESULT-015's addendum, judged forward only.

Long BTC while its daily close is above the average of the last 120 daily closes, in USDT
otherwise. Chosen from a scan of 20 to 300 days after every historical span had been seen,
so no historical figure is evidence for it. A separate module from `btc_trend` so the
150-day book keeps its code identity and its run.
"""

from __future__ import annotations

import numpy as np

SMA_DAYS = 120


def long(daily_closes: np.ndarray) -> bool | None:
    """True long, False flat, None until 120 closed days exist."""
    if daily_closes.size < SMA_DAYS:
        return None
    return bool(daily_closes[-1] > daily_closes[-SMA_DAYS:].mean())
