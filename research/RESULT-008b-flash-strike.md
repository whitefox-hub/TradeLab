# Result 008b — Flash Strike, replicated without the leak: the model helps, and it is not enough

Run 2026-09-28 against research/PREREG-008-flash-strike.md (`2615de8`), on the
pipeline of `117cd44`. The corrections that voided RESULT-008 came in `be25f65`
and `6aa732a`: the archive's five-minute metrics stamp, and auxiliary data
usable only from the next bar's close. No rule, feature, configuration or
threshold changed, so this is **replication, not a second trial**. It is the
result of record.

## The verdict, as registered

**INCONCLUSIVE, rule 2**: kurtosis **39.96**, above the 30 the deflated
Sharpe is derived for. The net Sharpe is **negative**.

| Rule | Measured | Required | |
|---|---|---|---|
| 1. Permuted-label null, 95th pct | −0.539 | below the result | passes |
| 1. PBO, 12 configurations | 0.429 | ≤ 0.5 | passes |
| 2. Skew / kurtosis | −1.41 / **39.96** | kurt ≤ 30 | **fails → inconclusive** |
| 3. Worst 1% of trades | −0.270% | ≥ −0.50% | would pass |
| 4–6. Net Sharpe | **−0.176** | > 1.313 | would be **refuted** under rule 6 |

The rules apply in order, and RESULT-007 is why that order is kept even when
it is inconvenient. Rule 2 stops at *inconclusive*. Had the moments been in
range, rule 6 would have refuted it. It should be read as the second.

## What the leak was worth

| | RESULT-008 (void) | **RESULT-008b** |
|---|---|---|
| Net OOS Sharpe | +0.244 | **−0.176** |
| Trades | 236 | 186 |
| Maker exits, mean net | +1.02% | +0.82% |
| Taker exits, mean net | −0.75% | −0.59% |
| Beat the permuted null | yes | yes |

**The whole of RESULT-008's positive result was the five-minute lookahead in
open interest.** With the reading taken when it is actually published, the
procedure loses money. This is the first time this project has measured a
leak's value directly: it is the difference between a small edge and a small
loss, and nothing but a comparison with live data would have found it.

## What still holds

* **The model still extracts something real.** The rules alone lose at a
  Sharpe of −28, and the model brings that to −0.18. It beats all 20
  permuted-label runs (best −0.42). The features carry information. It is not
  enough to pay the costs.
* **Adverse selection, unchanged.** Limits that did not fill moved **+1.20%**
  in the shock's direction over the next 15 minutes. Limits that filled moved
  −0.03%. Waiting for the pullback fills the moves that fail.
* **The queue calibration holds.** 99.75% of bar-model fills are confirmed
  against aggTrades after a second of latency.
* **The selection is unstable.** The chosen configuration still changes most
  months.

## Feature importance (MDA), the diagnostic RESULT-008 owed

Log-loss increase when each feature is shuffled, on the month each frozen model
traded (`scripts/mda_prereg_008.py`, `9234800`). It reproduces the run's
decisions exactly.

| Feature | Mean MDA | Top feature in |
|---|---|---|
| **shock_volume_z** | **+0.00782** | **15 / 19 months** |
| trade_size_z | +0.00197 | 3 / 19 |
| vol_ratio | +0.00125 | 0 |
| toptrader_change | +0.00106 | 0 |
| oi_change_signed | +0.00099 | 0 |
| shock_taker_share | +0.00075 | 0 |
| minutes_to_fill | +0.00067 | 0 |
| btc_return | +0.00047 | 1 / 19 |
| premium_change | +0.00034 | 0 |
| shock_atr | +0.00013 | 0 |
| funding_z | +0.00007 | 0 |
| oi_change | +0.00005 | 0 |
| forced_flow | **−0.00017** | 0 |

Spearman stability of the whole vector between consecutive months: median
**+0.22** (min −0.34, max +0.62).

**What the model learned is volume, and little else.** The shock's
participation is the top feature in fifteen months of nineteen, and it is four
times the next. The features the brief asked for add almost nothing once the
leak is gone: open interest, the forced-flow liquidation proxy and funding sit
at or below zero. The forced-flow proxy is the only feature that *helps* when
it is shuffled. The rest of the ranking changes month to month, which a median
correlation of 0.22 says plainly. One stable signal sits in a model otherwise
fitting what each month contained.

That is itself a result about the brief. At five minutes, and with readings
used only once published, **positioning data does not predict whether a
pullback trade reaches its target.** Volume does, somewhat, and not enough to
pay the costs.

## The forward simulation

The registration says the forward simulation runs unless the verdict is
*refuted*. The verdict is *inconclusive*, so by the letter it is owed. By its
substance, the procedure has a negative expected net return, and 180 forward
days at 0.32 trades a day (~58 trades) resolve a Sharpe of about 2.4 and
nothing smaller.

What a forward run could still show is whether the machinery behaves live as
modelled: fills, latency, publication lag. It cannot make this a strategy.

## What follows, and what does not

* No second look at these 574 days. That rules out no entry at the shock, no
  looser break-even and no dropping the persistence check, however much the
  adverse-selection finding invites the first of these.
* **Entering at the shock instead of on its retrace** is the hypothesis this
  line of work has earned. It is a new registration, judged forward only, and
  the second trial of the family.
* The two leak corrections are general. Any study built on `metrics` or
  premium data inherits them through `flash_data.assemble` and
  `intraday.METRICS_STAMP_LAG_MS`.
