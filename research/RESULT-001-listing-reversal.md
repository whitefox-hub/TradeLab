# Result 001 — Post-listing reversal

Run 2026-09-25 against `research/PREREG-001-listing-reversal.md`, which was
committed before any listing return was computed (`63b6d70`). Nothing in the
registration was altered.

## Sample

| | |
|---|---|
| Listings announced | 583 |
| Tokenised equities excluded | 38 |
| No retrievable price history | 14 |
| Insufficient history | 0 |
| **Evaluated** | **531** over 6.8 years (78/yr) |

## Outcome

| | |
|---|---|
| Gross mean per trade | **+2.85%** |
| Funding mean per trade | **−0.73%** |
| Cost per round trip | 0.13% |
| **Net mean per trade** | **+1.99%** (median +8.36%) |
| Net standard deviation | 34.26% |
| Win rate | 65.3% |
| **Annualised Sharpe** | **+0.515** |
| Resolvable at 1 trial | 0.633 |

**Verdict: inconclusive.** Minimum track record is 895 observations against 531
available. The point estimate is positive and below what this sample can
distinguish from chance, so the result neither supports nor refutes the
hypothesis. It is not a weak pass.

## What the registration got right and wrong

**Right: the direction.** Shorting after a new listing earned +2.85% gross per
trade with a 65.3% win rate and a median of +8.36%. The price reversal the
mechanism predicted is there, and it is not small.

**Wrong: the funding.** The registration argued a short would *collect* funding,
reasoning from the 11.8%/yr that longs pay on mature BTCUSDT. On fresh listings
the short **pays** — −0.73% per trade, negative in 37% of events and heavily
negative in the tail. The mature-market asymmetry does not hold at listing,
which is consistent with new contracts being crowded on the short side: everyone
expects the dump, the perpetual trades below spot, and funding inverts.

So half the registered mechanism is refuted on its own terms. The trade works
for the reason given and is then charged for the privilege, rather than paid
twice as claimed.

**Not anticipated at all: the tail.** Worst case **−202.7%**, 1st percentile
−113.5%, 5th percentile −70.1%. A mean of +2.0% against a standard deviation of
34% is what produces the Sharpe of 0.5 despite a 65% win rate: the strategy wins
often and small, loses rarely and catastrophically. As specified — equal weight,
no stop — it is not survivable. The registration recorded "short squeezes on
thin new contracts are violent" as the counter-argument; it was right, and it
understated the magnitude.

## What this does and does not license

The hypothesis is **not** dead, and it is **not** confirmed. What the sample can
say is that 531 events cannot resolve a Sharpe of 0.5, and that 895 would.

What must not happen next is a search for the configuration that fixes the tail
— a stop, a different holding period, a volatility filter. Each is a trial, and
at 12 trials this sample resolves only 1.19, so tuning would spend the remaining
budget to produce a number that cannot be believed. The registration says this
in advance and it holds now that the result is known.

Two routes that add observations rather than trials:

1. **A second venue.** Bybit and OKX list many of the same tokens on their own
   dates. Those are additional independent events, not the same ones re-counted,
   and they roughly double N toward the 895 required.
2. **The excluded 38 tokenised equities** are a separate population and can be
   tested as their own pre-registration rather than pooled in.

The tail is the real obstacle and it is a position-sizing question, not a signal
question. It should be addressed in a registration of its own, with the sizing
rule fixed before it is measured.
