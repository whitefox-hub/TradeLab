# Feasibility 006 — Breadth persists, so an ex-ante rule is possible

Measured 2026-09-26 on the PREREG-003 panel: 470 asset-venue pairs, 640 live
days. **No return was read**, so this costs no trial.

## The question this answers, and the one it does not

FINDING-004 left the open question: was the good quarter a **regime identifiable
in advance**? If it was, there is a strategy — hold the book when breadth is
high and stand flat when it is not. If it was not, the hypothesis is finished.

That question cannot be asked honestly in one step, because the quarterly
breakdown has already been published. Any threshold chosen knowing 2026Q1 was
the good quarter is a description, not a discovery.

It decomposes, though, and the first half is free:

1. **Does breadth persist?** A property of the covariance structure. Answerable
   without touching a return, so no trial is spent — and if the answer is no, no
   ex-ante rule can capture it and the registration that would follow is not
   worth its trial.
2. **Do returns condition on it?** That is the trial, and it is worth spending
   only if (1) passes.

This is (1).

## It persists, strongly

Non-overlapping windows, so consecutive values are not the same days twice.

| Window | Windows | Breadth range | Pearson | Spearman | Above median now → above median next |
|---|---|---|---|---|---|
| **30d** | 21 | 49 – 492 | **+0.919** | **+0.913** | **7/9 = 78%** |
| 15d | 42 | 40 – 835 | +0.770 | +0.877 | 17/20 = 85% |
| 60d | 10 | 60 – 448 | +0.866 | +0.783 | 3/4 = 75% |

The 30-day window was declared before running; 15 and 60 are a stated
sensitivity and do not decide. All three agree, which is the useful part — a
result that held only at one window length would be the window and not the
panel.

The hit rate matters more than the correlation. A correlation can be strongly
positive while the rule a trader would actually write is a coin toss, because
the rule asks a binary question about a threshold and the correlation does not.
Here both say the same thing.

**So an ex-ante rule is possible.** Whether it is profitable is the next
question and a different one.

## A correction to FINDING-004

That document reported effective breadth as 151 pooled against 11 / 0 / 103 / 4
by quarter. **Those are two different estimators**, and presenting them in one
argument was wrong:

| Estimator | Whole panel | What it measures |
|---|---|---|
| Eigenvalue participation ratio | **151.4** | Effective dimensionality of the correlation matrix |
| Variance ratio σ̄²/σ_p² | **355.7** | Diversification actually realised in the equal-weighted book |

The quarterly figures were variance ratios; the pooled 151 was the eigenvalue
one. The comparison in FINDING-004 — "the 151 I reported is an average across
regimes that spans 0 to 103" — is therefore comparing two quantities as though
they were one.

**The finding survives the correction.** Both estimators agree that breadth
varies enormously across the window, and the quarterly figures were internally
consistent with each other, which is what the argument rested on. What changes
is that the pooled number should have been 355.7, and the sentence about a 151
spanning 0 to 103 was comparing apples to a different fruit.

The variance ratio is the one used from here on, because it is what a book
actually earns: the eigenvalue measure counts directions in a correlation matrix
and says nothing about whether an equal-weighted position captures them.

## What this licenses

A registration, and only that. The rule it would test must still be fixed before
it is run, and the threshold problem is unchanged: a number chosen knowing which
quarter was good is fitted. The way through is a rule with no chosen number in
it, which is what PREREG-006 has to solve.
