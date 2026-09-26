# Pre-registration 004 — Sizing from realised breadth

Registered 2026-09-26, before any sizing rule has been run against the panel.

## What this study is, and the one thing it cannot do

FINDING-004 measured effective breadth on the PREREG-003 panel at 11 / 0 / 103 / 4
by quarter, against 151 pooled. Q2's portfolio volatility exceeded a single
asset's basis volatility — diversification running backwards. The open question
it left is sizing: position size cannot come from the nominal pair count, because
470 pairs is 103 bets in one quarter and effectively one in another.

**I have already seen all 640 days of this panel, including that quarterly
split.** No rule tested here is out of sample, and no amount of care about the
rule changes that. So the decision rule below is deliberately asymmetric:

* **A failure here refutes.** If a rule chosen with full knowledge of where the
  problem sits cannot fix the problem on the very data that revealed it, the
  hypothesis does not survive. In-sample failure is failure.
* **A success here confirms nothing.** It is the minimum bar — the result a
  fitted rule produces by construction. Confirmation needs forward data, which
  is calendar time this study cannot buy.

Stating that in advance is the point of registering it. The tempting version of
this study is to report a repaired Sharpe as though the repair had been
predicted.

## The rule, and why it is not a new parameter

> Each day *t*, scale the whole book by
> **L_t = clip( σ_target / σ̂_p,t , 0 , 2.0 )**,
> where σ̂_p,t is the realised standard deviation of the portfolio's daily net
> return over the **trailing 90 days ending at t−1**. σ_target is fixed as
> described in the amendment below.

Three constants, none of them chosen here:

| | Value | Where it comes from |
|---|---|---|
| Lookback | 90 days | The window PREREG-003 already fixed, for being one calendar quarter. Reusing it introduces no new degree of freedom. |
| Cap | 2.0× | `RiskLimits.max_gross_exposure`, which already governs every order the risk layer sees. Inventing a study-specific cap would be inventing a knob. |
| σ_target | Realised portfolio volatility of the **first 90 live days**, fixed thereafter | Self-normalising and causal: known before the evaluated window opens. Sharpe is invariant to it except through the cap, so it is a scale and not a result. See the amendment below. |

**This is breadth sizing, not a substitute for it.** With equal weights and a
common per-asset volatility σ̄, effective breadth is N_eff = σ̄²/σ_p², so
1/σ_p = √N_eff / σ̄. Scaling by the inverse of trailing portfolio volatility *is*
scaling by the square root of realised breadth, adjusted for how volatile the
individual legs happen to be. It reaches FINDING-004's recommendation without
estimating a 470×470 correlation matrix from 90 observations, which cannot be
done.

### Amendment, 2026-09-26, before any sizing run

The registered σ_target was 10% annualised. That is wrong for this book, and
FINDING-004 already contained the numbers that say so: portfolio volatility runs
0.010%–0.103% daily, which is 0.19%–1.97% annualised. A 10% target asks for
roughly fifty times leverage, so the 2.0× cap would bind on every day of every
quarter, leverage would be the constant 2.0, and the sized series would be the
unsized series times two — identical Sharpe, and a trial spent measuring
nothing.

The replacement is **self-normalising**: σ_target is the realised portfolio
volatility of the first 90 live days, fixed from then on, and the study is
evaluated from live day 91. Median leverage is then near 1.0 by construction and
the cap binds only where trailing volatility falls below half the opening
quarter's — which is what a cap is for.

Nothing was run to discover this. It uses only the quarterly volatilities
already published in FINDING-004, it is committed before the first sizing run,
and it costs about 90 days of evaluation window, which is stated here rather
than absorbed quietly.

## Costs, charged because the last two versions of this did not

PREREG-003 charged `per_side × 4` per sign flip, monthly. Vol targeting changes
the notional **every day**, and that traded notional is real:

> On each day, charge `per_side × 4 × |L_t − L_t−1|`.

Two legs, both resized, on each side of the change. This is registered
explicitly because the two defects that have cost this project the most were
both of this shape: a cost branch that could not fire (PREREG-003's flips) and a
gross figure read as a net one. A sizing rule that is free to trade is a rule
that will look good.

## What is reported, whatever it says

* Annualised Sharpe of the sized series, against the unsized series from
  RESULT-003b, and against the resolvable threshold at **two trials** — this is
  the second trial on this panel and the threshold moves accordingly.
* The same quarterly table as FINDING-004, sized. Q2's trimmed return is the
  specific number the rule exists to fix.
* The **leverage path**: median, and the fraction of days the cap binds. A
  result where the cap binds most days is a constant-leverage result wearing a
  vol-targeting label, and is reported as that.
* Total cost paid as a fraction of gross return.

## Confounds, recorded in advance

* **Pair count trends up** across the window, 264 live in Q1 to 463 in Q4. More
  pairs mechanically lowers portfolio volatility and therefore raises leverage,
  independent of any change in correlation. The leverage path will trend up for
  reasons that have nothing to do with breadth improving, and must be read that
  way.
* **The volatility estimate must end at t−1.** A window including day *t* sizes
  the position using the return it is about to earn. This is the leak the
  synthetic ordering tests exist to catch, and it is checked directly rather
  than assumed.
* **Trailing vol reacts late by construction.** A 90-day window carries a
  correlation spike for a quarter after it ends and misses the first weeks of
  one. That is a property of the rule, not a flaw to be tuned away.
* The panel's survivorship and five-venue selection holes from PREREG-003 are
  unchanged and still apply.

## What must not happen

No search over the lookback. The 30-day and 180-day windows are run **once** as
a declared sensitivity and reported in full, and the decision rests on 90
regardless of what they say. Picking the window that makes Q2 survivable is the
same search as before with the search step hidden in a document — which is the
error PREREG-003 was written to avoid, and the reason the FEASIBILITY-003
t-threshold was left unused.
