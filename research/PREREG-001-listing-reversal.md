# Pre-registration 001 — Post-listing reversal on new perpetuals

**Written before any listing return was computed.** The commit that adds this
file contains no results, and the commit that adds results does not modify it.
That ordering is the point: with a power budget of one trial, choosing the
hypothesis after seeing the data is a search with the search hidden.

Registered: 2026-09-25. Plan.md §0.4 hypothesis 2, §8.8.

## Why this hypothesis and not another

The tradable window starts **at** listing. A perpetual does not exist before it
is listed, so the announcement-to-listing drift — the obvious third candidate —
is not capturable on the instrument itself. That leaves direction after listing.

The mechanism argues for short:

* A new perpetual does not create new ownership of the asset, which already
  trades on spot. What it creates is **leveraged access**, and retail demand for
  leverage is asymmetrically long.
* Perpetual funding is persistently positive, which is the price of that
  asymmetry. Measured on BTCUSDT 2020-2026 in this repo: 0.0108% per eight
  hours, **11.8% a year paid by longs**. A brand-new listing is where that
  imbalance is largest, before any arbitrage capital has arrived.
* So the initial leveraged flow is directional and one-sided, and it decays as
  funding compounds against it.

A short therefore collects two things rather than one: the price reversal and
the funding. That is the whole reason this hypothesis is worth the single trial
rather than a momentum variant, which would pay funding instead of receiving it.

The honest counter-argument, recorded so it cannot be claimed afterwards as
foresight: shorting a fresh listing is a crowded and dangerous trade, short
squeezes on thin new contracts are violent, and the equity index-inclusion
literature finds a *positive* announcement effect. If the result is negative,
that literature is the reason and this registration says so in advance.

## The one configuration

Fixed now, and not to be adjusted afterwards:

| | |
|---|---|
| Direction | **Short** |
| Entry | Close of the **1st day after** the listing day |
| Holding | **7 days**, exited at close, no stop and no target |
| Weighting | **Equal** across listings; each listing is one observation |
| Costs | `BINANCE_RETAIL`, charged both sides |
| Funding | Actual funding series where available, received while short |

Entry is delayed one day deliberately. The listing day itself has unreliable
marks, extreme spreads and partial bars, and including it would make the result
a statement about first-print microstructure rather than about the hypothesis.

Seven days is chosen because §0.1's cost arithmetic puts the viable holding band
at 2-7 days, and the upper end of that band is where a single round trip's cost
is smallest relative to the move. It is not chosen because 7 looked best.

## Population

All futures listings in the announcement corpus, **excluding**:

* tokenised equities (`TRADIFI_PERPETUAL`) — different underlying, different
  holders, different news flow, and pooling them mixes two populations;
* listings whose asset was never extracted from the title (`needs_body`);
* listings with no retrievable price history covering entry and exit.

Every exclusion is a count reported with the result. A study that cannot say how
many observations it discarded, and why, has not reported its sample.

## What counts as an answer

One trial, so the entire sample is the test — there is no selection step and
therefore no in-sample window. At N≈545 and 76 events per year, this design
resolves an **annualised Sharpe of 0.59**. Read before running:

* Sharpe clearly above 0.59, gate passed → the hypothesis survives a first pass
  and earns a second, independent venue.
* Sharpe near zero → no effect of the size this sample can see. Not "no effect".
* Sharpe clearly negative → the reversal runs the other way, which is a finding
  about direction and is reported as one, not re-run as a long.

Re-running the mirror configuration after seeing a negative result would make
the trial count two and the resolvable Sharpe 0.80. If it is done, it is done
with that stated.
