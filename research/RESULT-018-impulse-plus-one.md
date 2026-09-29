# Result 018 — "Impulso +1": it never sells at a loss, reaches +1% in hours, and earns about 5% in three years

Run 2026-09-29 with `scripts/run_prereg_018.py`, against research/PREREG-018-impulse-plus-one.md.
**Disclosed:** the registration was written before the simulator, and committed together with
it and this result, not before the run. No parameter differs from the text. A truncation test
(the first span cut at 2023-06-30) reproduces every trade closed before the cut exactly, so
no decision reads a later bar.

## The two spans, $10,000 each

| | 2021-11 → 2024-09 (with 2022) | 2024-10 → 2026-09 |
|---|---|---|
| **Account at the end** (marked) | **$10,559 (+5.6%)** | **$10,568 (+5.7%)** |
| Holding $10,000 of BTC | $10,459 (+4.6%) | $13,299 (+33%) |
| BTC above SMA 150 (RESULT-015) | **$18,630 (+86%)** | **$14,499 (+45%)** |
| Worst drawdown of the account | −13.4% (low $8,698, 2022-11-21) | −12.1% |
| Closed trades, all at ≥ +1% net | 146 (1.0 a week) | 146 (1.4 a week) |
| Realised profit | +$1,339 | +$1,353 |
| Median time to +1% | **12 hours** | **10 hours** |
| 90th percentile time to +1% | 12 days | 9 days |
| Longest held | **845 days** | 245 days |
| Worst move against a position | −77% | −64% |
| Positions still trapped at the end | 5: cost $3,225, worth $2,466 | 4: cost $2,948, worth $2,164 |
| Days new entries were blocked (locked > 30%) | 71 | 7 |
| Mean share of the account invested | 22% | 18% |

## What it means

* **It does what it was designed to do.** Every closed trade sold at +1% net or more, half of
  them within half a day. The capital rules held the damage: the account never fell more than
  13–14%, and new entries were blocked on 71 days in the first span, not paralysed.
* **It does not earn.** About **+$1,340 realised per span, and about −$780 carried in the
  positions that did not come back.** Net, +5.6% over 2.9 years and +5.7% over 2 years, on an
  account that is four-fifths cash on average.
* **The realised profits were spent on trapped positions.** Scale-ins and rescues are funded
  by the profits pool, and at the end of each span it held $15 and $4: the gains had gone into
  rescues and second tranches of positions that were then trapped.
* **The regime filter does not stop every trap.** The worst trade opened on 2021-11-08, when
  BTC was above its 150-day average, a day before the top; it waited 845 days.
* Against the simple rule: RESULT-015's BTC above SMA 150 made **16 to 15 times more** on the
  same $10,000 in the same spans. Frequent +1% gains are real, and they are small; the losses
  they avoid realising are carried, and they are large.

Both spans had been read by earlier studies, so this describes the design. It is not
evidence for it.
