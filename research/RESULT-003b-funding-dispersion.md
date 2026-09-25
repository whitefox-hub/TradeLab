# Result 003b — Inconclusive, and two defects in the gate that nearly hid it

Re-run 2026-09-25 against `research/PREREG-003-funding-dispersion.md`
(`5024b5f`), on a corrected implementation carrying both legs. No rule changed,
so this is replication rather than a second trial.

## The numbers, once the trade being measured is the trade registered

| | Funding leg only (voided) | **Both legs** |
|---|---|---|
| Usable asset-venue pairs | 470 | 470 |
| Live window | 641 days | 640 days (1.75y) |
| Annualised return | +1.18% | **+1.04%** |
| Daily volatility | 0.0078% | **0.0574%** |
| Effective breadth | 80.4 | **151.4** of 470 |
| Annualised Sharpe | +7.93 | **+0.946** |
| Resolvable at 1 trial | 1.243 | 1.244 |

Adding the price leg multiplied volatility by 7.4 and cut the Sharpe by a factor
of eight. That is the correction RESULT-003 said was needed, and its size is why
that run had to be voided rather than adjusted.

**Verdict: inconclusive**, on two independent grounds either of which suffices.

## The gate said PASS, and it was wrong twice

**A positive result below the detection floor passed unchallenged.** The
observed Sharpe was 0.0495 per observation against a floor of 0.0651 that the
same call had just computed. §8.8's underpowered check qualified *negative*
results only, on the reasoning that a study finding something clearly resolvable
had made a planning error rather than a discovery to discard. The reasoning
holds; what was missing is that nothing checked whether the positive result was
resolvable. It now returns inconclusive, placed after the chance-benchmark
comparison so that a powered search finding nothing still fails — that verdict
is the only one that can ever close a hypothesis and must not be softened.

**The deflated Sharpe was reading moments far outside its range.** The live
series has **skew +14.4 and kurtosis 308.5**. The deflation formula's skew term
*raises* confidence, so a series dominated by a few observations inflates its own
significance — backwards, and by enough to matter: under normal moments the same
Sharpe deflates to 0.894, under these to 0.965, crossing the 0.95 threshold.

## Why the moments are what they are

| | |
|---|---|
| Top 1 day | **66.6%** of total return |
| Top 5 days | 69.0% |
| Sharpe excluding the 5 largest days | **+0.631** |

One day out of 640 is two thirds of the result. That is not a strategy with a
Sharpe near one; it is a single dislocation with 639 days of noise around it.
Removing five days leaves 0.631, still under the 1.244 this span can resolve.

## What stands

The hypothesis is **not** refuted and **not** confirmed. What is now known:

* Breadth is real and it is the reason to keep looking. 151 effective bets from
  470 pairs, mean pairwise correlation +0.0094. This is the only §0.4 hypothesis
  that has passed the §0.4a test.
* The edge, if any, is roughly 1% a year on committed capital before the tail is
  understood — small, and concentrated in events rather than spread across days.
* 1.75 years resolves 1.244. Reaching 0.6 needs about seven years, and §0.4a is
  explicit that more venues do not substitute for more calendar.

What would change the answer is not another venue and not another parameter. It
is understanding the one day: whether concentrated dislocations are a repeatable
feature of cross-venue funding or a single episode this window happened to
contain. That is a different question and needs its own registration.

## What must not happen

No lookback sweep, no cadence sweep, no winsorising the tail to make the moments
behave. The last is the tempting one and it is the worst: the tail *is* the
result here, and trimming it to satisfy a normality assumption would be fitting
the data to the test.
