# Pre-registration 014 — Never sell at a loss: BTC and ETH, a target above the costs

Drafted 2026-09-29, before any data of the test span has been read. Once committed it is
binding.

## Why

The operator's rule: *register the commission, never sell at a loss, take profit when the
price has moved a percentage above it.* On the seen era (2024-11 → 2026-09), BTC and ETH
under that rule ended ahead (+7 to +9% on $10,000, with $500 a trade), while the whole
universe lost (delisted coins close positions at their loss). The seen era had no long
bear market. The rule's risk is exactly that: a position bought near a top waits as long
as the market takes to come back. The unseen era holds 2022, when BTC fell 75% and took
more than two years to recover. It is the test this rule needs.

The three targets were fixed by the operator after seeing the seen-era table for eight
targets. All three go to the test; none is chosen over the others.

## The rule

| | |
|---|---|
| Assets | BTC and ETH perpetuals |
| Signal | PREREG-010's shock: the asset's top 1% of 5-minute moves in ATR, 30-minute cooldown, taker flow on the move's side |
| Entry | market at the shock bar's close, in the move's direction, taker 0.065% |
| Exit | **only** at the target: the price moves x past the entry plus both costs (maker 0.02% out), so every closed trade nets x before funding |
| Loss limit | **none**. Waiting limit: **none** |
| Size | $500 a trade, one position per asset at a time, $10,000 of capital |
| Targets | **x = 0.5%, 1%, 2%**, three rules |

Funding is charged or paid at settlements while a trade is open.

## How it is judged

**The account's real value**, every day at 00:00 UTC: capital, plus realised gains, plus
every open position marked at that close net of the cost to exit it and the funding it
has paid. A position not sold is not a loss avoided. It is a loss carried, and it counts.

**Test span:** 2022-01-01 → 2024-09-24, on `intraday_2022` (survivorship-neutral,
FINDING-011), run **in full**. The account at the end of every 10-day window is reported,
and no window stops the test. The operator's instruction for this rule is *never sell at a
loss*, and a gate that ends the test while positions wait to recover would judge the rule
by the moment it was stopped. A mechanics check on the seen era showed exactly that: a
10-day gate stopped all three targets within 20 days, in a span they ended 7 to 9% ahead.
That check was run before this commit, and it is why the gate is not used.

**Verdict**, once per rule, on the full span: the daily Sharpe of the account above the
resolvable threshold 1.65/√years (1.00 over the full 2.7 years), **and** the account
ahead of $10,000 at the end. Reported beside it: the worst drawdown of the account, the
longest wait of a trade, and the largest loss carried by an open position. On the seen era
the largest carried losses were −64% (a BTC short, 10 months open) and −82% (an ETH short,
7 months open); both closed at their target.

## What must not happen

* No target, asset, size or rule changed after this commit. A change is a new
  registration, and it may only use data after this span.
* No rule re-run on the span.
* No real capital without §15.

**Seen-era mechanics check, before commit** (`--era seen`, full span): accounts of $10,819,
$10,734 and $10,661; daily Sharpe 0.73, 0.72 and 0.66, below the 1.20 that span would
need; the worst carried loss of the 0.5% rule was −147% of its stake (a short whose price
more than doubled), which without a loss limit is more than the $500 put up.
