# Pre-registration 012 — The daily search: patterns that move more than they cost

Drafted 2026-09-29, before any result of the search exists. Once committed it is binding.

## Why

RESULT-011: at 5 minutes to 2 hours, no rule of 10,368 earned its costs. The moves were
the size of the fee. At a horizon of days a move is 5 to 20% and the fee ~0.13%, so a
pattern can win on direction alone. The operator's instruction: *find a winning pattern.*
The search follows PREREG-011's discipline: every rule counted, chosen on one span, checked
on the next, judged once on the unseen past.

Families already tried on the seen era, and counted here again: funding carry
(PREREG-003, 007) and the operator's trend (005, 009). New here: cross-sectional momentum
and reversal, time-series momentum across the universe, and the trend of BTC and ETH alone.

## The data

| Part | Span | Use |
|---|---|---|
| Discover | 2024-11-01 → 2025-10-31 | every rule scored; the top 20 kept |
| Validate | 2025-11-01 → 2026-09-24 | the 20 scored once; at most 3 go forward |
| **Unseen** | **2022-03-01 → 2024-09-24** | sealed; 10-day windows, below |

Daily closes and funding from the survivorship-neutral panels (`binance_pit`,
`binance_pit_2022`), the monthly top 100 by volume (point in time, `monthly_universe`). No
return from the unseen panel has been computed by any study.

## The rule space: 444 rules

Signals are known at a day's close (00:00 UTC) and the position is taken then, held to the
next close. A rule rebalances every H days, and between rebalances the weights drift with
prices.

| Family | Signal | Grid | Rules |
|---|---|---|---|
| Cross-section | past return over L ∈ {1, 3, 7, 14, 30} days, or mean funding over L ∈ {1, 3, 7} | top/bottom 10% or 20%; H ∈ {1, 3, 7}; follow or reverse; long-short, long only, short only | 288 |
| Time series, universe | sign of the L-day return, L ∈ {3, 7, 14, 30, 60} | H ∈ {1, 3, 7}; follow or reverse; long-short or long only | 60 |
| Majors | sign of BTC's or ETH's L-day return, L ∈ {3, 7, 14, 30, 60, 90} | H ∈ {1, 7}; follow or reverse; long-short or long-flat | 96 |

Gross exposure is 1: a long-short book is half long, half short. Costs: 0.065% of every
dollar traded (taker, half spread, slippage). Funding paid or received daily. An asset
whose price stops is exited at its last close, and pays the exit.

## How a rule is scored and chosen

Daily net return, Sharpe annualised on 365 days. The 20 highest discovery Sharpes go to
validation. Up to 3 go to the unseen era, and only if all hold on validation: Sharpe > 1.0,
mean daily return > 0. If none passes, the search ends **without a pattern**.

## The test on the unseen past: 10-day windows

Consecutive 10-day windows from 2022-03-01, each opened once. After each window, the rule
continues only if its **cumulative** net return since 2022-03-01 is positive. So the first
window must be favourable, as the operator asked, and one bad window after good ones does
not kill a strategy whose total is still ahead (a Sharpe of 2 loses in ~37% of 10-day
windows). The first failure ends the rule's sequence, and is reported.

**The verdict**, once, on the span reached: the Sharpe must exceed the resolvable threshold
for that span (1.65/√years; 1.05 over the full 2.6 years) and the deflation for the rules
taken to it. A rule that clears it is a candidate for a forward run under §15, not capital.

## What must not happen

* No rule or grid value added after the first discovery score.
* No look at the unseen era before the rules going to it are committed.
* No re-run of a rule on the unseen era after it fails.
