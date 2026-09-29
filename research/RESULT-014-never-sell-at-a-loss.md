# Result 014 — Never sell at a loss: on the unseen past, two years waiting and no gain

Run 2026-09-29 against research/PREREG-014-never-sell-at-a-loss.md (`3ffbe1b`), with
`scripts/run_prereg_014.py --era unseen`. Output: `research/prereg_014/unseen.json`.

## The verdict, as registered

**All three rules fail.** None has a Sharpe above the 1.00 the 2.7 years need, and two
end below the $10,000 they started with.

| Target | Account at 2024-09-24 | Daily Sharpe | Trades closed | Longest wait | Worst carried |
|---|---|---|---|---|---|
| +0.5% | **$9,633** | −0.37 | 81 | **791 days** | **−85%** |
| +1% | **$9,763** | −0.25 | 60 | 791 days | −74% |
| +2% | $10,175 | 0.19 | 43 | 789 days | −72% |

The account was below $10,000 at the end of 100 of 100 ten-day windows for the 0.5% rule,
99 of 100 for 1%, and 79 of 100 for 2%.

## What happened

The rule's premise is that the price comes back. In 2022 it came back two years later:

* **ETH long at $3,819, 2022-01-05**, the first days of the test: down 74% at worst,
  closed at its target on **2024-03-06**, 26 months later. For those 26 months its stake
  earned nothing and the rule could not trade ETH again.
* **BTC long at $44,158, 2022-04-06** (0.5% rule): down 64% at worst, closed 2023-12-05.
* **BTC short at $39,625, 2024-01-24** (0.5% rule): BTC then rose past $70,000. The short is
  **still open at −85%** when the span ends, and a short's loss has no ceiling.

One position per asset means one trapped trade stops that asset for as long as it waits.
Over 998 days the rules closed 43 to 81 trades, against 346 in the 693 days of the
seen era.

## What it means

* The seen era's +7 to +9% came from a market that returned quickly. When it did not, the
  small gains stopped, because the capital waiting in one loser was the capital the rule
  had.
* Not selling at a loss did not avoid the loss. It carried it, for up to 26 months, and
  at the end of the span one short carried −85% of its stake.
* No rule, target or size is changed after this. A variant is a new registration, and
  may only use data after 2024-09-24.
