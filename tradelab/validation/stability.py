"""Parameter surface stability. Plan.md §8.5.

The shape of the surface is the evidence, not its maximum. RSI 14 excellent with
13 and 15 disastrous is a spike, and a spike is what noise produces. RSI 12 to 18
all working is a plateau, and a plateau is what a mechanism produces.

There is a subtlety that changes how this is measured. **The maximum of a noisy
surface is biased upward by selection.** Even on a perfectly flat true surface,
the sampled peak is an outlier — so comparing the peak against its neighbours
always flatters the peak, and a ratio built that way is measuring the bias as
much as the shape.

The response is to stop reporting the peak at all. The **robust score** here is
the median of the neighbourhood, which needs no bias correction: a spike scores
poorly because its neighbours drag the median down, and a plateau scores well
because its neighbours agree with it. Promotion uses that number, so the
selection-biased maximum never reaches a verdict.

And the point analysed is the best *neighbourhood*, not the best point. Taking
the argmax of the raw surface and then describing its surroundings has a failure
its first version demonstrated: with near-ties, the argmax lands on an arbitrary
member of a plateau, and if that member happens to be a corner, most of its
neighbourhood lies outside and a genuine plateau is reported as a spike.
Maximising the smoothed surface instead lands in the middle of the plateau, which
is where a configuration should be chosen from anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product

import numpy as np


@dataclass(frozen=True, slots=True)
class StabilityResult:
    peak_score: float            # raw maximum anywhere on the surface
    peak_params: dict            # parameters of the robust optimum, not the raw one
    robust_score: float          # neighbourhood median: what should be promoted
    neighbour_ratio: float       # median(neighbours) / peak, bias caveat above
    plateau_fraction: float      # share of the neighbourhood holding >= threshold
    n_neighbours: int
    surface_median: float
    retention: float = 0.5

    @property
    def is_spike(self) -> bool:
        """Whether the best single point survives smoothing.

        Stated as `robust < retention x peak` rather than as a property of the
        chosen neighbourhood, because an isolated spike is erased entirely by the
        median smoother: the smoothed surface becomes uniformly low and the
        "best neighbourhood" is then arbitrary. Comparing the best neighbourhood
        against the best point asks the question directly — does the good result
        hold up when its surroundings are taken into account?
        """
        if self.peak_score <= 0:
            return True
        return self.robust_score < self.retention * self.peak_score


@dataclass(frozen=True, slots=True)
class Surface:
    """A parameter grid with a score per point."""

    axes: dict[str, list]        # ordered values per dimension
    scores: np.ndarray           # shaped by axis order

    @classmethod
    def build(cls, points: Sequence[tuple[dict, float]], keys: Sequence[str]) -> Surface:
        axes = {k: sorted({p[0][k] for p in points}) for k in keys}
        shape = tuple(len(axes[k]) for k in keys)
        scores = np.full(shape, np.nan)
        index = {k: {v: i for i, v in enumerate(axes[k])} for k in keys}
        for params, score in points:
            scores[tuple(index[k][params[k]] for k in keys)] = score
        return cls(axes, scores)

    @property
    def keys(self) -> list[str]:
        return list(self.axes)

    def params_at(self, position: tuple[int, ...]) -> dict:
        return {k: self.axes[k][i] for k, i in zip(self.keys, position, strict=True)}


def _neighbourhood(position: tuple[int, ...], shape: tuple[int, ...],
                   radius: int) -> list[tuple[int, ...]]:
    """Every grid point within `radius` steps along each axis, the centre aside."""
    ranges = [
        range(max(0, i - radius), min(dim, i + radius + 1))
        for i, dim in zip(position, shape, strict=True)
    ]
    return [p for p in product(*ranges) if p != position]


def smooth(surface: Surface, radius: int = 1) -> np.ndarray:
    """Neighbourhood median at every grid point, centre included."""
    out = np.full(surface.scores.shape, np.nan)
    for position in np.ndindex(*surface.scores.shape):
        values = [
            float(surface.scores[p])
            for p in [*_neighbourhood(position, surface.scores.shape, radius), position]
            if np.isfinite(surface.scores[p])
        ]
        if values:
            out[position] = float(np.median(values))
    return out


def analyse(surface: Surface, radius: int = 1, retention: float = 0.5) -> StabilityResult:
    """Find the best neighbourhood and describe it.

    `retention` is the share of the local optimum a neighbour must keep to count
    as part of the plateau. At 0.5, a neighbour holding half the score counts.
    """
    finite = np.isfinite(surface.scores)
    if not finite.any():
        raise ValueError("surface has no finite scores")

    # Maximise the SMOOTHED surface: the chosen point is the centre of the best
    # region rather than the luckiest single draw.
    smoothed = smooth(surface, radius)
    position = np.unravel_index(
        int(np.argmax(np.where(np.isfinite(smoothed), smoothed, -np.inf))), smoothed.shape
    )
    local = float(surface.scores[position])
    robust = float(smoothed[position])
    raw_peak = float(np.nanmax(surface.scores))

    neighbours = [
        float(surface.scores[p])
        for p in _neighbourhood(position, surface.scores.shape, radius)
        if np.isfinite(surface.scores[p])
    ]
    if not neighbours:
        return StabilityResult(raw_peak, surface.params_at(position), robust, 1.0, 1.0, 0,
                               float(np.nanmedian(surface.scores)), retention)

    arr = np.asarray(neighbours)
    reference = max(local, robust)
    threshold = reference * retention if reference > 0 else -np.inf
    return StabilityResult(
        peak_score=raw_peak,
        peak_params=surface.params_at(position),
        robust_score=robust,
        neighbour_ratio=float(np.median(arr) / reference) if reference != 0 else 0.0,
        plateau_fraction=float((arr >= threshold).mean()) if reference > 0 else 0.0,
        n_neighbours=arr.size,
        surface_median=float(np.nanmedian(surface.scores)),
        retention=retention,
    )


def plateau_width(surface: Surface, axis: str, retention: float = 0.5) -> int:
    """Contiguous points along one axis that hold `retention` of the peak.

    The number that answers "RSI 12 to 18 all work": a width of one is a spike,
    and a spike is what a search over noise reliably produces.
    """
    keys = surface.keys
    dim = keys.index(axis)
    # Anchored on the smoothed optimum in the OTHER dimensions, so the slice
    # taken is a representative one, but measured against the best score on that
    # slice. Using the smoothed value as the reference would make a spike look
    # infinitely wide, since every point clears half of a low number.
    smoothed = smooth(surface, radius=1)
    anchor = np.unravel_index(
        int(np.argmax(np.where(np.isfinite(smoothed), smoothed, -np.inf))), smoothed.shape
    )
    line = surface.scores[tuple(
        slice(None) if i == dim else anchor[i] for i in range(len(keys))
    )]
    if not np.isfinite(line).any():
        return 0
    centre = int(np.argmax(np.where(np.isfinite(line), line, -np.inf)))
    peak = float(line[centre])
    if peak <= 0:
        return 0
    threshold = peak * retention

    width = 1
    for step in (-1, 1):
        i = centre + step
        while 0 <= i < line.size and np.isfinite(line[i]) and line[i] >= threshold:
            width += 1
            i += step
    return width
