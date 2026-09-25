# Pre-registration 003 — Cross-venue funding dispersion

Registered 2026-09-25, **while the two-year panel was still downloading**. The
collection script had fetched 16 of 244 assets when this was committed, so none
of the rules below can have been fitted to it.

## Why this one, and what the previous two taught

§0.4a establishes that the detection threshold is fixed by calendar span —
roughly 1.65/√years — and that the only fast lever is breadth, meaning
uncorrelated bets held *at the same time*. PREREG-001 failed that test badly: a
listing is held alone, so its breadth was one and the signal had to clear 0.62
unaided.

FEASIBILITY-003 measured this hypothesis against the same test: 60 assets, mean
pairwise spread correlation +0.0005, effective breadth 32, an information-ratio
multiplier of about 5.7. That is why this one is worth a trial.

## The selection rule, and why it is not the one I already saw

FEASIBILITY-003 found 31 of 60 assets with |t| > 2 on their mean spread. **That
threshold is not used here.** It is a number this project has already looked at,
and carrying it forward would be a search whose search step happened in a
document rather than in a loop.

The rule instead uses no threshold at all:

> For each asset and each rebalance date, take the **sign of the trailing
> 90-day mean spread**. Hold the spread in that direction. When the sign is
> unchanged, do nothing.

Ninety days is one calendar quarter, chosen for that reason and not because any
measurement suggested it. The 47-day split-half window from FEASIBILITY-003 is
deliberately avoided for the same reason as the t-threshold.

No asset is excluded for weak signal. Every asset in the panel is held every
period, equally weighted. A rule that drops the quiet assets is a rule fitted to
which assets were quiet.

## The one configuration

| | |
|---|---|
| Panel | Perpetuals listed on **all five** venues (availability, not performance) |
| Venue pair | Binance against each of Bybit, OKX, Gate, Bitget |
| Signal | Sign of the trailing 90-day mean daily spread |
| Rebalance | **Monthly**, and only where the sign changed |
| Weighting | Equal across asset-pairs |
| Costs | `BINANCE_RETAIL.per_side` × 4 per sign flip — two legs, opened and closed |
| Window | Two years ending 2026-09-25, the first 90 days consumed as warm-up |

## Capital, stated because it halves the answer

The trade commits margin on **two** venues at once. Return is therefore reported
on the **total capital committed across both legs**, which is roughly half the
return on one leg's notional.

That halving is not a footnote. It is the friction §0.4 names as the reason
institutions decline this trade — fragmented margin and reconciliation in several
places — and it is therefore the price of the structural advantage rather than an
objection to it. No transfer cost between venues is modelled: capital is assumed
pre-positioned on both, which is how it would actually be run, and rebalancing
capital between venues is a separate question this study does not answer.

## What counts as an answer

One trial. With roughly two years of span the threshold is about 1.17 for a
single bet, but breadth is the point: at an effective breadth near 32 a per-pair
Sharpe of 0.2 becomes a portfolio figure above 1.1.

* **Portfolio Sharpe clearly above the resolvable threshold, gate passed** → the
  hypothesis earns a simulator run, which is now built and waiting.
* **Inconclusive** → the effect is smaller than this span can see, and the answer
  is more calendar, not more venues. §0.4a is explicit that stacking does not
  help.
* **Negative** → reported as a refutation.

Recorded in advance, as PREREG-002's confounds should have been and were not:

* The five-venue panel is biased toward established assets. Every venue listing
  a name is itself a filter, and this one is applied five times.
* Bitget and OKX returned shorter histories than the others on the smoke test.
  Where a venue's history does not cover the window, that pair is simply absent —
  it is not filled, and the count of usable pairs is reported with the result.
* No survivorship correction is possible: all five universe endpoints return
  currently-listed instruments only. A delisted contract is missing from both
  legs simultaneously, so the spread is absent rather than biased in a
  direction, which is the better of the two failures but is still a hole.

## What must not happen

No threshold sweep, no lookback sweep, no rebalance-cadence sweep, no dropping
the venues that disagree. Each is a trial; at twelve trials two years of span
resolves only about 2.2, which no cross-venue funding spread is going to reach.
