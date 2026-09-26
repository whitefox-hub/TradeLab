"""What the market trend does to a position, stated once and bounded. §20, §11.3.

The trend is the operator's reading of the news on a **1 to 10 scale where 5 is
neutral** — the same shape as the fear-and-greed index beside it, which runs 1
to 100 with the middle meaning nothing in particular. Five is what the operator
sets when nothing has happened, and it must therefore do nothing at all.

That last sentence is the whole reason this file was rewritten. The first
version treated magnitude as distance from zero, so a 5 tilted the book by a
quarter — the system leaning hard in a direction at the exact moment the
operator meant to say "I have heard nothing".

A trend is a view about the environment: a war ends, so the market should be
bid. It is not a view about any particular asset being mispriced, so it does not
belong in the signal — §11.3 is explicit that the tradeable quantity is the
residual, observed minus expected, and that news everyone can read is priced
before it can be acted on. What a view about the environment *can* legitimately
do is change how much of a position is taken.

So the trend is a **tilt on exposure**, and nothing else. It scales positions
that agree with it up and positions that disagree down. It never creates a
position, never changes a sign, and never trades on its own: a tilt applied to a
flat book is still flat.

**The ceiling is the point.** At either extreme the tilt is ±50%, so a maximal
reading moves a position by half and not by a multiple. An operator who could
turn a systematic book into a directional bet with one slider does not have a
systematic book, and the slider would be the strategy. Half is a number chosen
to bound the damage rather than because anything measured it, which is stated
here so it is not later read as a finding.

**Whether the tilt helps is untested.** PREREG-005 is the study that would say.
Until it reports, the tilt is applied in simulation against the same run without
it, so the comparison is the output rather than the assumption.
"""

from __future__ import annotations

import numpy as np

#: Exposure change at either end of the scale. Bounded on purpose; see above.
TILT_CEILING = 0.5

#: Nothing has happened. The value the operator holds when they have read no
#: news, and the one that must leave a position exactly as it was.
NEUTRAL = 5

#: The scale's ends.
LOWEST, HIGHEST = 1, 10


def strength(magnitude: int) -> float:
    """Signed tilt in [-TILT_CEILING, +TILT_CEILING]. Zero at NEUTRAL.

    Each side is normalised by its own reach. On a 1-10 scale with 5 neutral
    there are four steps below and five above, so dividing both by five would
    make 1 mean minus 40% while 10 meant plus 50% — the same "as extreme as it gets"
    reading, worth less when it is bearish. Nothing about the operator's
    intent is asymmetric, so the arithmetic should not be either.
    """
    value = min(HIGHEST, max(LOWEST, int(magnitude)))
    if value == NEUTRAL:
        return 0.0
    reach = (HIGHEST - NEUTRAL) if value > NEUTRAL else (NEUTRAL - LOWEST)
    return (value - NEUTRAL) / reach * TILT_CEILING


def direction(magnitude: int) -> str:
    """The word for a position on the scale.

    Derived, never stored. A stored direction beside a stored magnitude is two
    fields that can disagree, and the first version of this had exactly that
    problem — a NORMAL that had to be kept in step with a zero.
    """
    tilt = strength(magnitude)
    return "UP" if tilt > 0 else "DOWN" if tilt < 0 else "NEUTRAL"


#: §11.4 puts market state into discrete buckets rather than a continuous
#: interaction, because sample size forbids conditioning on a continuum. These
#: are the vendor's own bands, so a run can later be grouped by the state it ran
#: under without anyone having to agree on new boundaries.
#:
#: Read off 3156 published days rather than guessed. The vendor documents the
#: five names and not the cut points, and the first version of this file
#: invented 24/49/74 — which put 50 in "Greed" and had no "Neutral" band at all,
#: although the series has 401 days of one.
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


def exposure_multipliers(magnitude: int) -> tuple[float, float]:
    """(long, short) multipliers. NEUTRAL is the identity, (1.0, 1.0)."""
    tilt = strength(magnitude)
    return 1.0 + tilt, 1.0 - tilt


def apply(position: np.ndarray, magnitude: int) -> np.ndarray:
    """Scale a position series by the trend, without changing any sign.

    Longs and shorts are scaled separately rather than the whole series by one
    factor: a single multiplier would move a short *further* into the trend it
    disagrees with, which is the opposite of what the operator asked for and
    would be invisible in any aggregate.
    """
    long_multiplier, short_multiplier = exposure_multipliers(magnitude)
    out = np.asarray(position, dtype=np.float64).copy()
    out[out > 0] *= long_multiplier
    out[out < 0] *= short_multiplier
    return out
