# Result 011 — The Flash Strike search: 10,368 rules, none earns its costs

Run 2026-09-28 against research/PREREG-011-flash-strike-search.md (`a69c646`), with
`scripts/search_flash.py` on the bank of `scripts/build_flash_bank.py` (466 assets,
2024-09 → 2026-09, the already-seen era). Output: `research/search_011/`.

## The verdict, as registered

**The search ends without a strategy.** Not one of the 10,368 rules has a positive
Sharpe in discovery. The 20 best were scored on validation as registered, and all 20
lose there too. None meets the gates (Sharpe > 1, mean net > 0), so **nothing goes to
the unseen era, which stays sealed.**

| | Discovery, 2024-11 → 2025-10 |
|---|---|
| Rules with Sharpe > 0 | **0 of 10,368** |
| Best Sharpe | −1.06 |
| 99th percentile | −4.53 |
| Median | −18.9 |
| Best mean net per trade | **−0.03 bps** (top 0.5%, taker agrees, short only, 3 bars late, follow, 2 ATR stop, 3× target, 120 min; 47 trades/day) |

| The top 20 of discovery, on validation (2025-11 → 2026-09) | |
|---|---|
| Sharpe | −7.0 to −9.0 |
| Mean net per trade | −12.7 to −18.9 bps |
| Trades a day | 13 to 23 |

## What it means

* **The problem is costs, not a shortage of trades.** Every rule traded a lot, from
  several to about fifty times a day. The best is break-even *after* ~13 bps of costs
  per round trip (taker 0.065% in, maker 0.02% or taker 0.065% out). So before costs the
  best 5-minute shock trades gross at most about what they pay. The median rule loses
  ~15 bps a trade in every combination of delay and direction.
* **Following and fading are both negative.** No direction, delay, stop, target,
  horizon, side or filter tried turns it positive.
* The break-even rule is the best of 10,368. Its figure is the most optimistic reading
  the search produces, and after the selection it would be expected to lose.

## What follows, and what does not

* No wider grid on these data. That would be the second look that 011 was registered
  to prevent. A new idea is a new registration, counted on top of these trials.
* The unseen era (2022-12 → 2024-08) is untouched, and remains available for a
  hypothesis that has earned it.
* Diagnostics are not a strategy: a lower fee tier might move the best rule to a few
  bps a trade, but that is before the deflation for 10,368 trials.
