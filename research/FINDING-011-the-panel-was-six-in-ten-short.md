# Finding 011 — The panel was six assets in ten short, and none of them dead

Measured 2026-09-27. `scripts/build_binance_pit_panel.py` rebuilt the Binance
daily panel, 2024-09-25 → 2026-09-25, with every USDT crypto perpetual that
traded in the window, delisted ones included. `scripts/check_panel_survivorship.py`
compared it with the five-venue panel that PREREG-003 to 007 used. The check
reads prices and funding per asset, and nothing conditioned on a signal. No trial
was spent.

## The old panel was right about what it held

Where both panels hold an asset-day, **137,584 of them across 244 assets**:

| | |
|---|---|
| Closes that differ | **0** |
| Funding that differs | **0** |
| Days with fewer settlements in the old panel | **0** |

The older collector breaks out of its funding loop on any non-list answer. A
rate-limited asset would have come back short and looked complete, and none
did. The flaw is real, and it did not fire. The new collector raises instead.

Two boundary effects, neither of them material:

* **The last day was partial.** The old panel was fetched on 2026-09-25 and
  holds that day's intraday price and part of its settlements: ADA at 0.2558
  against a close of 0.2586, RAVE with five of its six settlements. That is one
  day of PREREG-007's 640. The builder now refuses to include a day that has not
  closed.
* **The first day is missing** for 121 of the old panel's assets, because the
  old window began a few hours after midnight.

## What it did not hold

| | Assets with a price on the day | |
|---|---|---|
| | New | Old | Removed |
| 2024-10-01 | 292 | 122 | 58% |
| 2025-04-01 | 381 | 151 | 60% |
| 2025-10-01 | 504 | 189 | 62% |
| 2026-04-01 | 540 | 232 | 57% |
| 2026-07-01 | 526 | 240 | 54% |

**644 assets traded in the window, against 244.** The survivors-only and
five-venue filters together removed about six in ten of the tradeable
cross-section on every day of two years. **119** of the 644 no longer trade.

The universe excludes TradFi perpetuals (equities, commodities) and index
perpetuals, which the old panel did not hold either. For 31 symbols the REST
endpoint no longer answers, and 3 of those (AERGO, BDXN, SXP) traded inside the
window. For them the public archive is the only source, and it is used for
them alone.

## What the dead did on the way out

118 assets were delisted inside the window with at least 30 days of history:

| | Median | 5th pct | 95th pct |
|---|---|---|---|
| Last traded day | −4.1% | −37.0% | **+41.7%** |
| Final 30 days | **−38.6%** | −78.6% | +58.3% |
| Funding, final 7 days | +0.44% | −12.9% | +7.6% |

* **42 of 118 fell more than half in their last month.** A long book on the old
  panel never held them. That flatters any long leg.
* **4 rose more than half on their last day**: A2Z, ALPACA (0.19 → 1.19), DENT
  and TANSSI. A delisting squeeze is RESULT-007's RAVE episode with no exit left.
  The short is settled at the squeezed price. A short book on the old panel never
  met one, and that flatters any short leg too.
* The **5th percentile of final-week funding is −12.9%** over seven days. That
  is FINDING-010's mechanism at its most extreme: shorts are charged hardest
  exactly as the price turns against them.

So the direction of the bias depends on the book. The old panel was not
neutral for any of them, and it was least neutral for the tails. A result
validated on it has not been validated.

## One thing the venue does that a collector must not trust

After delisting, `/fapi/v1/klines` goes on returning a daily bar at the frozen
settlement price with **zero volume**, indefinitely. AGIX, merged in 2024,
still "closes" at 0.7009 every day. A collector that keeps every bar turns a
dead asset into a live one with zero volatility, which is the most flattering
asset a book can hold. This one keeps only days that traded, and never a day
after `deliveryDate`.

## What follows

Every later study on this universe uses `data/parquet/binance_pit`.
PREREG-003 to 007 are not re-run on it. That would be a second look at 640 days
already seen, and none of them passed on the flattering panel either.
