# Pre-registration 019 — Adaptive Profit-Capacity Trading

Drafted 2026-09-29, before the simulator is written and before any price of the out-of-sample
span has been downloaded. Once committed it is binding.

## The operator's brief, with the capital rule settled

$10,000: **protected reserve $8,000, never used**; operating capital $2,000. **Operating
capacity grows only by realised profit, one dollar for one dollar, backed by real capital**,
up to $5,000; realised profit beyond that is harvested outside the book. Realised losses are
forbidden: a position sells only at or above its target. Universe **BTC and ETH**, spot
costs 0.10% + 0.015% slippage a side, priced on Binance USDⓈ-M 5-minute bars.

## Signals: the same for every variant

A **signal** is the first 5-minute bar of a UTC hour, per asset, where the 1-hour return is
positive, the 1-hour taker buy share is above 0.5 and the close is above its 24-hour average.
Every variant sees the same signals, prices and costs.

## The Signal Score, 0–100, from klines only

Open interest and funding do not exist before 2021-12 and are left out, so that the same
score runs on every span. Components: momentum 25 (1h > 0: 12.5; 4h > 0: 12.5), trend 20
(close > 24h average 10; regime bull 10, neutral 5), market 10 (BTC's 4h return > 0),
volatility 10 (24h realised volatility between 0.5× and 1.5× its 30-day median), liquidity 15
(quote-volume z against 24h > 1: 15; > 0: 8), microstructure 20 (1h taker buy share > 0.55:
20; > 0.5: 10). Penalties: risk −10 (24h volatility > 2× its median), exposure −20 × max(0,
utilisation − 0.5) / 0.5, aging −10 × the share of open positions older than 7 days.
Simultaneous signals are taken in score order.

## The eight variants, each adding one piece

| # | Adds | Rule |
|---|---|---|
| 1 | basic | $100 at every signal while $2,000 allows; target +1% net, sold on touch |
| 2 | Signal Score | enter only if score ≥ threshold (default 65) |
| 3 | Dynamic target | net target by score: < 75 → 1%, 75–85 → 1.75%, 85–90 → 2.5%; ≥ 90 → floor 1.5% then a 0.7% trail from the peak, never below the floor |
| 4 | Profit capacity | capacity = $2,000 + realised profit, ≤ $5,000; before this, realised profit is harvested |
| 5 | Adaptive sizing and utilisation | size $100 → $110 → $125 → $140 → $160 → $180 at realised profit $0 / 50 / 150 / 300 / 500 / 800; utilisation (locked / capacity) 70–80% ×0.75 size, 80–90% only score ≥ threshold + 15, > 90% no entries |
| 6 | Aging | a position older than 14 days sells at +0.3% net (never below); average open age > 14 days → threshold +10 |
| 7 | Cooldown | per asset after an entry: 60 min, 30 if score ≥ 85; ×(1 + k/3) with k the entries since the last realised profit; at most 6 entries a day |
| 8 | Full | regime and defensive mode, and an asset cap of 60% of capacity |

**Regimes** (variant 8), from BTC's daily closes: **bear** close ≤ SMA150 (threshold +15, size
×0.5, max utilisation 50%, cooldown ×2, target ×0.8); **high volatility** 30-day vol > 90%
(+10, ×0.7, 70%, ×1.5, ×1.2); **bull** vol < 60% (−5, ×1, 90%, ×1, ×1.2); **neutral** otherwise
(0, ×0.9, 85%, ×1, ×1). **Defensive mode** (variant 8) when, over the last 14 days, realised
profit per day is below half its 60-day mean, or locked > 60% of capacity, or the operating
book's marked drawdown > 10%, or the mean open age > 10 days: threshold +10, size ×0.7,
cooldown ×2, max utilisation 70%.

## Data, optimisation and the out-of-sample test

| Part | Span | Use |
|---|---|---|
| Training | 2021-11-01 → 2025-06-30 | the 27-point grid, full strategy |
| Validation | 2025-07-01 → 2026-09-24 | the 3 best of training; one chosen |
| **Out-of-sample** | **2019-10-01 → 2021-10-31** | never downloaded; the chosen one and the 8 variants, once |

The grid: threshold {55, 65, 75} × aging exit {7, 14, 30} days × target scale {0.75, 1.0, 1.25}.
The objective is **marked capital efficiency**: (realised profit + the unrealised result of
the positions open at the end) / average capital committed. Realised efficiency alone rewards
realising winners and carrying losers, and is reported beside it, not optimised.

**Robustness:** 40 seeds, each a path of the training span rebuilt from 30-day blocks of real
BTC and ETH bars drawn with replacement (both assets from the same block), 150 days of
warm-up included; every variant runs on every seed with default parameters. Reported: P5,
P25, median, P75 and P95 of final account, realised profit, drawdown, both efficiencies,
maximum locked capital, the longest span without a realised profit and recovery time.

## Metrics

Final account (reserve + operating cash + positions marked net of the exit cost + harvest),
realised profit, profit per trade, win rate (share closed; closed trades never lose), average
win, capital committed (maximum, mean), locked capital maximum, positions open, recovery time
(mean days held of trades that were more than 1% under), maximum drawdown of the account,
longest span without a realised profit, rotation (dollars bought / mean capacity), trades,
**realised and marked capital efficiency**; against holding $10,000 of BTC and against
RESULT-015's SMA 150 on $10,000.

## What must not happen

* No parameter or rule outside this text; the grid is the only optimisation.
* The out-of-sample span is downloaded and read once, after the chosen parameters are
  committed.

## Amendment, 2026-09-29 — the out-of-sample start

Written after the download and before any out-of-sample figure. data.binance.vision holds
USDⓈ-M 5-minute klines for BTCUSDT and ETHUSDT from **2019-12** only, and the score's
volatility component needs a 30-day median. The out-of-sample span is therefore
**2020-01-01 → 2021-10-31**. Nothing else changes.
