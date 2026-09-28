"""What the market trend does to a position, stated once and bounded. §20, §11.3.

The trend is a 1-10 scale where 5 is neutral and must do nothing. It is a tilt on exposure
only (§11.3): scales agreeing positions up, disagreeing down; never creates a position or
changes a sign. Bounded at ±50% by choice, not measurement. Untested until PREREG-005.
"""

from __future__ import annotations

import numpy as np

#: Exposure change at either end of the scale.
TILT_CEILING = 0.5

#: Must leave a position exactly as it was.
NEUTRAL = 5

#: The scale's ends.
LOWEST, HIGHEST = 1, 10


def strength(magnitude: int) -> float:
    """Signed tilt in [-TILT_CEILING, +TILT_CEILING]. Zero at NEUTRAL.

    Each side is normalised by its own reach (4 below, 5 above), so both ends hit the ceiling.
    """
    value = min(HIGHEST, max(LOWEST, int(magnitude)))
    if value == NEUTRAL:
        return 0.0
    reach = (HIGHEST - NEUTRAL) if value > NEUTRAL else (NEUTRAL - LOWEST)
    return (value - NEUTRAL) / reach * TILT_CEILING


def direction(magnitude: int) -> str:
    """The word for a position on the scale. Derived, never stored."""
    tilt = strength(magnitude)
    return "UP" if tilt > 0 else "DOWN" if tilt < 0 else "NEUTRAL"


#: Discrete market-state buckets (§11.4): the vendor's bands, read off published days
#: (the vendor documents names, not cut points).
SENTIMENT_BUCKETS = (
    (25, "Extreme Fear"), (46, "Fear"), (54, "Neutral"), (75, "Greed"),
    (100, "Extreme Greed"),
)


def sentiment_bucket(sentiment: int) -> str:
    """The band a 1-100 sentiment falls in. Recorded with a run, never applied (§11.4)."""
    for ceiling, name in SENTIMENT_BUCKETS:
        if sentiment <= ceiling:
            return name
    return SENTIMENT_BUCKETS[-1][1]


def exposure_multipliers(magnitude: int) -> tuple[float, float]:
    """(long, short) multipliers. NEUTRAL is the identity, (1.0, 1.0)."""
    tilt = strength(magnitude)
    return 1.0 + tilt, 1.0 - tilt


def apply(position: np.ndarray, magnitude: int) -> np.ndarray:
    """Scale a position series by the trend, without changing any sign.

    Longs and shorts scale separately; one factor would push shorts further against the trend.
    """
    long_multiplier, short_multiplier = exposure_multipliers(magnitude)
    out = np.asarray(position, dtype=np.float64).copy()
    out[out > 0] *= long_multiplier
    out[out < 0] *= short_multiplier
    return out
