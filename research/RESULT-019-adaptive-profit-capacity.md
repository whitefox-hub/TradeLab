# Result 019 — Adaptive Profit-Capacity: safe, and it earns only when the market does

Run 2026-09-29 against research/PREREG-019-adaptive-profit-capacity.md (`349878c`, committed
before any code; the out-of-sample start amended to 2020-01 in `357a865`, where the archive
begins, before any out-of-sample figure). Parameters chosen on validation and committed in
`9bf3795` before the out-of-sample data were downloaded. Simulator `scripts/run_prereg_019.py`:
a truncation test and a price-bending test show no lookahead; no trade ever realised a loss.

## The account, $10,000 ($8,000 of it a reserve never used)

| Span | Signals | Full strategy (8) | Best variant | Hold BTC | BTC > SMA 150 |
|---|---|---|---|---|---|
| Training 2021-11 → 2025-06 | 30,513 | $9,767 | $9,767 (8) | $17,459 | $25,509 |
| Validation 2025-07 → 2026-09 | 9,797 | $9,711 | $9,759 (2) | $7,869 | $10,435 |
| **Out-of-sample 2020-01 → 2021-10** | 15,643 | **$12,746** | $15,439 (5) | **$85,097** | $52,004 |

Training and validation use default parameters; the out-of-sample row uses the chosen ones
(threshold 65, aging 14 days, target ×1.25).

## The variants, one piece at a time

| Variant | Training | Validation | Out-of-sample |
|---|---|---|---|
| 1 basic | $9,490 | $9,707 | $13,562 |
| 2 + score | $9,409 | $9,759 | $12,867 |
| 3 + dynamic target | $9,412 | $9,743 | $13,186 |
| 4 + profit capacity | $9,414 | $9,650 | $15,262 |
| 5 + adaptive sizing | $9,424 | $9,720 | **$15,439** |
| 6 + aging | $9,706 | $9,644 | $14,772 |
| 7 + cooldown | $9,753 | $9,694 | $14,069 |
| 8 full | **$9,767** | $9,711 | $12,746 |

* **In falling or sideways markets every variant loses** (training, validation), by 2 to 6%.
  The realised profit is always positive (+$210 to +$490), and the positions still open at the
  end carry more than that (−$580 to −$920).
* **In the rising market of 2020–21 every variant earns** (+24 to +54%), and far less than
  holding BTC (+751%).
* **Profit capacity and sizing** add the most when prices rise (variants 4–5); **aging,
  cooldown and the defensive mode** give some of it back there and lose less when prices
  fall. The full strategy has the narrowest spread and the smallest drawdown.

## Robustness: 40 bootstrap paths of the training span, default parameters

| Variant | Final P5 | P25 | **Median** | P75 | P95 | Drawdown median | Marked efficiency median |
|---|---|---|---|---|---|---|---|
| 1 | $8,712 | $9,033 | **$9,741** | $10,587 | $12,690 | −13.6% | −0.13 |
| 4 | $8,402 | $8,763 | **$9,323** | $10,402 | $13,949 | −18.7% | −0.17 |
| 5 | $8,694 | $9,062 | **$9,569** | $10,228 | $13,909 | −15.8% | −0.17 |
| 8 | $9,001 | $9,225 | **$9,651** | $10,050 | $12,420 | −12.8% | −0.17 |

**The median path loses for every variant.** The full strategy is the safest: the highest P5
($9,001) and the smallest drawdown, and a quarter of its paths end above $10,050. The longest
span without a realised profit is 420 days at the median (variant 8) and more than 1,200 at
P95; recovery takes about 21 days at the median.

## Overfitting

The best training point (marked efficiency +0.007, $10,010) fell to −0.21 ($9,625) on
validation. The three best training points all lost on validation. The grid found noise.

## What it means

* **It keeps its promises about risk.** The reserve is never touched, no loss is realised,
  capacity grows only from real profit, and the full strategy's worst drawdown was 9–11%.
* **It has no edge of its own.** Realised efficiency always looks good (0.2 to 1.9) because
  winners are closed and losers carried; marked efficiency, which counts the losers, is
  negative at the median. The profit→capacity loop compounds only when the market rises, and
  then holding BTC compounds far more.
* The conclusion of RESULT-011, 013, 014 and 018 stands: taking +1–3% on short-term signals
  without ever realising a loss converts the market's direction into small realised gains and
  large carried losses. Controls change how the loss is carried, not whether it exists.
