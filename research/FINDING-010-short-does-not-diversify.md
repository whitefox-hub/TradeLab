# Finding 010 — Shorting everything is shorting Bitcoin, and the funding edge is a median

Measured 2026-09-26 on Binance perpetuals: 138,133 funding observations across
244 assets and 731 days, and 121 assets with complete price history. No rule was
tested and no return hypothesis was read; no trial spent.

## The sign does not diversify

| Book | Effective breadth |
|---|---|
| All 121 long | **1.82** |
| All 121 short | **1.82** |

Identical, and it has to be: breadth is a function of the correlation matrix,
and flipping every sign leaves every pairwise correlation exactly where it was.
Shorting a hundred names is shorting Bitcoin a hundred times, in the same way
that buying them is buying it a hundred times.

So a short-only hypothesis inherits FEASIBILITY-008's arithmetic unchanged: it
needs a per-bet Sharpe near **1.0** over eighteen months to clear the threshold.
Nothing in this project will have that, and a directional edge on the asset
itself is what it would be.

**What changes breadth is holding both sides at once**, not choosing the other
one. A dollar-neutral book of the same 121 names carries roughly its asset count
in independent bets — measured at ~100 of 100 in FEASIBILITY-008 and stable in
every week of two years in FINDING-009.

## The funding "edge" for shorts is a median, and the mean is negative

Perpetual funding is usually paid by longs to shorts, and it is here:

| | Daily | Annualised |
|---|---|---|
| **Median** | +0.000204 | **+7.44%** |
| **Mean** | −0.000369 | **−13.48%** |
| Positive | **71.0%** of observations | |
| 5th percentile | −0.001720 | −62.8% |
| 95th percentile | +0.000419 | +15.3% |

A short collects on 71% of days and the median day pays it 7.4% a year. **The
mean is minus 13.5%.**

Of 212 assets with long histories, only **45%** have a positive mean funding
rate, and the median asset's mean is **−1.58%** annualised.

That is the same shape this project keeps meeting: many small gains, occasional
large losses, a median and a mean that disagree in sign. RESULT-004 and
RESULT-006 both died of it, and the weekly basket in `simulate_weeks.py` has it
too — median −0.18%, mean −0.61%.

The mechanism is not mysterious. Funding spikes negative when shorts are being
squeezed, which is when a short position is already losing on price. The cash
flow and the mark-to-market move against the short **together**, which is the
worst possible correlation for a carry trade and the reason the median is the
wrong statistic to plan with.

## What this rules in and out

* **Short-only.** Ruled out on the same arithmetic as long-only, with the added
  problem that loss is unbounded and the funding carry is negative on average.
* **"Short to collect funding".** The carry exists at the median and not at the
  mean, and it arrives against you exactly when the position does.
* **Long/short, dollar-neutral.** Still the only shape whose breadth clears the
  bar, and now with a second reason: a neutral book is on both sides of the
  funding flow, so the spikes that ruin a short-only carry largely offset.

None of this is a strategy. It is a constraint on which ones are worth writing.
