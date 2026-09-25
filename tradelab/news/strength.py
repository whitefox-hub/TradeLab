"""Turning a 1-10 reading into a scoreable rating. Plan.md §20.5, §11.3.

The operator picks a strength out of ten and a direction. What is **stored** is
sigma of pre-event volatility, signed, because that is the only unit a rating
can be scored in.

The reason is worth keeping next to the mapping. §11.3 normalises every measured
reaction to sigma, so a rating in sigma can be checked against what actually
happened and the annotator's calibration becomes knowable. "8 out of 10 bullish"
cannot be checked against anything, and an input nobody can score is the one
nobody ever corrects — it drifts for years while looking like data.

So the scale is a *control*, not a unit. It exists because picking 7 is easier
than typing 3.5, and the dialog shows the sigma each step means so the operator
learns the unit rather than being shielded from it.

The steps are linear from a half sigma to five. A reaction under one sigma is
ordinary, two to three is a large move, and five is the kind of day people
remember — so ten points spread over that range put the common readings in the
lower half, which is where most events belong.
"""

from __future__ import annotations

#: Strength 1-10 to sigma of pre-event volatility.
SIGMA_BY_STRENGTH: dict[int, float] = {n: round(n * 0.5, 2) for n in range(1, 11)}

MIN_STRENGTH, MAX_STRENGTH = 1, 10


def to_sigma(strength: int, direction: int) -> float:
    """Signed sigma from a strength and a direction.

    Direction carries the sign so the operator never types a negative number,
    which is what the two controls are for. The stored value is still signed
    sigma, unchanged from what a typed rating produced.
    """
    if strength not in SIGMA_BY_STRENGTH:
        raise ValueError(
            f"strength must be {MIN_STRENGTH}-{MAX_STRENGTH}, got {strength!r}"
        )
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction must be -1, 0 or 1, got {direction!r}")
    return SIGMA_BY_STRENGTH[strength] * direction


def describe(strength: int) -> str:
    """What a step means, for the label beside it."""
    sigma = SIGMA_BY_STRENGTH[strength]
    if sigma <= 1.0:
        scale = "ordinary"
    elif sigma <= 2.5:
        scale = "large"
    elif sigma <= 4.0:
        scale = "very large"
    else:
        scale = "exceptional"
    return f"{strength} — {sigma:.1f} sigma, {scale}"
