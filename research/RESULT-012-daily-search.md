# Result 012 — The daily search: one rule survives validation, and fails its first unseen window

Run 2026-09-29 against research/PREREG-012-daily-search.md (`c466e07`); the rule sent to
the unseen era was fixed in `5a32d59` before any unseen return was computed. Output:
`research/search_012/`.

## Discovery and validation (seen era)

| | |
|---|---|
| Rules | 444 |
| Positive Sharpe in discovery | 203 (46%) |
| Top 20 of discovery on validation | Sharpe −1.04 to 1.31 |
| **Passed validation** (Sharpe > 1, mean > 0) | **1** |

The top of discovery was almost all cross-sectional momentum: long the recent winners,
short the losers. Its discovery Sharpes of 2 to 2.4 fell to 0.2 to 1.0 on validation, and
the short-only books that led discovery lost 50 to 72% there. The one rule to pass:

**ETH, sign of the 30-day return, long or short, rebalanced daily.** Discovery Sharpe
1.59 (+151%), validation Sharpe 1.31 (+76%, drawdown −41%).

## The unseen era

| Window | Return | Cumulative |
|---|---|---|
| 2022-03-01 → 03-10 | **−10.8%** | **−10.8%** |

**The first window lost, so the sequence ends there, as registered.** No verdict threshold
is reached, and the rule is not a strategy.

## What it means

* Ten days of one asset's trend is a single draw. A rule with a true Sharpe of 1.3 loses
  in about 40% of 10-day windows, so this failure says little about the rule. It is the
  registered gate, and the gate is kept.
* What the search does say: the cross-sectional momentum that dominated discovery did
  not hold up on validation. The fall from ~2.3 to ~0.4 is what selection on noise looks
  like.
* No further look at the unseen era for these 444 rules. The remaining 2.5 years of it
  are unopened.
