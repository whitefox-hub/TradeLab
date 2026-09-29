# Pre-registration 015 — BTC's trend against holding BTC

Drafted 2026-09-29, before any BTC price from 2017-08 to 2021-10 has been read by this
project. Once committed it is binding.

## Why

The operator's instruction: *find a strategy that beats holding BTC in the hard period.*
Every rule of PREREG-014 lost to holding BTC over 2022–2024, because a position trapped in
the 2022 fall stopped the rule for up to two years. The one family built to beat holding
in a long fall is the one that is **out** of it: long BTC while its trend is up, in dollars
while it is down. Never short.

**Disclosed:** the family was chosen knowing, as everyone does, that BTC fell ~84% in
2018 and ~77% in 2022, and that trend rules sit out long falls. The price series of the
holdout have not been read. 2022–2024 has been seen (PREREG-014), so it is used for
discovery, not as evidence.

## Data

Binance **spot** BTCUSDT daily klines from data.binance.vision, 2017-08-17 → 2026-09-24:
the same prices across every span, no funding.

| Part | Span | Use |
|---|---|---|
| Discover | 2022-01-01 → 2024-09-24 | the hard period, already seen; every rule scored |
| Validate | 2024-11-01 → 2026-09-24 | the rules that beat holding in discovery, scored once |
| **Holdout** | **2018-04-01 → 2021-10-31** | never read; up to 3 rules, once |

The holdout starts after the 200-day warm-up of its first signal. It holds the 2018 fall,
the March 2020 crash and the 2021 rise.

## The rules: 24

Decided on a day's close from closes up to it, traded at that close. Long means all of
the capital in BTC, flat means all of it in USDT.

| Family | Long when | Values |
|---|---|---|
| Price over its average | close > SMA(n) | n ∈ {50, 100, 150, 200} days |
| Crossover | SMA(fast) > SMA(slow) | (20, 50), (50, 100), (50, 200), (100, 200) |
| Momentum | the n-day return > 0 | n ∈ {30, 60, 90, 180} |

Each checked **daily** or **weekly** (Mondays): 12 × 2 = 24 rules.

Cost: 0.10% fee plus 0.015% slippage on each switch. Holding BTC pays the same cost once,
on the first day.

## Choice and verdict

**Beats holding** means a higher account value at the span's end than buying BTC on its
first day and holding it.

* Discovery: every rule scored by its excess over holding.
* Validation: the rules that beat holding in discovery, ranked by that excess.
* Up to 3 go to the holdout: the highest discovery excesses among those that also beat
  holding on validation. None passing ends the study without a strategy.
* **Verdict, once per rule, on the holdout:** it beats holding BTC if its final value is
  higher. Reported beside it: the worst drawdown of each, the days in the market, the
  number of switches, and the daily Sharpe.

Beating holding in one span is one draw. A trend rule wins when a long fall is sat out and
loses in choppy rising markets; the holdout has both. The result is read that way.

## What must not happen

* No rule or value added after the first discovery score.
* No holdout price read before the rules going to it are committed.
* No re-run on the holdout.
