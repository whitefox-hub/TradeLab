# Pre-registration 009 — The trend book: the operator's reading, held for the days it is about

Drafted 2026-09-27. It is binding once committed, and it is committed before
the book exists and before any setting counts.

## Why

The operator's position: **their reading of the news adds value.** The Market
Trend slider is how that reading reaches the algorithm (§20). Until now it
could only scale positions other rules had already chosen. In Flash Strike
that means an hour-long trade, while the reading is a view about days
(RESULT-008). A view about days should be held for days.

So this book does one thing: **it holds the market in the direction the
operator sets, sized by how strongly, for as long as the view stands.** Its
P&L is what the reading is worth in money, and it runs beside PREREG-005, which
asks the same question in hits and misses.

## The rules

| | |
|---|---|
| Market | **BTCUSDT 60%, ETHUSDT 40%** perpetuals: the two largest weights in CMC100, which is PREREG-005's yardstick, at roughly their relative size |
| Direction | the sign of `trend − 5`. **At 5 the algorithm's rule decides** (below) |
| Size | net exposure = `tilt.strength(trend)` × equity: **+30% at 8, +50% at 10, −50% at 1**, the same bounded scale the slider already means (§20) |
| Entry | at the next 5-minute bar close after a setting is recorded, at market (taker) |
| Hold | **for as long as the trend is not 5, until a barrier below is hit.** A change resizes the book to the new value. At 5 the algorithm's rule takes over. There is no expiry by time: the operator's instruction is that a trend other than 5 is acted on |
| Take profit | the basket moves **+2 × its daily ATR** in the position's favour from the entry, ≈ +6% of the basket at a typical 3% daily ATR, ≈ +1.8% of equity at a setting of 8 |
| Stop | the basket moves **1 × its daily ATR** against the position, ≈ −3% of the basket, ≈ −0.9% of equity at 8 |
| After either | **flat until the operator changes the trend.** The view has either played out or been answered by the market; if the operator still holds it, setting it again re-enters |
| Costs | taker 0.05% + 0.005% half spread + 0.01% slippage on every change; funding at every settlement held across |
| Risk | every §14.1 limit, the risk engine, `trim_held`, and the daily-loss halt |
| Capital | $10,000 fake, in the simulator (§12.1 rung 2). Nothing here reaches a venue |

**Barriers, exactly.** The basket is the index `0.6 × BTC/BTC₀ + 0.4 × ETH/ETH₀`,
with prices from the 5-minute closes. Its daily ATR is the 14-day average true
range of that index's daily bars, taken from days closed before the entry. Both
levels are fixed at entry and reset when a new setting resizes the book.
Barriers are checked on every 5-minute bar. When one bar touches both, the
stop came first. A bar that opens through the stop exits at the bar's worst
price. Both exits are at market (taker). The operator's instruction is to take
profits (option A, 2026-09-27), and this is it.

Every setting moves the book, whoever made it, because the book is the
operator's instrument. Each position records the trend that caused it and its
`source` (`manual`, `adopted`, `derived`).

## At 5: the algorithm's rule, in the same book

The operator's instruction: **when the trend is 5, the algorithm follows its own
logic, in the same book.** The book is therefore never idle. Every position
records which of the two guided it:

| `guide` | When | Direction and size |
|---|---|---|
| `operator` | trend ≠ 5 | as above, from the operator's value |
| `algorithm` | trend = 5 | **7-day time-series momentum**: the sign of the basket's return over the previous 7 days, **±20% of equity**, recomputed once a day at 00:00 UTC |

The algorithm's rule is fixed here and has **no validation in this lab**.
Time-series momentum is documented elsewhere and has not been tested on this
data. Seven days matches PREREG-005's horizon, so the two guides are judged
over the same span. ±20% is the exposure of an operator setting of 7, which is
moderate, and it is fixed, because the rule has no strength of its own to
express.

When the operator moves off 5, the algorithm's position is replaced by the
operator's at the next bar. When they return to 5, the algorithm's rule resumes
at the next bar from that day's reading, and at every 00:00 UTC after it.

**A trend left standing keeps the book open.** A view set for a piece of news
that has since been priced goes on holding the market until the slider returns
to 5. That is the operator's instruction, and it is the operator's to manage.
The screen shows how long the current setting has stood.

## What counts as an answer

**Two numbers, reported monthly, and neither is decided early:**

1. **Hits**: PREREG-005's test, unchanged, on `manual` settings only. It is a
   one-sided binomial of the direction over the following 7 days, with the
   cutoff and the stopping count PREREG-005 set. The book holds past 7 days,
   and the hit test does not follow it there. Its horizon was registered first.
2. **Money, by guide.** Net P&L and Sharpe for `operator` days and for
   `algorithm` days separately, each against **buy-and-hold** of the same
   basket at the same average absolute exposure over the same days. A guide
   that makes money only because crypto went up is beta, not reading, and the
   comparison is what separates the two. The book's total is reported as well,
   and it is the sum of the two, never a verdict on either.

The operator's reading adds value when both agree: hits significant under
PREREG-005, and `operator` P&L above its buy-and-hold comparison at the
resolvable threshold for the span its days cover. The algorithm's rule is
judged by its P&L against the same comparison over its own days. It is the
first trial of time-series momentum here.

**Power, stated now.** The book is one directional bet (FINDING-010: a long
crypto book is ~1.8 independent bets however it is held). At a 65% hit rate
the binomial needs ~66 manual calls, at 60% ~150. The Sharpe threshold falls as
1.65/√years (§0.4a). This is a study measured in years, not weeks, and a good
or bad month says little either way.

## What must not happen

* No changing the basket, the sizing scale, the hold rule, the barriers or the
  algorithm's rule after the book starts. That is a new registration.
* No attributing a day to the wrong guide. `guide` is set from the trend in
  force when the position was sized, and never edited afterwards.
* No counting `adopted` or `derived` settings in the hit test. They move the
  book, and the book's P&L reports them. The hit test is about a person's
  reading.
* No reading a winning week as validation, or a losing one as refutation.
* No path from this book to real capital that skips §15. It runs in the
  simulator and nowhere else.
