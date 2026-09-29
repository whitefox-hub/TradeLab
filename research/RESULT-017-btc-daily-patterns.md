# Result 017 — BTC's daily moves: one weak pattern, positive three times, below the bar

Run 2026-09-29 against research/PREREG-017-btc-daily-patterns.md (`1be8536`); the check
found two outage days (2018-02-09/10) without hourly bars before any score, and no trade
may now enter or exit on a missing hour (`e3208e3`, before the search). The rule sent to the
holdout was committed in `ec40f6d`. Output: `research/prereg_017/`.

## Search and validation

Of 62 rules, 9 had a positive discovery Sharpe. **Every 4-hour block lost**, long or short:
their gross moves (−10 to +10 bps) are a tenth of the 23 bps round trip. The discovery
leader, *buy the day after a fall of more than 3% in an uptrend* (38 trades, 68% winners,
+104 bps net), lost 60 bps a trade on validation. One rule passed validation.

## The holdout, 2018-06 → 2021-12

**Long BTC through every UTC Wednesday while BTC is above its 150-day average.**

| | Discovery 2022–24 | Validation 2024–26 | **Holdout 2018–21** |
|---|---|---|---|
| Trades | 71 | 50 | 104 |
| Winners | 44% | 60% | 55% |
| Mean net per trade | +25 bps | +54 bps | **+48 bps** |
| Sharpe | 0.40 | 1.18 | **0.66** |

**Verdict: not a pattern.** The holdout Sharpe of 0.66 is below the 0.87 the 3.6 years need,
though the mean per trade is positive. Over the holdout it turned $1 into $1.52 while holding
BTC turned it into $6.17, because it is in the market one day in seven.

## What it means

* **The size of BTC's daily moves is not a pattern in their direction.** The hour of the
  day predicts nothing that pays; the previous day's move predicts nothing that survives a
  second span.
* The Wednesday rule was positive in all three spans, which is more than chance usually
  manages, and weaker than the bar. A calendar effect without a mechanism is the kind that
  appears in one sample and fades; it is reported, not traded.
