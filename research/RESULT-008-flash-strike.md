# Result 008 — Flash Strike: the model finds something, the pullback selects against it, and two years cannot tell

Run 2026-09-27 against research/PREREG-008-flash-strike.md (`2615de8`), with
the pipeline committed (`117cd44`) before it read a market candidate. One
procedure, one trial. Evaluated 2025-03-01 → 2026-09-25: 19 traded months,
574 days.

## The verdict, as registered

**INCONCLUSIVE, rule 5**: positive and below the bar. The registration says
the forward simulation runs.

| Rule | Measured | Required | |
|---|---|---|---|
| 1. Permuted-label null, 95th pct | −0.341 | below the result | passes |
| 1. PBO over 12 configurations | 0.476 | ≤ 0.5 | passes, narrowly |
| 2. Skew / kurtosis | +1.45 / 21.9 | \|skew\| ≤ 3, kurt ≤ 30 | passes |
| 3. Worst 1% of trades | −0.271% of equity | ≥ −0.50% (two stops) | passes |
| 4. Net Sharpe against the bar | **+0.244** | > 1.313 | **no** |
| 5. Positive | yes | | **→ inconclusive** |

## What the procedure did

| | |
|---|---|
| Shocks detected | 142,000 |
| Filled by the bar model | 50.4% |
| Queue calibration (aggTrades, 1,999 fills) | **99.9%** confirmed after 1 s of latency; 99.85% in the bar the model said |
| Candidates the model accepted | 411 |
| Trades the book took | **236**, 0.41 a day |
| Configuration chosen, month by month | 9, 8, 11, 9, 9, 5, 7, 7, 7, 7, 11, 3, 11, 11, 7, 8, 6, 9, 9 |

| | Sharpe |
|---|---|
| **The procedure** | **+0.244** |
| Rules only, every candidate taken | **−25.1** |
| Random entries, same exits | −47.2 |
| Permuted labels, 20 runs | median −1.6, best +0.09 |

Among the accepted candidates, the maker exits (take profit, n = 147) averaged
**+1.02%** of notional and the taker exits (stop or time, n = 264) **−0.75%**.

## Four things this establishes

**1. The model is not what fails.** The rules alone lose at a Sharpe of −25:
thousands of trades, each paying costs, most of them stopped. The model turns
that into a small positive, and it beats every one of the 20 permuted-label
runs. So it extracts something real from the features, which pure noise does
not give it. That is rule 1 passing, and it is not nothing.

**2. The pullback is adverse selection, and it is large.**

| 15 minutes after the order's decision bar | Move from the limit, shock direction |
|---|---|
| Limits that **did not** fill | **+1.19%** |
| Limits that **did** fill | **−0.02%** |

Waiting for the retrace, as the brief asks ("do not chase"), fills the moves
that come back and misses the ones that keep going. The shocks that continue
are exactly the ones that never offer a pullback. The limit is hit by the
market that is about to prove it wrong. That is the price of patience, and it
is measured here for the first time. The persistence check does not remove it,
because persistence is checked on the orders that did fill.

**3. It is not frequent.** 0.41 trades a day, because the break-even rule is
strict and the book takes one entry per bar. The model accepts roughly one
candidate in 175. "Frequent small gains" came out as infrequent, medium-sized
ones: +1.0% wins against −0.75% losses at a 36% hit rate.

**4. The selection is unstable.** The chosen configuration changes almost
every month (3, 5, 6, 7, 8, 9, 11 all appear), and PBO is 0.476, a hair under
the line. The procedure cannot settle on which exit it prefers, which says the
differences between them are mostly noise.

## What was seen before the verdict, stated

During the smoke test on two assets (1000PEPE, 0G), the exit-reason split of
one configuration was displayed: 26% take profit, 74% stop. No parameter,
feature or rule changed afterwards. All were already committed in `2615de8`.

## What the pipeline did not report that the registration required

**Feature importance by MDA, per fold, with its stability (§6.1)** was not
implemented in `run_prereg_008.py`. It is a reported diagnostic and does not
enter the verdict. It is owed, and will be computed by refitting the frozen
monthly decisions, without re-selecting anything. Average concurrent
positions and realised breadth were also not printed. At 0.41 trades a day
they are close to one.

## The forward simulation, and what it can and cannot show

Rule 5 starts it, and it will run. The arithmetic should be read first:

| Forward days | Trades at 0.41 a day | Resolvable Sharpe at 1 trial |
|---|---|---|
| 180 | ~74 | ~2.3 |
| 365 | ~150 | ~1.65 |
| 730 | ~300 | ~1.16 |

At this trade rate, **180 forward days cannot confirm a Sharpe of 0.24, or of
anything short of a figure this backtest gives no reason to expect.** What the
forward run *can* do in 180 days is catch a failure: live fills worse than the
queue calibration, adverse selection larger than measured, or a model that
stops beating its rules-only baseline. It is an integrity check on the
machinery, and it is the first time this project will run one live.

It cannot turn this into a candidate within the year. That should be said
before it starts, so the screen filling up with positions is not read as
evidence.

## What follows, and what does not

* **No second look at these 574 days.** That rules out no looser break-even
  (which trades more), no entry at the shock close instead of the pullback (the
  adverse-selection finding invites it), and no dropping the persistence
  check. Each is a new configuration on data already seen.
* The adverse-selection finding is itself a hypothesis for a **new
  registration**: enter *at* the shock rather than on its retrace, conditioned
  on the same model. It would be judged forward only, and it would be the
  second trial of this family.
* The MDA diagnostic is owed.
