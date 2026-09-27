# Pre-registration 007 — Does a dollar-neutral book collect funding faster than price takes it back?

Drafted 2026-09-27. Once committed, this registration is binding and fixed
before `scripts/run_prereg_007.py` exists. No return has been read under this
rule, or under any rule that ranks assets by funding within one venue.

## Why this one

Three findings narrowed what is worth writing:

* **FEASIBILITY-008 and FINDING-009.** A dollar-neutral book of beta-adjusted
  residuals on this universe carries roughly its asset count in independent
  bets, in every week of two years, and more on the worst days. Long-only books
  and short-only books are ~1.8 bets whatever they hold (FINDING-010). This is
  the only shape whose arithmetic clears §0.4a.
* **FINDING-010.** For a single short, funding is a median and not a mean: 71%
  of days it pays, and the mean is −13.5% a year, because funding spikes
  against a short exactly when price moves against it. A book that is **on
  both sides** of the funding flow nets those spikes against each other instead
  of absorbing them.
* **§18 Phase 4b**, hypothesis 1: funding says who is crowded and what it costs
  them. It is fully backfilled and it is not a function of price.

And the operator's mandate: frequent small gains rather than a large move.
Carry is that literally — a payment every eight hours — and this registration turns the mandate into a condition the result
has to meet, below, rather than a hope about its shape.

## What has already been seen, stated before it matters

* The panel's **per-asset funding moments** (FINDING-010): means, medians and
  the share of positive days. No return conditioned on a funding rank.
* The panel's **residual correlation structure** (FEASIBILITY-008, FINDING-009).
  Covariances, no signal.
* Three trials of **cross-venue** funding dispersion on the five-venue panel
  (PREREG-003, 004, 006). A different construction on overlapping data: the same
  asset on two venues, not different assets on one.

That last point is why the trial count below is argued rather than asserted.

## The hypothesis

> **H1.** Among Binance USD-M perpetuals, a dollar-neutral book that is long
> the lowest trailing funding and short the highest earns a positive mean net
> return: the funding it collects is not taken back by the price legs and the
> trading costs.

Two components, reported apart as well as together: the **funding leg**
(what the book is paid) and the **price leg** (what crowding does to price
afterwards). H1 is about their sum after costs. Either one alone is not H1.

## The one configuration

| | |
|---|---|
| Panel | `data/parquet/funding_multivenue`, venue `binance` only: 244 assets, 2024-09-25 → 2026-09-25, daily funding (sum of that UTC day's settlements) and daily close |
| Eligible on a rebalance day | Close and funding on each of the prior 90 days |
| Minimum cross-section | 40 eligible assets, or the book stands flat that week |
| Signal | Mean daily funding over the 7 days ending at *d−1* |
| Weights | Cross-sectional rank of the signal, demeaned and scaled so Σ\|w\| = 1 and Σw = 0. Lowest funding longest, highest funding shortest. Ties share the mean rank |
| Rebalance | Every 7 days, at the close of *d−1*, starting at the first day the minimum cross-section is met |
| Held between rebalances | Weights drift with price; they are not re-levelled daily |
| Daily P&L | Σ wᵢ · (rᵢ − fᵢ), where rᵢ is close-to-close return and fᵢ is day *d*'s funding. Positive funding is paid by longs |
| Costs | Turnover × per-side cost from `BINANCE_RETAIL` (0.065% a side), on every rebalance including the first entry. `PESSIMISTIC` reported as a sensitivity and not decided on |
| Evaluation | Daily net returns from the first rebalance to the panel end |

**Timing, because it is where a leak would live.** Day *d*'s funding is the
00:00, 08:00 and 16:00 UTC settlements of *d*. A position opened at the close
of *d−1* is held at all three, so it pays exactly fᵢ(*d*). The signal uses
funding through *d−1*, whose last settlement is 16:00 on *d−1*, before the
close it trades at. The run script must include a probe that perturbs day *d*
and confirms no weight formed at *d−1* moves.

**Every constant is inherited or structural.**

* The 7-day hold is the upper edge of §0.2's 2–7 day band. It is taken because
  cost is what killed PREREG-004, and a shorter hold multiplies it.
* The 7-day signal matches the hold, so the signal horizon and the hold horizon
  are one choice rather than two.
* 90 days is FINDING-009's beta window.
* Rank weights use the whole cross-section and have no quantile to choose, which
  is the §0.4a lever. Terciles or deciles would be a threshold.
* 40 is FEASIBILITY-008's smallest measured breadth row.

## The trial count, and the bar

This is **trial 1 of a new family**: single venue, cross-sectional, dollar
neutral. The three earlier trials asked whether the *same asset* is mispriced
*across venues*. This asks whether *different assets* on *one venue* are. The
questions differ, but the funding data overlaps, so both bars are stated now
and both are reported:

| Trials counted | Resolvable Sharpe, ~632 days |
|---|---|
| **1** (this family) | **1.25** |
| 4 (every funding trial in the project) | 2.05 |

Per bet, at breadth *N*, clearing 1.25 needs about 1.25/√N: **0.11 at 120
bets, 0.08 at 240**. A signal-weighted book will realise less breadth than
FINDING-009's random one, so the realised breadth is measured and reported,
and it is not assumed.

## What counts as an answer

Decided on the **net** Sharpe at `BINANCE_RETAIL`, with these rules applied in
order:

1. **Concentration first — the mandate as a condition.** If the 5 largest days
   account for more than half of total net return, the result is
   **inconclusive**, whatever the Sharpe. RESULT-003b was two thirds one day,
   and a book like that does not make frequent small gains, whatever its mean.
   Also reported: the share of positive weeks, and the median and mean weekly
   return side by side. A positive median with a negative mean is FINDING-010's
   shape, and it is reported as that.
2. **Moments.** Skew and kurtosis are reported. If they are outside the range
   the deflated Sharpe is derived for, the gate returns inconclusive
   (RESULT-003b's fix), and that verdict stands.
3. **Net Sharpe above 1.25** → passes this family's bar. It does **not** become
   a candidate on that alone. It earns two confirmations, both fixed now:
   * **Survivorship.** Rebuild the panel with every perpetual that traded on
     Binance in the window, delisted ones included (§4.4), and re-run this
     configuration unchanged. A pass that disappears when the dead are added
     back is a survivorship result.
   * **Forward.** Every day after this registration is committed is data
     nothing here has seen. The same configuration runs on it, untouched, for
     at least 180 days before §15's `validating → candidate` gate is asked.
4. **Positive but below 1.25** → inconclusive. The answer is more calendar,
   which the forward run gives. No second look at these 632 days.
5. **At or below zero net** → **refuted.** Reported as a refutation, with the
   funding and price legs apart, so the refutation says which leg failed.

## Confounds, recorded in advance

* **Survivorship, twice over.** The panel is assets that trade today, and only
  those listed on all five venues. Delisted names are missing, and they are
  disproportionately the ones that collapsed. That biases the short leg, which
  loses the crashes it would have profited from, and the long leg, which loses
  the names that went to zero. The net direction is not known in advance. That
  is why rule 3 demands the rebuilt panel, not a caveat.
* **Beta is not hedged.** The book is dollar-neutral, not beta-neutral. With
  median beta near 1.00 (FEASIBILITY-008) the two are close, but a
  funding-ranked book may tilt systematically, for example high-funding names
  running high beta. The realised beta to the equal-weighted market is
  reported. A result that is mostly beta is reported as that.
* **Funding and price are not independent.** High funding follows a rally.
  Shorting it is partly short-term reversal, which is a price signal. That is
  part of what H1 claims, not a leak, but it means a pass does not show that
  funding *causes* anything.
* **Daily closes, not fills.** Close-to-close at 00:00 UTC ignores the spread
  at that instant and the depth of the smallest names. `PESSIMISTIC` is the
  bound, and §9.5 requires it to stay positive before promotion.

## Deployment, stated so the research answer is not mistaken for it

Research is validated wide and deployed narrow (§6.3). Rank weights over 120 to
240 names put the middle of the book at a few percent of a percent each. At the
$5 venue floor and twenty increments of headroom (§14.1), sizing is a control
only above about **$100 per name, $12k–24k gross**. Below that the book cannot
be held as registered.

So, if this passes: the narrow deployed subset is chosen **by liquidity
alone**, never by in-sample performance, and it is re-validated as its own
configuration before any capital decision. Whether that capital exists, and on
which venue the operator can trade (Bybit refuses this operator; Binance live is untested), are decisions this
study does not make.

## What must not happen

* No sweep of hold, signal window, eligibility window or weighting. Each is
  inherited above. 3- and 14-day holds may be reported **once** as a declared
  sensitivity, and the decision rests on 7.
* No beta hedge added after the result. That is a second configuration, and it
  needs a second registration.
* No tercile or decile book "because the ranks were noisy". That is a threshold
  arriving afterwards.
* No trimming of the tail to make the moments behave. The tail is part of the
  result (RESULT-003b).
* No second trial on these 632 days. What comes after this run is the rebuilt
  panel and the forward window, and nothing else.
