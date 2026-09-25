# Feasibility 003 — Cross-venue funding dispersion

Measured 2026-09-25. **No strategy return was computed.** This records properties
of the data that decide whether the hypothesis deserves a pre-registration, in
the order §0.4a says to check them. Nothing here is a result to trade on.

## Breadth, which §0.4a makes the first question

| | |
|---|---|
| Assets on ≥2 of 5 venues | 857 (244 on all five) |
| Sample measured | 60 assets, Binance vs Bybit, 95 usable days |
| Mean pairwise correlation of spreads | **+0.0005** |
| Effective breadth (participation ratio) | **32.3** |
| IR multiplier vs a single bet | **5.7×** |

This is the test PREREG-001 would have failed. A listing is held alone, so its
breadth is one and the signal had to clear 0.62 unaided. Here the spreads are
essentially uncorrelated, so a per-asset information coefficient that is far too
small to detect on its own becomes a portfolio effect roughly six times larger.

## Is the dispersion persistent, or just noise

| | |
|---|---|
| Mean absolute daily spread | 0.027% (9.9%/yr gross) |
| Spread autocorrelation, lag 1d | +0.269 |
| lag 3d / 7d / 30d | +0.065 / +0.028 / +0.021 |

Day-to-day persistence dies within three days, which rules out rebalancing on the
spread. That is not how this trade works: both legs are held and the differential
is collected every settlement, so what matters is a persistent **per-asset** bias
rather than a predictable daily one.

| | |
|---|---|
| Median per-asset mean spread | 0.0075%/day (**2.75%/yr**) |
| Assets with \|t\| > 2 on their mean | 31 of 60 (**25 after adjusting for autocorrelation**) |
| Expected at \|t\| > 2 by chance | 2.7 |
| First-half vs second-half rank correlation | **+0.386** (p = 0.0023) |
| Sign agreement between halves | **71.7%** |

The t-statistics are inflated by the lag-1 autocorrelation of 0.269, by a factor
of about 1.32; the counts above are given both ways. Even adjusted, 25 against an
expected 2.7 is not a marginal excess.

The split-half check is the one that matters: the per-asset bias measured on the
first 47 days predicts the next 48 with a rank correlation of +0.386 and agrees
on sign 72% of the time. The dispersion is not only present, it is predictable
from its own history.

## What this does not establish

* **95 days is not an evidence horizon.** §0.4a puts the resolvable annualised
  Sharpe at 1.65/√years, which is 3.2 over this window — useless for a verdict.
  Breadth is what makes the hypothesis viable anyway: at an effective 32, a
  per-asset Sharpe near 0.2 becomes a portfolio figure above 1.1, which is
  detectable over two to three years rather than fifteen.
* **Two venues, not eight.** The thesis in §0.4 is dispersion across many; this
  is one pair.
* **No cost was applied to any strategy.** Four legs round trip is 0.26%, against
  a median persistent bias of 0.0075%/day — break-even at roughly 35 days of
  holding, and the quartiles run from 17 to 73 days. Holding periods that long
  raise questions this measurement does not answer.
* **Margin is committed on both venues.** Return on deployed capital is roughly
  half the return on one leg's notional. That halving is exactly the friction
  §0.4 names as the reason institutions avoid this, so it is the cost of the
  structural advantage rather than an objection to it.

## What has to happen before a pre-registration

1. Extend history to two years or more on both venues, and add venues.
2. Fix the selection rule for which spreads to hold **before** measuring any
   return, since "assets with |t| > 2" is a threshold this feasibility pass has
   already seen and would be a hidden search if carried forward unchanged.
3. Decide the rebalancing cadence and the cost of moving margin between venues,
   which is not in the tier-A cost model.
