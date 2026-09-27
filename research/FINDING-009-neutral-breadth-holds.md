# Finding 009 — The neutral book's breadth does not collapse under stress

Measured 2026-09-26 on 100 Binance perpetuals, 729 days, beta removed on a
**trailing** 90-day window so the hedge is one a strategy could actually have
put on. No rule was tested and no return hypothesis was read; no trial spent.

## The question FEASIBILITY-008 left open

That document measured a dollar-neutral book at breadth ~100 of 100 and said
what was missing: it is an average, and §13.1 warns that correlations converge
toward one under stress. FINDING-004 is this project's record of what skipping
that check costs — a pooled breadth of 151 that ran 11 / 0 / 103 / 4 by quarter,
collapsing exactly when it was needed.

So the same measurement, period by period, and then on the stress days
themselves.

## Period by period, it holds

| | Periods | Median | Min | Max | Below half |
|---|---|---|---|---|---|
| Weekly | 91 | 100.5 | **92.2** | 115.1 | **0%** |
| Fortnightly | 45 | 100.6 | 93.4 | 109.7 | 0% |
| Monthly | 21 | 100.6 | 97.2 | 106.9 | 0% |

Not one week of two years falls below 92. The funding panel's equivalent figure
went to zero.

The full-sample beta gives the same answer (weekly median 100.5, min 88.3), so
the result is not resting on the lookahead that fitting beta over the whole
sample would be.

## On the worst days it is *higher*

| Days | n | Mean market move | Breadth |
|---|---|---|---|
| Worst 1% | 6 | **16.03%** | **108.6** |
| Worst 5% | 31 | 11.18% | 104.4 |
| Worst 10% | 63 | 8.94% | 104.2 |
| Calmest half | 320 | 1.04% | 100.5 |
| All | 639 | 2.85% | 100.9 |

The panel's worst day moved the market **−26.08%**. The neutral book moved
0.53%.

## Why, and why this is not a contradiction of §13.1

§13.1's convergence is about the **common factor**. On a day everything falls
26%, everything is correlated — and that is precisely the beta component, which
this book has removed. What is left is the residual dispersion, and it *widens*
on those days: 7.23% on the worst one against a typical 2.85%. Breadth is a
ratio of the average asset's variance to the portfolio's, and the numerator grew
faster than the denominator.

So the neutral book is not immune to stress because stress does not reach it. It
is immune because the thing that converges is the thing it does not hold.

**This is the opposite of what closed the funding hypothesis.** That book's bets
were spreads whose common driver was not removed, so when the driver moved they
all moved; RESULT-006 measured the consequence. A beta-neutral residual book has
no such driver left to move.

## Three ways this is still not a strategy

* **The portfolios are random.** Equal ±1/N weights drawn once and held. That is
  the best case for diversification, and a signal-driven book concentrates: it
  goes long the names the signal likes, which may be the names that move
  together. What is measured here is the breadth *available*, not the breadth
  any particular rule would realise.
* **Residual dispersion widening is not free.** Individual positions moved 7.23%
  on the worst day. A random book nets that out; a concentrated one does not,
  and the risk in a neutral book is not market exposure but a residual that
  blows up while you are on one side of it.
* **There is still no edge.** Breadth is the denominator of the problem, not the
  answer. FEASIBILITY-008's arithmetic stands: a neutral book of 100 needs a
  per-bet Sharpe of 0.117 over two years, and finding one is the work.

## What it settles

The shape is viable and it is stable. A dollar-neutral cross-sectional book on
this universe carries roughly its asset count in independent bets, in every week
of two years, and most so on the days that matter.

That was the one thing that could have ruled the shape out before a hypothesis
was written, and it did not. The next registration can be written against a
breadth of ~100 rather than against a hope of one.
