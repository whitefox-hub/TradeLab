# Result 003 — Void. The study omitted the dominant risk term.

Run 2026-09-25 against `research/PREREG-003-funding-dispersion.md` (`5024b5f`).

**The PASS this study printed is not a result and must not be cited as one.**
The implementation measured the funding leg of a two-leg trade and left the price
leg out entirely, which is where essentially all of the risk lives.

## What it printed, and why none of it counts

| | First run | After two fixes |
|---|---|---|
| Usable asset-venue pairs | 470 | 470 |
| Live window | 52 days | 641 days (1.75y) |
| Annualised return | +1.22% | +1.18% |
| Effective breadth | 33.5 | 80.4 |
| **Annualised Sharpe** | **+40.7** | **+7.9** |
| Verdict | inconclusive | PASS |

A Sharpe of 40 is not a finding, it is a defect announcing itself. Two were
real bugs in my code and the third is worse, because it is not a bug.

**Costs were never charged.** The rebalance loop read:

    if target != position:
        flips += 1
        position = target        # reassigned here
    ...
    if target != position:       # therefore always False
        daily[start] -= cost

The second comparison can never be true, because the first branch has already
made them equal. 1,570 sign flips were counted and none was paid for. This is
the same shape as the gross-for-net defect in `run_study`: a variable reassigned
between a test and its use, with the documentation describing the intent rather
than the behaviour.

**Every pair was truncated to the shortest one.** Intersecting the day set
across all 470 pairs collapsed a 730-day window to 142 days and left a 52-day
live window. One pair with a short history was deciding the sample for all of
them.

## The part that is not a bug

The trade is long a perpetual on one venue and short the same perpetual on
another. Its P&L is the funding differential **plus the divergence between the
two venues' prices**, and the second term was not in the model at all.

Measured directly on 29 assets over a year of daily closes:

| | |
|---|---|
| Daily volatility of cross-venue basis | **0.134%** |
| Daily funding spread being collected | **0.0032%** |
| Ratio | **42×** |

The omitted risk is forty-two times the size of the collected return, per pair
per day. A Sharpe computed without it is not an overstatement, it is a different
quantity.

## Whether the hypothesis survives it

Breadth is the question, and unlike PREREG-001 the answer is not obviously no.
Basis divergences turn out to be nearly as uncorrelated across assets as the
funding spreads are — mean pairwise correlation +0.013 against funding's +0.032,
effective breadth 30 of 38 measured.

Diversified across the panel, portfolio basis volatility falls to 0.023% daily
against the 0.0032% daily return, which puts a **rough estimate around 2.7**
rather than 7.9.

That number is an envelope calculation on 38 assets, not the registered study
rerun, and it is recorded here as a reason to continue rather than as a finding.
It still omits liquidation risk on either leg, execution slippage on four legs,
and the funding paid on the hedge itself.

## What has to happen

The panel needs **daily closes per venue alongside the funding**, and the study
re-run with both legs. That is a collection job of the same shape as the one
already written, roughly 3MB more.

Re-running is replication of one fixed configuration on a corrected
implementation, not a second trial. Nothing in PREREG-003's rules changes: same
90-day lookback, same monthly cadence, same sign rule, same panel. If any of
those moved, it would be a second trial and the threshold would rise with it.

The registration stands. What failed was the code beneath it, and the reason the
failure was visible at all is that a Sharpe of 40 is not a number anyone can
bank.
