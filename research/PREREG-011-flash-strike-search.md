# Pre-registration 011 — The Flash Strike search: look everywhere, count every look, judge once on the unseen past

Drafted 2026-09-27, before any result of the search exists. Once committed it is binding.

## Why

The operator's instruction: *simulate until the right strategy is found.* Searching until
something wins always finds a winner. With ten thousand rules, the best one's Sharpe on
noise alone is about √(2 ln N) ≈ 4.3 standard errors. So the search is allowed to be as
wide as it likes, on data already seen, provided that:

1. every rule tried is counted;
2. the choice is checked once on a later span of the same data;
3. the verdict comes **once**, from data no study of this family has looked at, with the
   bar raised for the number of rules tried.

## The data, in three parts

| Part | Span | Use |
|---|---|---|
| Discover | 2024-11-01 → 2025-10-31 | every rule scored; the top 20 kept |
| Validate | 2025-11-01 → 2026-09-26 | the 20 scored once; at most 3 go forward |
| **Unseen** | **2022-12-01 → 2024-08-21** | sealed; 10-day windows, below |

The unseen era is being downloaded now (`build_unseen_era.sh`, survivorship-neutral,
FINDING-011's method). No figure from it has been computed. It is not opened for anything
but the test below.

## The rule space: 10,368 rules

The bank (`scripts/build_flash_bank.py`) holds every move reaching the asset's trailing
30-day 95th percentile in ATR, in universe months (monthly top 100 by volume). Its trade
mechanics are `flash_strike`'s, and reproduce PREREG-010's candidates to 1e-9.

| Dimension | Values |
|---|---|
| Shock level | top 5%, 1%, 0.5% of the asset's own moves, 30-min cooldown at that level |
| Filter | none; taker flow agrees with the move; volume z > 2; BTC moved against it |
| Sides | both; long only; short only |
| Entry | market at the shock bar's close, or 1 or 3 bars later |
| Direction | follow the move, or fade it |
| Stop | 0.5, 1, 2 ATR |
| Target | 0.5, 1, 2, 3 × the stop |
| Horizon | 15, 30, 60, 120 min |

Costs: taker 0.065% in, maker 0.02% on a target, taker 0.065% on a stop or the clock.
Funding at settlements. Only data known at the entry is used.

## How a rule is scored

As a **signal**, not a book: every qualifying shock is a trade at 0.25% risk, so there are
hundreds or thousands of trades per window rather than two or three. The daily return is
the mean R of the day's trades × 0.25%. The Sharpe is annualised on calendar days, and a day
with no trade counts as zero. Eligible rules average **≥ 1 trade a day** in discovery.

**Choice.** The 20 eligible rules with the highest discovery Sharpe are scored on
validation. Up to 3 go to the unseen era, and only if all of these hold:

* validation Sharpe > 1.0;
* validation mean net per trade > 0;
* validation trades per day ≥ 1.

If none passes, the search ends **without a strategy**, and that is reported.

## The test on the unseen past: 10-day windows

The operator's rule. For each rule that goes forward, consecutive 10-day windows from
2022-12-01, each opened once and never revisited:

* a window **passes** if the rule's mean net per trade in it is positive;
* the first window that fails **ends that rule's sequence**, and its result is reported;
* at most 63 windows (through 2024-08-21).

**The verdict**, once, on the span each rule reached, against a Sharpe threshold that pays
for the search: the deflated Sharpe for 10,368 trials, and at least the resolvable
Sharpe for that span (1.65/√years). A rule that passes every window but misses the threshold
is **not** a strategy. It is a candidate for a forward run.

## What must not happen

* No rule, filter or grid value added after the first discovery score. A wider search
  is a new registration, counted on top of these 10,368.
* No look at the unseen era before the 3 rules are fixed in a commit.
* No re-running a rule on the unseen era after it fails a window.
* No real capital without §15.

PREREG-010's forward run stands. Its doubling-window amendment is replaced by this.
