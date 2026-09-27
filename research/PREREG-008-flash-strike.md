# Pre-registration 008 — Flash Strike

Draft v3, 2026-09-27. Once committed, this registration is binding. It is fixed
before any intraday return is computed and before the intraday data set exists.

## The brief

> A "Hit & Run" strategy. Detect anomalous moves by combining **price, volume,
> open interest, funding and liquidations**. Do not chase: wait for a
> **pullback** and enter only if the imbalance persists. A small, fast move,
> with a **short take profit, a strict stop and a maximum holding time**.
> Thresholds and parameters **optimised automatically** by backtesting and
> simulation.

And the operator's rule for the design: **perfection and simplicity at once.**

## The rule this design follows

Every part earns its place in one of two ways. Either it is **something the
brief asks for**, or it is **a control against fooling ourselves**. Anything
that is neither is cut, however clever it is.

v1 was simple and not good enough: a grid can only AND its signals together.
v2 was powerful and carried parts that did not earn their place:

| Cut from v2 | Why it did not earn its place |
|---|---|
| Five-signal composite gate | The gate only proposes candidates. One clean definition of "anomalous" is enough, and the other signals do more as features the model weighs |
| Second playbook (exhaustion fade) | Not in the brief. It is a different hypothesis (§0.4 no. 4) and deserves its own registration, not a share of this one |
| Three entry levels | One principled level is enough: the price the shock itself paid on average |
| Order-book snapshots (`bookDepth`) | 30–40 GB for 30-second snapshots that miss spoofing and fleeting walls. Size is capped by traded volume instead |
| Partial take profit, break-even stop, invalidation and funding exits | Path-dependent rules, each with its own parameters. The brief asks for TP, stop and time, and nothing else |
| Kelly sizing from model probability | It makes size depend on calibration quality. A **fixed risk per trade** bounds the tail by construction, which is what RESULT-007 taught |
| Hyperparameter search (27 model configurations) | Conservative fixed hyperparameters. The automatic optimisation goes where the brief put it: the thresholds |
| Forty features | **Thirteen, one per mechanism.** A feature with no mechanism is a way to fit noise |

## Data

From data.binance.vision, none of it downloaded yet:

| Source | Resolution | Used for |
|---|---|---|
| `klines` | 5 m | price, quote volume, taker buy volume, trade count |
| `metrics` | 5 m | open interest, top-trader long/short ratio |
| `premiumIndexKlines` | 5 m | perp against index: leverage-driven pressure |
| `fundingRate` | per settlement | funding charged; its level as crowding |
| `aggTrades` | per trade | fill calibration only, on a sample of events |

**Window**: 2024-09-25 → 2026-09-25, survivorship-neutral (FINDING-011). Every
source is cut at the asset's `deliveryDate`.

**Universe, point in time**: on the first of each month, the 100 highest by
quote volume over the previous 30 days, among assets with at least 30 days of
history.

## The strategy

**1. Anomaly.** A closed 5-minute bar whose move `|close − open|` is at least
`k` × ATR(5 m, trailing 24 h). `k` is **not tuned**: it is the asset's trailing
30-day 99th percentile of that ratio, so "anomalous" means the top 1% of the
asset's own recent bars. After an event, nothing more on that asset for 30
minutes.

**2. No chase.** A resting limit, on the shock's side, at the **shock bar's
VWAP** (quote volume ÷ base volume), which is the average price paid during the
move. It lives for 30 minutes. It fills only if a later bar trades **through**
it by one tick.

**3. Persistence.** At the fill, taker buy share since the shock is still on
the shock's side (above ½ for an up move, below ½ for a down move), and open
interest has not fallen since the shock. Otherwise the order is cancelled.
These are the brief's "imbalance persists", taken literally, and they are the
only hand-set conditions in the strategy.

**4. Decision.** A model decides which of the surviving candidates to take
(below).

**5. Exits**, the first to trigger:
* **Take profit**: `TP` × stop distance, as a resting limit (maker).
* **Stop**: `SL` × ATR, as a stop-market (taker). A gap fills **at the bar's
  worst price**. When one bar touches both, the stop came first.
* **Time**: `H` minutes, at market.

**6. Size.** Fixed risk. Each trade is sized so that hitting the stop costs
**0.25% of equity**, capped at 1% of the trailing 1-hour average 5-minute quote
volume. At most **10** open positions, and at most **one new entry per
5-minute bar across the whole book**. A market-wide shock fires everywhere at
once and is one bet, not ten, and this rule treats it as one. Every §14.1 limit
applies, and `trim_held` runs on every bar.

## The model

**Meta-labelling.** Rules 1–3 propose candidates. The model only answers
*take it or not*. The label is the triple barrier (§7,
`tradelab.labeling.barriers`): did this trade hit its take profit before its
stop and its time limit, **net of costs**?

**Thirteen features, one per mechanism, all known at the fill:**

| # | Feature | The mechanism it stands for |
|---|---|---|
| 1 | Shock size in ATR | How anomalous the move was |
| 2 | Shock quote volume, z against 24 h | Whether the move had participation |
| 3 | Taker buy share during the shock | Aggression: who crossed the spread |
| 4 | OI change during the shock | New positions (up) or closing (down) |
| 5 | OI change × sign of the move | Divergence: opening *into* the move or out of it |
| 6 | Price fall × OI fall, in ATR (**derived**) | Forced flow, the liquidation proxy |
| 7 | Funding rate, z against the asset's 30 days | Crowding: who pays to hold |
| 8 | Premium-index change during the shock | Leverage leading spot |
| 9 | Top-trader long/short ratio change | Positioned money against the crowd |
| 10 | Average trade size, z | Participation: large tickets or many small ones (§11.4) |
| 11 | BTC return in the same bar | Idiosyncratic move or market move |
| 12 | Realised volatility, 1 h ÷ 24 h | Regime: quiet market or already moving |
| 13 | Minutes from shock to fill | How far the pullback had to wait |

**Fixed model**: LightGBM, 200 trees, depth 3, learning rate 0.05, at least
200 samples per leaf. It is small on purpose, because intraday samples are
correlated and a deep model memorises them. Samples are weighted by uniqueness
(`tradelab.labeling.uniqueness`). Probabilities are calibrated with isotonic
regression on the last 20% of each training window.

**Take a candidate when** its calibrated probability exceeds the break-even
its own barriers imply:

```
p* = (SL + cost_loss) / (TP + SL + cost_loss − cost_win)
```

There is no cut-off to search. The arithmetic sets it.

## The automatic optimisation

| Optimised | Values |
|---|---|
| `SL`, × ATR | 0.5, 1.0 |
| `TP`, × stop distance | 1.0, 1.5, 2.0 |
| `H`, minutes | 30, 120 |

**12 configurations**, selected **inside each training window** by purged,
embargoed cross-validation (§8.2, `splits.py`), on net expected value per trade.

**Walk-forward, monthly, anchored.** For each month *m* from 2025-03 to
2026-09:

1. train on every earlier month, with a one-day embargo;
2. select the configuration by inner purged CV, then fit, calibrate and freeze;
3. trade month *m* untouched.

**The result is the concatenation of the traded months**: 19 months, about 1.6
years, carrying every choice the procedure made. The best configuration in
hindsight is reported once and labelled as such.

## Execution and costs

* **Fills**: trade-through by one tick in the bar model. On a stratified
  sample of **2,000 events** replayed against `aggTrades`, a limit fills only
  after the volume resting ahead of it at its price has traded. The sample's
  fill rate against the bar model's gives a haircut, applied to the whole
  backtest. The haircut is reported, and so is the adverse selection: the
  15-minute return of filled limits against unfilled ones.
* **Costs**: maker 0.02%; taker 0.05% + 0.005% half spread + 0.01% slippage;
  funding at each settlement a position spans. `PESSIMISTIC` (§9.5) is reported
  beside it.

## Controls, run before the verdict is read

1. **Synthetic ground truth** (§16.4). A planted persistence edge must be found,
   and pure noise must not.
2. **Lookahead probe** (§16.5). Perturbing a bar must move no decision made
   before it.
3. **Permuted-label null** (§8.4). The full procedure, run 20 times with labels
   shuffled within each month, gives the Sharpe this pipeline makes out of
   nothing.
4. **Rules-only baseline**: steps 1–3 and the exits with no model, taking every
   candidate. It says what the model adds.
5. **Random-entry baseline**: the same exits and sizing at random times on the
   same assets. It says what the anomaly adds.

## What counts as an answer

On the **net out-of-sample Sharpe of the procedure**, rules in order:

1. **Integrity.** The result must be above the permuted-label null's 95th
   percentile, and PBO over the 12 configurations must be at most 0.5.
   Otherwise → **inconclusive**.
2. **Moments** outside the deflated Sharpe's range → **inconclusive**.
3. **The tail.** Each of the worst 1% of trades must cost at most two stops
   (0.5% of equity), gaps included. Otherwise the stop does not bound the loss
   → **inconclusive**. This is the small-gains mandate constrained on the loss
   side, which RESULT-007 showed is the side that matters.
4. **Above the resolvable threshold** at one trial for the realised span
   (≈ 1.3), **and** above both baselines → **passes**. That starts the forward
   simulation. It is not a candidate yet.
5. **Positive and below the threshold** → **inconclusive**. The forward
   simulation runs anyway, because the answer is calendar.
6. **At or below zero** → **refuted**, reported with maker and taker legs apart.
   That separation is §0.2's question: whether the edge failed or the cost did.

Reported whatever the verdict: trades per day, fill rate, hit rate, mean win
and mean loss, the configuration chosen each month, feature importance by MDA
per fold with its stability (§6.1), and average concurrent positions.

## The forward simulation

If the verdict is not *refuted*, the same procedure runs on live data in the
**simulator** (§12.1 rung 2):

* retrained monthly;
* fake capital, the risk engine and `trim_held` active;
* shown on the operator's screen as **SIMULATOR — not validated**.

At least **180 days** of it before §15's `validating → candidate` gate is asked,
and that gate is judged on the forward days alone.

## Confounds, recorded in advance

* The liquidation proxy (feature 6) is not liquidations. It is reported as
  *forced-flow*, never *liquidation*.
* The queue replay is a model of the matching engine, better than a bar touch
  and still not the engine. The forward simulation is the correction.
* Two years holds one broad market cycle. Walk-forward cannot test a regime the
  window lacks.

## What must not happen

* No feature, rule, exit or model setting added after a result. That is a new
  registration.
* No tuning of `k`, of the persistence conditions or of the break-even rule.
  They are derived or literal, and tuning them is optimising outside the 12
  that are counted.
* No in-hindsight best configuration presented as the strategy.
* No relaxing the trade-through rule, the queue haircut or stop-first ordering.
* No forward simulation on a refuted verdict because the screen is empty.

## Amendment, 2026-09-27 — the operator's trend enters the forward simulation

Written after RESULT-008 (`afe7394`) and before a single forward bar exists.

The operator's instruction: **the Market Trend slider must be taken into
account.** The trend is the one channel through which the operator's reading
of the news, the macro catalogue included, reaches the algorithm (§20). So the
forward simulation runs **two books on the same signals**:

| Book | Size per trade | Role |
|---|---|---|
| **Registered** | fixed risk, 0.25% of equity at the stop | The procedure as registered. The §15 gate after 180 days is judged **on this book alone** |
| **Operator** | the same, × `1 + tilt(trend, side)` from `tradelab.scenario.tilt`, with the trend read at each entry | What the operator sees as the strategy with their judgement applied |

Both books receive identical candidates, model decisions, fills and exits, and
both run under §14.1 and `trim_held`. Only size differs, and only by the tilt.
Where a cap binds in one book and not the other, the books may take different
trades, and that is reported.

**What the difference measures.** Operator book minus registered book is the
contribution of the slider, trade by trade. Each trade records the trend value
at entry and its `source` (`manual`, `adopted`, `derived`, as PREREG-005's
amendment defines them). That is evidence about the operator's reading at a
horizon PREREG-005 does not cover. It is reported, and it is **not** a
substitute for PREREG-005. That study's rule, horizon and sample are
unchanged.

**What this does not change.** The procedure, its features, its twelve
configurations, the break-even rule, the forward horizon and the gate. The tilt
is bounded at ±50% (`TILT_CEILING`), so at a maximal reading one stop costs
0.375% of equity in the operator book. Rule 3's two-stop bound is judged on the
registered book, where it was registered.
