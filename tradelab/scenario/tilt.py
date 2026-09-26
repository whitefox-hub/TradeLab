"""What the market trend does to a position, stated once and bounded. Plan.md §20, §11.3.

The market trend is a view about the environment: a war ends, so the market should be
bid. It is not a view about any particular asset being mispriced, so it does not
belong in the signal — §11.3 is explicit that the tradeable quantity is the
residual, observed minus expected, and that news everyone can read is priced
before it can be acted on. What a view about the environment *can* legitimately
do is change how much of a position is taken.

So the market trend is a **tilt on exposure**, and nothing else. It scales positions that
agree with it up and positions that disagree down. It never creates a position,
never changes a sign, and never trades on its own: a tilt applied to a flat book
is still flat.

**The ceiling is the point.** At the extreme the tilt is ±50%, so a maximal market trend
moves a position by half and not by a multiple. An operator who could turn a
systematic book into a directional bet with one slider does not have a
systematic book, and the slider would be the strategy. Half is a number chosen
to bound the damage rather than because anything measured it, which is stated
here so it is not later read as a finding.

**Whether the tilt helps is untested.** Nothing in this repository has measured
whether an operator's read of the environment improves a return. It is applied
in simulation, against the same run without it, so the comparison is the output
rather than the assumption.
"""

from __future__ import annotations

import numpy as np

#: Exposure change at magnitude 10. Bounded on purpose; see the module note.
TILT_CEILING = 0.5

#: §11.4 puts market state into discrete buckets rather than a continuous
#: interaction, because sample size forbids conditioning on a continuum. These
#: are the vendor's own bands, so a run can later be grouped by the state it ran
#: under without anyone having to agree on new boundaries.
#:
#: Read off 3156 published days rather than guessed. The vendor documents the
#: five names and not the cut points, and the first version of this file invented
#: 24/49/74 — which put 50 in "Greed" and had no "Neutral" band at all, although
#: the series has 401 days of one.
SENTIMENT_BUCKETS = (
    (25, "Extreme Fear"), (46, "Fear"), (54, "Neutral"), (75, "Greed"),
    (100, "Extreme Greed"),
)


def sentiment_bucket(sentiment: int) -> str:
    """The band a 1-100 sentiment falls in.

    Recorded with a run and **not applied to any position**. §11.4 has market
    state conditioning a model that does not exist yet, and applying a number
    whose method is not auditable would be inventing a market theory and then
    trading it. Recording the bucket costs nothing and means the runs are
    already grouped when there is something to condition.
    """
    for ceiling, name in SENTIMENT_BUCKETS:
        if sentiment <= ceiling:
            return name
    return SENTIMENT_BUCKETS[-1][1]


def exposure_multipliers(direction: str, magnitude: int) -> tuple[float, float]:
    """(long, short) multipliers for a market trend. NORMAL is the identity."""
    if direction == "NORMAL" or magnitude == 0:
        return 1.0, 1.0
    with_trend = 1.0 + (magnitude / 10.0) * TILT_CEILING
    against = 1.0 - (magnitude / 10.0) * TILT_CEILING
    return (with_trend, against) if direction == "UP" else (against, with_trend)


def apply(position: np.ndarray, direction: str, magnitude: int) -> np.ndarray:
    """Scale a position series by the market trend, without changing any sign.

    Longs and shorts are scaled separately rather than the whole series by one
    factor: a single multiplier would move a short *further* into the market trend it
    disagrees with, which is the opposite of what the operator asked for and
    would be invisible in any aggregate.
    """
    long_multiplier, short_multiplier = exposure_multipliers(direction, magnitude)
    out = np.asarray(position, dtype=np.float64).copy()
    out[out > 0] *= long_multiplier
    out[out < 0] *= short_multiplier
    return out
