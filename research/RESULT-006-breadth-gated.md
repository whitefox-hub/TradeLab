# Result 006 — The regime was not identifiable in advance

Run 2026-09-26 against research/PREREG-006-breadth-gated.md, committed before
`scripts/run_prereg_006.py` existed. One trial, the third on this panel.

| | |
|---|---|
| Evaluated | 610 days, 1.67 years |
| Held | **79.5%** of days |
| Gate flips | 5 |
| Lookahead probe | **0** decisions moved when the gated day was perturbed |

## The answer

| | Sharpe | Mean/day | Std/day |
|---|---|---|---|
| Ungated (PREREG-003) | **+1.075** | 0.003285% | 0.058390% |
| Gated, **gross** of flip cost | **+0.903** | 0.002740% | 0.057980% |
| Gated, net — the registered rule | **+0.541** | 0.001674% | 0.059203% |
| Resolvable at 3 trials | **1.934** | — | — |

**Refuted**, on the registered decision rule: at or below the ungated result
means the regime was not identifiable in advance.

And refuted twice over, which matters:

1. **It fails gross.** Before a single cost is charged, the gate takes 1.075 to
   0.903. It stood out of 125 days worth **+0.332%** of return while the
   standard deviation moved from 0.058390% to 0.057980% — three parts in a
   thousand. It gave up return and bought no reduction in risk.
2. **Then cost finishes it.** Five flips of the whole book cost 0.65%, which is
   **38.9% of the 1.671% the gate retained**. Fewer trades than PREREG-004's
   daily resizing, but each one moves everything.

So it would not work even if trading were free.

## Why: the filter and the risk are on different timescales

FEASIBILITY-006 measured breadth persistence at Pearson +0.919 across
non-overlapping months, with the binary question hitting 7 of 9. That was real
and it is not contradicted here. Breadth **is** predictable.

It is the wrong thing to predict.

The gate kept every one of the five largest days, including 2025-11-07 at
**+1.21%** — the single day that is 66.6% of the window's whole return. That is
not a failure of the gate; it is what the gate was for. But it means the
volatility the filter was supposed to avoid was *inside* the periods it held.

Breadth is a month-scale property of the covariance structure. The risk in this
strategy is a one-day event. **A month-scale filter cannot dodge a one-day
event**, and a filter that removes a fifth of the calendar without touching the
standard deviation has demonstrated exactly that.

This is why the two-step design was worth the trouble. FEASIBILITY-006 passing
did not imply this passing, and had the two been run as one study the
persistence result would have been read as support for a rule it does not
support.

## The gate returns INCONCLUSIVE, and that is also the answer

Skew **13.7**, kurtosis **287.0** — far outside the range a deflated Sharpe is
derived for. The series is dominated by a handful of observations.

That is the same fat tail RESULT-004 reported, and it is the finding underneath
both: this strategy's return and its risk are the same few days. Sizing could
not help because the days are unpredictable; gating cannot help because they sit
inside the regime you would want to hold.

## This line of work is closed

PREREG-006 registered that this was the last trial on this panel, and it should
be. Cross-venue funding dispersion has now had:

* **PREREG-003** — the search. Ran, and did not clear the threshold.
* **PREREG-004** — the sizing. Worked gross, died on 167% cost.
* **PREREG-006** — the regime. Fails gross, dies again on cost.

Three trials, and the bar is now 1.934 against a best observed figure of 1.075
that was itself below the two-trial bar. A fourth look at the same 640 days
would be the same data answering the same question more politely.

**What is not refuted** is §0.4a's arithmetic, which said this from the start:
the threshold is fixed by calendar span, and breadth is the only fast lever.
This hypothesis had 1.67 usable years and an effective breadth that collapses
exactly when it is needed. It was underpowered before it was wrong.

The next hypothesis should be chosen against that constraint first — **how many
uncorrelated bets can it hold at once, and for how long** — rather than against
how plausible its story is. FEASIBILITY-003 asked that question and got 32, and
the honest reading now is that 32 was not enough and the answer needed to be
larger by an order of magnitude.
