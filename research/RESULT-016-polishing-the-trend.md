# Result 016 — The rule was already polished; sizing by volatility makes it safer, not richer

Run 2026-09-29 against research/PREREG-016-polishing-the-trend.md (`0b08895`); the two
refinements sent to the holdout were committed in `0172f58`. Output: `research/prereg_016/`.
The engine reproduces PREREG-015's to 1e-10 on the base rule, and a perturbation test shows
no lookahead in any of the 50 rules (`run_prereg_016.py check`).

## Search on BTC, 2018-07 → 2026-09

Only 2 of 49 refinements beat the base's Sharpe of 1.307, both by less than 0.01, and both
are the base with **volatility-targeted size**. Bands of 1–3% and a two-day confirmation made
every rule worse. On BTC the vol-sized rule earns less (×27.7 against ×48.7) at the same
drawdown (−31% against −32%): in BTC it is the base at about 60% of the size.

## The holdout: twelve coins, 2019-01 → 2021-10

**Adopted: the 120-day rule with volatility-targeted size.** Its Sharpe beats the base's on
**10 of 12 coins**, median 1.19 against 0.91. The 1% band variant also passed (9 of 12, 1.19),
and the rule with the higher median is the one adopted.

| Coin | Hold: × / drawdown | Base: × / drawdown | **Vol-sized: × / drawdown** |
|---|---|---|---|
| ETH | 30.8 / −68% | 10.1 / −70% | 10.0 / **−39%** |
| BNB | 87.5 / −76% | 46.8 / −63% | 9.0 / **−42%** |
| NEO | 5.8 / −79% | 0.5 / −89% | 1.8 / **−49%** |
| LTC | 6.1 / −79% | 4.7 / −64% | 3.3 / **−33%** |
| QTUM | 7.1 / −82% | 3.3 / −79% | 3.5 / **−34%** |
| ADA | 46.7 / −76% | 29.7 / −51% | 6.3 / **−27%** |
| XRP | 3.1 / −72% | 0.7 / −75% | 1.9 / **−43%** |
| EOS | 1.8 / −79% | 0.6 / −84% | 1.4 / **−43%** |
| XLM | 3.3 / −78% | 2.7 / −64% | 2.1 / **−36%** |
| TRX | 5.3 / −78% | 1.6 / −68% | 2.4 / **−43%** |
| ETC | 10.3 / −73% | 3.3 / −85% | 3.6 / **−46%** |
| ICX | 9.7 / −78% | 3.8 / −77% | 4.1 / **−35%** |

## What it means

* **For BTC, the rule is already where refinement leaves it.** No band, confirmation or
  ensemble improved it; the only gain is to size it by volatility, which on BTC is the same
  as holding less of it.
* **Volatility sizing halves the worst drawdown on every coin** (−27 to −49% against −51 to
  −89% for the base) and raises the Sharpe on ten of twelve. It gives up return in a strong
  bull market: on BNB and ADA it made a sixth and a fifth of the base.
* **A caution on RESULT-015's reach.** On all twelve coins, over 2019–2021, **neither trend
  rule beat holding the coin in final value.** The altcoin bull of 2021 rewarded holding,
  and the trend rules were out for parts of it. RESULT-015's result is BTC's, over spans
  with long falls; it is not a law of crypto.
* By PREREG-016, the adopted rule may run forward only as a new book beside the others.
