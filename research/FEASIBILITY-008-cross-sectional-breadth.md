# Feasibility 008 — A neutral book has a hundred bets; a long one has two

Measured 2026-09-26 on Binance perpetual closes: up to 229 assets, up to 729
days. **No rule was tested and no return hypothesis was read**, so no trial was
spent. This is a property of the panel.

## Why this, and why first

RESULT-006 closed cross-venue funding dispersion and said what to do next:
choose the following hypothesis against the **breadth constraint before its
story**. §0.4a fixes the detection threshold by calendar span, and breadth —
bets held at the same time that are genuinely uncorrelated — is the only fast
lever.

This measures the lever for an entire class of strategies at once, which is
worth knowing before one is written rather than after it fails.

## The numbers

| Assets | Days | T/N | **Long-only** | **Dollar-neutral** | Residual ρ |
|---|---|---|---|---|---|
| 20 | 729 | 36.5 | **1.8** | **19.1** | −0.046 |
| 40 | 729 | 18.2 | 1.8 | 39.0 | −0.022 |
| 100 | 729 | 7.3 | 1.8 | 99.8 | −0.008 |
| 150 | 549 | 3.7 | 2.0 | 150.1 | −0.003 |
| 229 | 207 | 0.9 | — | — | **T < N, not estimable** |

Two things, and the second is the finding.

**A long-only crypto book is about two bets however many assets it holds.** Mean
pairwise correlation +0.33; the first factor explains 38% of the variance. Buying
a hundred names is buying Bitcoin a hundred times, and the breadth does not move
when the count does — 1.8 at twenty assets and 1.8 at a hundred.

**A dollar-neutral book of residuals has breadth essentially equal to its asset
count.** After removing each asset's beta to the equal-weighted market, the mean
pairwise residual correlation is −0.046 at twenty assets and −0.003 at a hundred
and fifty. Holding a hundred names long and short against each other is a
hundred bets, not two.

Beta is near one for most of the panel — median 1.00, with 74% between 0.7 and
1.3 — which is why removing it recovers so much.

## What that buys

Per-bet Sharpe required to clear the threshold, at one trial:

| Years | Threshold | Long-only (1.8) | Neutral, 40 | Neutral, 100 | Neutral, 150 |
|---|---|---|---|---|---|
| 1.5 | 1.35 | **1.00** | 0.215 | 0.135 | 0.110 |
| 2.0 | 1.16 | 0.87 | 0.186 | 0.117 | **0.095** |
| 3.0 | 0.95 | 0.71 | 0.152 | 0.095 | 0.078 |

A long-only hypothesis needs a per-bet Sharpe near **1.0** over a year and a
half. Nothing in this project has come close, and nothing plausibly will: that
is a directional edge on the asset itself.

A neutral book of 150 needs **0.095** over two years. That is a small edge, and
small edges are the kind that exist.

The same arithmetic backwards, at a per-bet Sharpe of 0.10: 181 independent bets
at a year and a half, 136 at two years, 90 at three.

## Two ways this could be wrong

**The estimate needs more days than assets.** At 229 assets and 207 days the
covariance is rank-deficient and the residual correlation near zero could be the
deficiency rather than the market. The script refuses to estimate there and says
so. The figures above come from T/N between 3.7 and 36.5, and the result is flat
across that whole range, which is the reassurance — a number that only appeared
at the crowded end would be the crowding.

The first version of this script did produce a fictitious figure: it measured
the residuals with equal *positive* weights, and since the market factor is the
equal-weighted mean, the residual portfolio is zero by construction. Breadth came
back as 3.2 × 10³². The same shape as differencing BTCUSDT against itself and
reading four rows of zeroes as "nothing happened".

**Residual independence is not tradeable independence.** These residuals are
uncorrelated *unconditionally*. §13.1 says correlations converge toward one under
stress, and FINDING-004 is this project's record of what that costs: a pooled
breadth of 151 that ran 11 / 0 / 103 / 4 by quarter. **Nothing here has been
measured per regime**, and until it has, the 150 is an average that may span
zero. That check is the natural next step and it costs no trial either.

## What this licenses

Not a strategy. It says which *shape* of hypothesis can clear the bar at all:

* **Dollar-neutral and cross-sectional.** The arithmetic works.
* **Long-only or directional.** It does not, at any per-bet edge this project
  could honestly claim.

That is a real constraint on what to look for next, arrived at before a trial
was spent rather than after three were.
