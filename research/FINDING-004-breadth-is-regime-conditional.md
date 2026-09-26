# Finding 004 — Breadth collapses exactly when it is needed

Measured 2026-09-26 on the PREREG-003 panel: 470 asset-venue pairs, 640 days,
both legs.

## The number that changes the design

§0.4a established that the detection threshold is fixed by calendar span and
that **breadth is the only fast lever** — uncorrelated bets held at the same
time. FEASIBILITY-003 measured effective breadth at 32 and the full run at 151,
and that is what justified continuing with this hypothesis.

Split by quarter, the same panel gives:

| | Per-asset basis vol | Portfolio vol | Implied effective breadth | Pairs live |
|---|---|---|---|---|
| Q1 | 0.063% | 0.019% | **11** | 264 |
| Q2 | 0.068% | **0.103%** | **0** | 318 |
| Q3 | 0.100% | 0.010% | **103** | 406 |
| Q4 | 0.091% | 0.047% | **4** | 463 |

Q2's portfolio volatility is **higher** than the volatility of a single asset's
basis. That is not diversification failing to help; it is diversification
running backwards, and it can only happen if the pairs moved together.

So the 151 I reported is an average across regimes that spans 0 to 103. The
lever §0.4a identified as the only fast one is **not available in the regime
where it would matter**.

## This was predicted and I measured the wrong thing

§13.1 says it plainly: correlations converge toward 1 under stress, so the
number that matters for risk is the **crisis-regime** correlation and not the
average one. It says to compute correlation per regime as well as overall, and
`strategy_correlations` carries a `regime` column for exactly this.

I measured the pooled correlation, found +0.0094, and reported effective breadth
as though it were a property of the strategy. It is a property of the regime.

## What the return profile actually is

Trimming the most extreme 1% of days from each quarter:

| | Raw | Raw Sharpe | Trimmed | Trimmed Sharpe | Positive days |
|---|---|---|---|---|---|
| Q1 | +0.10% | +0.64 | +0.21% | +1.57 | 56.0% |
| Q2 | +0.35% | +0.41 | **−0.86%** | **−2.80** | 56.0% |
| Q3 | +0.86% | +10.54 | +0.83% | +10.45 | 71.1% |
| Q4 | +0.50% | +1.27 | +0.23% | +0.67 | 58.5% |

Two things. Q2's entire positive return was one day — trimmed, the quarter is
solidly negative. And Q3 keeps a Sharpe above 10 **after** trimming, on 406
pairs with 71% positive days. That is not a lucky tail; it is a steady
accumulation in a regime where the pairs really were independent.

The single largest day, 2025-11-07, was +1.21% and 66.6% of the whole window's
return. It was a gain. One observation says nothing about the sign of the next
one, and a strategy whose result depends on the sign of a rare event is not
sized, whichever way that one went.

## What this licenses, and what it does not

**Not** "the hypothesis is dead". The Q3 core is real and it is the thing worth
having: a small, steady, genuinely diversified return.

**Not** "the hypothesis is ready". A Sharpe of 10 in one quarter out of four,
with breadth near zero in another, is a strategy that is short a
correlation-spike option and does not know it.

What it changes is the sizing question, which was the open one. Position size
cannot be set from the nominal pair count — 470 pairs is 103 bets in Q3 and
effectively one in Q2. It has to be set from **realised** breadth, measured on a
trailing window, so the book shrinks as correlation rises rather than after it
has already cost something.

That is a different study from PREREG-003 and it needs its own registration,
with the sizing rule fixed before it is measured. The tempting version —
choosing the trailing window after seeing which one makes Q2 survivable — is
the same search with the search hidden.
