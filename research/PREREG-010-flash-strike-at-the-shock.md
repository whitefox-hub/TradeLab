# Pre-registration 010 — Flash Strike at the shock: do not wait for the pullback that selects against you

Drafted 2026-09-28. Once committed it is binding. It is judged **forward only**,
on data after its commit.

## Why

RESULT-008b measured the cost of waiting. Of the limits resting at the shock's
VWAP, the ones that **did not fill** had moved **+1.20%** in the shock's
direction fifteen minutes later. The ones that **filled** had moved −0.03%.
The pullback fills the moves that fail and misses the ones that keep going.
That is adverse selection, and the brief's "do not chase" is what paid it.

So this registration changes **one thing**: the entry. Everything else is
PREREG-008's, so any difference is the entry.

## The one change

| | PREREG-008 | **PREREG-010** |
|---|---|---|
| Entry | resting limit at the shock's VWAP, 30 min, trade-through fill | **market order at the shock bar's close**, placed 1 s after it |
| Entry cost | maker 0.02% | **taker 0.05% + 0.005% half spread + 0.01% slippage** |
| Persistence | taker share since the shock, and OI not fallen, at the fill | **taker buy share of the shock bar itself on the shock's side**. OI is not published until ~19 s after the close (`flash_data`), so it cannot be asked |
| Break-even p* | cost_win = 2 × maker, cost_loss = maker + taker | cost_win = **taker + maker**, cost_loss = **2 × taker** |

Unchanged: the universe (monthly top 100 by volume, survivorship-neutral), the
anomaly (top 1% of the asset's own ATR-scaled moves, 30-minute cooldown), the
thirteen features and the fixed LightGBM with isotonic calibration, the twelve
exit configurations and their purged-CV selection inside each training window,
the exits (take profit maker, stop and time taker, stop first, gaps at the
worst price), fixed risk of 0.25% per trade, ten open at most, one new entry
per bar, every §14.1 limit and `trim_held`. Premium and metrics are usable
only from the next bar's close.

The features are **not** re-chosen from RESULT-008b's MDA. Choosing them
because shock volume mattered in those 574 days would fit this study to that
data. The model is left to weigh them again.

## How it is judged

**Forward only.** The model for each month is trained on every candidate
determined before that month opens. That includes the historical archive,
2024-11 to 2026-09, rebuilt under this entry, and every forward month as it
completes. It then trades the month on live data in the **simulator**, on
$10,000 of fake capital. Only months that begin after this registration's
commit count. September 2026 counts for nothing.

**Beside it, PREREG-008's procedure runs forward too**, on the same live data.
Its registration owes a forward run because its verdict was not *refuted*, and
running both gives the comparison this study exists for: the same shocks, the
same model, the same exits, and two entries.

**Decision, at 180 forward days and not before**, on the net daily Sharpe of
PREREG-010, rules in PREREG-008's order: integrity (PBO over its 12
configurations and the rules-only baseline), then moments, then the tail at
two stops, then the resolvable threshold for the span run. Also reported:

* the paired difference, PREREG-010 minus PREREG-008, day by day. This is the
  entry's contribution;
* live against model: fill prices against the bar close, and the publication
  lag of each input, per day.

**Power, stated now.** At the ~0.3 to 0.6 trades a day this family produces,
180 days resolve a Sharpe near 2.4. This study will very likely end
*inconclusive* at 180 days unless the effect is large. The honest reading is
then *more calendar*, and the run continues. Money is not the claim here. The
claim is whether entering at the shock removes the adverse selection the
pullback paid.

## What must not happen

* No change to the model, features, exits or sizing after the first forward
  month. That is a new registration.
* No verdict before 180 forward days, and no stopping early on a good or bad
  month.
* No historical walk-forward of this entry. The 574 days have been seen, and
  a backtest of this rule on them would be the second look this project keeps
  refusing.
* No path to real capital that skips §15.
