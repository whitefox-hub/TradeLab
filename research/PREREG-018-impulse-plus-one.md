# Pre-registration 018 — "Impulso +1": long-only, never sell at a loss, with regimes and capital pools

Drafted 2026-09-29, before the simulator exists. Once committed it is binding. This run
evaluates the design **as specified**, with no parameter optimised. An optimisation, if
ever run, is a later registration with its own trial count.

## The design (the operator's brief, as refined in conversation)

**Universe:** BTC and ETH, spot (priced on Binance USDⓈ-M 5-minute bars, which track spot;
spot costs 0.10% fee + 0.015% slippage per side, no funding paid).

**Regime**, from BTC's spot daily close at each 00:00 UTC:

| Regime | Condition | Max active positions | Size factor |
|---|---|---|---|
| bull | close > SMA150, 30-day vol < 60% | 3 | 1.0 |
| neutral | close > SMA150, vol 60–90% | 2 | 0.75 |
| high volatility | close > SMA150, vol > 90% | 1 | 0.5 |
| risk-off | close ≤ SMA150 | no new entries or rescues | — |
| panic | BTC's 24-hour return < −10% (5-minute bars), for 24 hours | no new entries or rescues | — |

**Score** at each 5-minute close, from inputs usable then (`flash_data.assemble`): momentum
20 (1-hour and 4-hour return > 0, 10 each), acceleration 10 (this hour's return above the
previous hour's), relative volume 20 (z of the bar's quote volume against the trailing 24
hours: > 1 → 20, > 0 → 10), VWAP 10 (close above the UTC day's VWAP), funding 5 (previous
day's rate per settlement < 0.03%), open interest 5 (1-hour OI change > 0 with a positive
1-hour return), BTC trend 10 (BTC's 1-hour return > 0 for ETH, its 24-hour return > 0 for
BTC). Order book (10), liquidations (5) and sentiment (5) have no history and are excluded:
the entry threshold 70/100 becomes **56/80**, the momentum-weak threshold 50/100 becomes
**40/80**.

**Pools:** $10,000: operational $6,000, profits $0, reserve $4,000 never used. A winning
exit returns each purchase's cost to its pool and its profit to the profits pool.

**Entry:** score ≥ 56, regime allows, active positions below the maximum, no other active
position in the asset, locked capital ≤ 30%. Buy 40% of the position ($1,000 × size factor)
from the operational pool. **Scale-in:** within 2 hours, if the price is ≥ 0.5% above the
entry and the score ≥ 56, buy the other 60%, **from the profits pool** (none while it is
empty).

**Exit:** never below the target, avg × 1.0123 (1% net after both sides' costs). On first
reaching it: sell if the score < 40; otherwise trail, selling when the price is 0.5% below
its peak and still at or above the target.

**Rescue:** a position more than 5% under its average, when the previous bar fell on more
than 3× its 24-hour mean volume and this bar rose and closed above the 1-hour VWAP, the
regime allowing. At most 2 per position, 5% apart. Amount: the lesser of 50% of the profits
pool and the room to $1,000 in the position. Among candidates, the one with the lowest
z = (required rise after the rescue) / (30-day daily vol × √30), and only if z ≤ 1.0; BTC
breaks ties.

**Recovery mode:** 7 days without the target, or a loss beyond 8%. If recovery positions'
cost exceeds 30% of the account, no new entries. After 90 days in recovery a position is
**accumulation**: it leaves the active count, still counts as locked, still sells only at
its target.

## How it is read

Two spans, each started fresh: **2021-11-01 → 2024-09-24** (the 2022 fall) and
**2024-10-01 → 2026-09-24**. Reported: the account marked daily (every pool and every
position at its close), trades, share reaching the target, median time to +1%, worst
adverse move, locked capital and new-entry capacity over time, positions still trapped at
the end; against **holding $10,000 of BTC** and against **BTC above SMA 150** (RESULT-015)
on the same $10,000.

Both spans have been read by other studies (PREREG-008 to 014), so the result describes the
design; it is not evidence for it beyond that. A forward run is the clean test.
