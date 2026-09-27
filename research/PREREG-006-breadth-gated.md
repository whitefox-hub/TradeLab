# Pre-registration 006 — Hold the book only when breadth is there

Registered 2026-09-26, after FEASIBILITY-006 and before any return has been read
under this rule. One trial. The **third** on this panel, so the bar moves again
and the arithmetic is below.

## Why this one, and why it is the last of this line

RESULT-004 closed the sizing question: inverse-volatility sizing works gross and
dies on the trading it requires. FINDING-004 left one thing open — whether the
quarter that worked was a **regime identifiable in advance**.

FEASIBILITY-006 says an ex-ante rule is at least possible: breadth persists at
Pearson +0.919 across non-overlapping months, and the binary question a rule
would ask hits 7 of 9. That cost no trial because it read no return.

This is the trial. If it fails, cross-venue funding dispersion is finished as a
line of work: the hypothesis has had its search, its sizing and its regime, and
a fourth look at the same 640 days would be the same data answering the same
question more politely.

## The threshold problem, and the rule that has none

I have seen the quarterly breakdown. Any fixed threshold I choose — 50, 100 —
selects 2026Q1 because I know 2026Q1 is the answer. That is not a rule, it is a
label.

So the rule has **no chosen number in it**:

> Each day, compute effective breadth over the **trailing 30 days**. Hold the
> equal-weighted book if that figure is **above the expanding median of every
> breadth reading observed so far**. Otherwise stand flat.

Both constants are inherited rather than picked. The 30-day window is the one
declared in FEASIBILITY-006, before its result was known. The expanding median
is not a threshold at all: it is whatever half the history has been, recomputed
each day from data already past, so there is nothing in it to fit. It is also
exactly the statistic whose persistence was measured — "above median now, above
median next" — rather than a different one chosen afterwards.

Effective breadth is the **variance ratio** σ̄²/σ_p², per FEASIBILITY-006's
correction: what an equal-weighted book actually earns, not the eigenvalue count
of directions in a correlation matrix.

## Costs, charged because this is how the last one died

The rule turns the book on and off, and each flip is a full entry or exit across
every live pair:

> On each day the held/flat state changes, charge
> `per_side × 2 × live_pairs` — one side of each of two legs, per pair.

PREREG-004 was refuted on exactly this: sizing worked gross and the resize cost
was 167% of gross return. A gating rule trades less often than a sizing rule but
trades the **whole book** when it does, so the cost per flip is far larger. It
is registered before the run because a rule that is free to trade is a rule that
will look good.

The flip count is reported with the result whatever it says.

## The one configuration

| | |
|---|---|
| Panel | The PREREG-003 panel, unchanged: 470 asset-venue pairs, 640 live days |
| Position | The PREREG-003 position, unchanged: sign of the trailing 90-day funding spread, monthly, equal weight, both legs, half capital |
| Gate | Trailing 30-day variance-ratio breadth above its expanding median |
| Warm-up | The first 30 days build the first breadth reading and are not traded |
| Costs | PREREG-003's sign-flip costs, **plus** the gating flips above |

Nothing else changes. This is the same strategy with a switch on it, so any
difference is the switch.

## What counts as an answer, at three trials

The bar rises with the trial count, and this is the third on this panel:

| Trials | Resolvable Sharpe over ~1.5 years |
|---|---|
| 1 | 1.35 |
| 2 | 1.77 |
| **3** | **2.04** |

* **Gated Sharpe clearly above 2.04** → the regime is identifiable in advance
  and the hypothesis earns a simulator run.
* **Above the ungated result but below 2.04** → the gate helps and the span
  cannot prove it. The honest answer is more calendar, not a fourth look.
* **At or below the ungated result** → the regime was not identifiable in
  advance, and this line of work is closed. Reported as a refutation.

## Confounds, recorded in advance

* **Breadth is computed from the same returns the strategy earns.** A day of
  low realised breadth is often a day of large common moves, which is also a day
  with a large return. The gate therefore sees something correlated with the
  outcome by construction — it is causal, since only past days enter the
  trailing window, but it is not independent of it.
* **The expanding median moves.** Early in the window it is estimated from few
  readings and is noisy; late it is stable. The rule is therefore effectively
  stricter early on, and the held fraction by year is reported.
* **Survivorship and the five-venue filter** from PREREG-003 are unchanged and
  still apply.
* **Standing flat is not free of opportunity cost**, and this study does not
  price what the capital would have earned elsewhere. It is compared against the
  same strategy always-on, which is the right comparison for "does the gate
  help" and the wrong one for "should this capital be here at all".

## What must not happen

No threshold sweep — there is no threshold. No window sweep: 30 days is
inherited from FEASIBILITY-006, and 15 and 60 may be reported once as a declared
sensitivity with the decision resting on 30 whatever they say. No switching the
statistic from the median to a quantile after seeing the result, which is a
threshold arriving through the back door. And no fourth trial on this panel: at
four the bar is above what any cross-venue funding spread is going to clear, and
continuing would be spending trials to avoid a conclusion.
