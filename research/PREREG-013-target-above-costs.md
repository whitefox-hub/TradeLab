# Pre-registration 013 — A target above the costs, a loss that is capped

Drafted 2026-09-29, before any result exists. Once committed it is binding.

## Why

The operator's rule: *register the commission and do not exit at a loss; take profit when
the price has moved a percentage above it.* Measured on BTC's last 1,000 shocks, never
exiting at a loss wins 97–99% of the time and still loses, because the few trades that
never recover lose ~18% each and wait up to 130 days. So the rule is tested with the
operator's target and a **cap on the loss**, wide enough that most trades are given room
to come back, and a limit on how long a trade may wait.

PREREG-011 tried exits within 2 hours. This holds for days, so it is a new family,
counted from zero.

## The trade

The shocks are PREREG-010's: the asset's top 1% of 5-minute moves in ATR, 30-minute
cooldown, taker flow on the move's side, monthly top 100 by volume. Entry at market at the
shock bar's close, in the move's direction.

| Exit | Rule | Cost out |
|---|---|---|
| **Target** | price moves **x** past the entry *plus both costs*, so the net gain is x | maker 0.02% |
| **Loss cap** | price moves **L** against the entry | taker 0.065%, at the bar's worst on a gap |
| **Waiting limit** | **T** days pass | taker 0.065% |

Entry taker 0.065%. Funding at settlements. A bar touching both target and cap is the cap.

## The rule space: 96 rules

| | Values |
|---|---|
| Net target x | 0.1%, 0.3%, 0.5%, 1.0% |
| Loss cap L | 2%, 3%, 5%, 10% |
| Waiting limit T | 1, 3, 7 days |
| Assets | the whole universe; BTC and ETH only |

## How a rule is scored and chosen

As PREREG-011: each shock a trade at 0.25% risk (notional = 0.25% / L), its R on the
entry day, the daily return the mean R of the day's trades × 0.25%, Sharpe on calendar
days. Discovery 2024-11-01 → 2025-10-31, validation 2025-11-01 → 2026-09-24 (a trade
whose waiting limit passes the span's end is dropped). The 20 best of discovery are scored
on validation. Up to 3 go to the unseen era if validation Sharpe > 1, mean net > 0 and
trades per day ≥ 0.2. None passing ends the study without a pattern.

## The unseen past

Consecutive 10-day windows from 2022-12-01 (after the unseen era's first year, which is
the model-free warm-up of the shock threshold), each opened once. A rule continues while
its cumulative net return since 2022-12-01 is positive; the first failure ends it and is
reported. Verdict once, on the span reached: Sharpe above 1.65/√years.

## What must not happen

* No grid value added after the first discovery score.
* No look at the unseen era before the rules going to it are committed.
* The count of concurrent open trades is reported, not limited: a rule that needs 200
  positions at once is not a book, and the report says so.

**Disclosed before commit.** A mechanics check on BTC over the whole seen era showed three
rules' figures (x 0.1%/L 10%/T 7: 96% winners, −15 bps a trade; x 0.1%/L 2%/T 1: 91%,
−8 bps; x 1%/L 5%/T 3: 75%, −13 bps). That is discovery data, and the unseen era is untouched.
