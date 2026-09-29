# Pre-registration 016 — Polishing BTC's trend rule, judged on other coins

Drafted 2026-09-29, before any price of the holdout coins has been downloaded. Once
committed it is binding.

## Why

The operator's instruction: *polish the strategy to the maximum.* Every span of BTC's
history has been read (PREREG-015, RESULT-015 and its addendum), so any refinement found
on BTC is fitted to BTC. A refinement that is real should carry to other coins whose
trends have the same character. This study searches on BTC and **judges on twelve other
coins**, in a span that no study of this family has read.

## The base

RESULT-015's addendum: long BTC while the daily close is above its 120-day average, flat
in USDT otherwise, decided at each 00:00 UTC close.

## The refinements: 50 rules, the base among them

| Dimension | Values |
|---|---|
| Average length L | 100, 120, 140 days |
| Band b | 0, 1, 2, 3%: go long when close > SMA·(1+b), go flat when close < SMA·(1−b), else keep |
| Confirmation k | 1 or 2 consecutive closes on the new side before switching |
| Sizing | **full** (all in when long) or **vol target**: min(1, 50% / the 30-day realised annualised volatility), resized only when it moves by more than 10 points |

3 × 4 × 2 × 2 = 48, plus the **ensemble**: the exposure is the share of the seven
averages 50, 75, 100, 125, 150, 175, 200 that the close is above, with either sizing (2).
Cost: 0.115% of every dollar traded. Cash earns nothing.

## Search, then the holdout

**Search on BTC**, spot daily, 2018-07-01 → 2026-09-24. Score: the daily Sharpe (365 days).
The **three highest Sharpes that beat the base's** go to the holdout. None beating it ends
the study and the base stays.

**Holdout: twelve coins**, Binance spot USDT pairs listed by mid-2018, daily closes
2019-01-01 → 2021-10-31 (warm-up from their listing): ETH, BNB, NEO, LTC, QTUM, ADA, XRP,
EOS, XLM, TRX, ETC, ICX. The base and each of the three are run on every coin.

A refinement is **adopted** if, on the holdout, its Sharpe beats the base's on **at least 8
of the 12 coins** and its median Sharpe across them is higher. If more than one is adopted,
the one with the highest median holdout Sharpe is the rule. Reported beside it: each coin's
final value and worst drawdown against the base and against holding the coin.

Survivorship: all twelve still trade; coins listed then that have since been delisted are
not in the set, which flatters holding more than it flatters a rule that can be flat.

## What must not happen

* No refinement or value added after the first BTC score.
* No holdout price read before the three are committed.
* An adopted refinement changes the forward books only as a new book beside them, judged
  on data after its start; the running books are not altered.
