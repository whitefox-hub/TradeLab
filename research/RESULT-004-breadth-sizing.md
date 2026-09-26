# Result 004 — Sizing from realised breadth

Run 2026-09-26 against research/PREREG-004-breadth-sizing.md, committed before
`scripts/run_prereg_004.py` existed. One configuration, no sweep.

| | |
|---|---|
| Panel | 470 asset-venue pairs, 640 live days |
| Evaluated | 2025-03-25 .. 2026-09-25 — 550 days, 1.51 years |
| Calibration | First 90 live days set σ_target, then excluded |
| Lookahead probe | **0** leverage values moved when day *t* was perturbed |

## The answer

| | Sharpe | Mean/day |
|---|---|---|
| Unsized (PREREG-003) | **+1.096** | +0.003518% |
| Sized, **gross** of resize cost | **+1.295** | +0.002546% |
| Sized, **net** — the registered rule | **−0.716** | −0.001717% |
| Resolvable at 2 trials | **1.766** | — |

**The registered rule is refuted.** It is refuted in-sample, on the data that
motivated it, by a rule chosen with full knowledge of where the problem sat —
which is the strongest form the asymmetric design admits.

Two separate failures, and they matter differently:

1. **Sizing works, slightly.** Gross of trading, scaling by inverse trailing
   volatility lifts the Sharpe from +1.096 to +1.295. The mechanism FINDING-004
   asked for is real and points the right way.
2. **It cannot pay for itself.** Resize cost is **167% of gross return** —
   149% excluding the one-off cost of opening the book. The rule spends about
   one and a half times what it earns on the trading required to earn it.

A gain of +0.20 in Sharpe does not buy 149% of the return.

## Where the cost goes: the rolling window's echo

The resize is usually tiny and occasionally enormous:

| median \|ΔL\| | mean \|ΔL\| | max \|ΔL\| |
|---|---|---|
| 0.0005 | 0.0164 | **1.829** |

**4.5% of days carry 75.1% of the total cost.** The five largest carry 52.4%.

The cause is not volatility clustering. It is the trailing window's trailing
edge. A 90-day rolling standard deviation drops an observation on day *t+91*
exactly as mechanically as it took it on day *t*, so every large return is paid
for **twice** — once when it enters the window and the book shrinks, once when
it leaves and the book re-expands. Neither trade carries information about day
*t+91*.

The confirmation is exact. The largest single day in the panel is 2025-11-07 at
**+1.21%**. It leaves the 90-day window on **2026-02-06**, and that day's resize
is **1.829** — the largest of all 640. A day already known to be 66.6% of the
whole window's return generates, a quarter later and for no reason connected to
anything happening then, the single most expensive trade in the study.

For this to break even, `per_side` would have to fall from 6.5bp to **4.4bp** —
costs 1.5× lower than the retail schedule. That is a venue-fee question, not a
research one.

## Quarterly, sized against unsized

Live pairs shown because it is the registered confound: more pairs mechanically
lowers portfolio volatility and so raises leverage, with no change in
correlation. The leverage column must be read against it.

| | Pairs | Median lev | Unsized | Sized | Unsz trim | Sz trim |
|---|---|---|---|---|---|---|
| 2025Q1 | 274 | 1.24 | −0.60 | −7.93 | +6.12 | −3.37 |
| 2025Q2 | 288 | 1.37 | +5.24 | +1.99 | +4.84 | +1.45 |
| 2025Q3 | 315 | 1.62 | +2.39 | −1.27 | +3.23 | −0.56 |
| 2025Q4 | 350 | **0.15** | +0.67 | −2.34 | −3.40 | −5.85 |
| 2026Q1 | 420 | 1.96 | **+10.22** | −0.16 | **+10.00** | +6.30 |
| 2026Q2 | 451 | 2.00 | +0.83 | −0.16 | +3.65 | +1.84 |
| 2026Q3 | 470 | 0.49 | +1.67 | +1.31 | +0.76 | +0.44 |

Leverage rises 1.24 → 2.00 across the window, and pairs rise 274 → 470 over the
same span. The registered confound fired: most of that leverage drift is the
panel getting wider, not correlation getting lower. The cap binds on 26.2% of
days, all of them late.

Sizing damages **every** quarter but the last. In 2026Q1 — the quarter worth
having — it converts +10.22 into −0.16.

## What this does and does not refute

**Refuted:** daily inverse-volatility sizing on a rolling window, at retail
costs. Decisively, and by mechanism rather than by a close number.

**Not refuted:** that breadth-aware sizing helps. Gross, it does. A banded rule
that resizes only on a material change would avoid most of the 4.5% of days
carrying 75% of the cost. That is a different rule and needs its own
registration — and choosing its band *now*, having seen which days were
expensive, is the search this project keeps catching itself at.

**Unchanged and more exposed:** the hypothesis itself. At two trials over 1.51
years the resolvable Sharpe is **1.766**, and **neither** series clears it — not
the sized −0.716, and not the unsized +1.096. Sizing was the lever FINDING-004
identified as the open question. It closed without moving the number past the
bar, and at zero cost it would still have reached only +1.295.

The gate returns **INCONCLUSIVE**, on skew −0.6 and kurtosis **57.7** — far
outside the range the deflated Sharpe is derived for. That is the correct
verdict and it is the same fat tail twice: the returns are dominated by a
handful of days, and so is the cost of reacting to them.
