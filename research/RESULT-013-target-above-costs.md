# Result 013 — A target above the costs wins 97% of the time, and loses money

Run 2026-09-29 against research/PREREG-013-target-above-costs.md (`3938131`), with
`scripts/search_hold.py`. Output: `research/search_013/`.

## The verdict, as registered

**No pattern.** None of the 96 rules has a positive Sharpe in discovery, the 20 best all
lose on validation, and none passes the gates. The unseen era stays sealed.

| Discovery, 2024-11 → 2025-10 | Whole universe | BTC and ETH |
|---|---|---|
| Trades a day | 182 | 4.1 |
| Highest share of winners | **97.6%** | **97.2%** |
| Best mean net per trade | −5.0 bps | −2.7 bps |
| Median mean net per trade | −10.6 bps | −8.1 bps |
| Best Sharpe | −1.89 | −0.64 |

On validation the top 20 won 64 to 97% of their trades and lost 11 to 30 bps a trade.

## What it means

* **The operator's target works as a target.** Most trades reach a gain above the costs,
  often within hours.
* **The capped losses pay for all of it and more.** A trade that wins 0.3% and one that
  loses 5 or 10% need 17 to 33 winners each to break even, and the loss cap is reached
  more often than that. Widening the cap raises the share of winners and makes each loss
  bigger; narrowing it does the reverse. Across the grid, neither side of that trade comes
  out ahead.
* This is the same answer as RESULT-011 by another route: after a 5-minute shock, the
  price has no direction that pays for the round trip. Choosing where to take profit
  moves the share of winners, not the expectation.
