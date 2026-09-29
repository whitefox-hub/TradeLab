# Pre-registration 017 — Patterns in BTC's daily moves

Drafted 2026-09-29, before any BTC hourly price has been downloaded. Once committed it is
binding.

## Why

BTC moves more than 1% on 62% of days (up on 33%, down on 29%), and the median absolute
daily move is 1.5%. The operator's question: *is there a pattern in those moves?* A pattern
that pays must predict the **direction** better than chance, by more than the round trip
of 0.23% on spot. Three families never tested on BTC in this project: what the previous day
did, the day of the week, and the hour of the day.

**Disclosed:** BTC's daily closes 2018–2026 were read by PREREG-015 and 016 to compute
moving averages, not to examine these patterns. Hourly prices have never been read.

## Data

Binance spot BTCUSDT **hourly** klines from data.binance.vision, 2017-08-17 → 2026-09-28.
Daily closes are the 00:00 UTC hourly closes.

| Part | Span | Use |
|---|---|---|
| Discover | 2022-01-01 → 2024-09-24 | every rule scored |
| Validate | 2024-11-01 → 2026-09-24 | the top 10 of discovery, once |
| **Holdout** | **2018-06-01 → 2021-12-31** | up to 3 rules, once |

## The rules: 62

Each trade is taken at a close and closed at a later close, at market; cost 0.115% on
each side (0.10% fee, 0.015% slippage). The **regime** is BTC's daily close against its
150-day average at the last 00:00 UTC close (RESULT-015): up, down, or any.

| Family | Trade | Grid | Rules |
|---|---|---|---|
| Previous day | the next day, 00:00 → 00:00, long or short, when the previous day's return is in a bucket | buckets r < −3%, −3% ≤ r < −1%, r > 1%, r > 3%; regime any, up, down; long or short | 24 |
| Weekday | long the whole UTC day d | 7 days; regime any or up | 14 |
| Hour block | a 4-hour UTC block, every day, long or short | blocks 0–4, 4–8, …, 20–24; regime any or up; long or short | 24 |

## Score, choice and verdict

Daily net return (days without a trade are zero), Sharpe annualised on 365 days, mean net
per trade. The 10 highest discovery Sharpes are scored on validation. Up to 3 go to the
holdout: the highest discovery Sharpes among those with validation Sharpe > 0.5 and mean
net per trade > 0. None passing ends the study without a pattern.

**Verdict**, once per rule, on the holdout: a pattern if its Sharpe exceeds 1.65/√3.58 =
**0.87** and its mean net per trade is positive. Reported beside it: trades, share of
winners, mean gross move against the cost, and its figures against holding BTC.

## What must not happen

* No rule or bucket added after the first discovery score.
* No holdout price read before the three are committed.
* No re-run on the holdout.
