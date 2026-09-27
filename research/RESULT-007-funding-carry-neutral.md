# Result 007 — Inconclusive by the rule, and one short squeeze is most of why

Run 2026-09-27 against research/PREREG-007-funding-carry-neutral.md
(`7e98c31`), with `scripts/run_prereg_007.py` committed (`31358b5`) before it
read the panel. One trial, the first of this family.

| | |
|---|---|
| Evaluated | 2024-12-25 → 2026-09-25, 640 days, 1.75 years |
| Rebalances | 92, every 7 days |
| Names held | median 174, min 120, max 238 |
| Preflight | noise straddles 0, planted signal found, 0 weights moved by the lookahead probe (synthetic and real) |

## The verdict, as registered

**INCONCLUSIVE, rule 2**: skew **−17.4**, kurtosis **369.7**, far outside
the range the deflated Sharpe is derived for. The registration applies its
rules in order and says that verdict stands.

It must not be read as "maybe". The book **lost 46% of equity**:

| Leg | Sum over window | Sharpe |
|---|---|---|
| Funding | **+32.7%** | **+3.56** |
| Price | **−74.4%** | −1.28 |
| Costs | −4.5% | — |
| **Net** | **−46.1%** | **−0.70** |

The rule is inconclusive because the loss belongs to too few days to measure
its probability, not because the book might have been profitable.

## A correction made after the first run, stated because it changed the answer printed

The first run printed **REFUTED (rule 5)**. The script tested rule 5 before
rule 2, and that is not the order registered. It now follows the order, and the
verdict above is the registered one. No constant, weight, cost or data changed.
One diagnostic was also corrected: realised breadth was printed as
Σσᵢ²/var(Σcᵢ), which equals one for *independent* positions — the opposite
reading. It is (Σσᵢ)²/var(Σcᵢ) now. Neither enters the decision.

## One name

| Day | Net | Price | Funding | Market | Largest contributor |
|---|---|---|---|---|---|
| 2026-04-14 | **−43.2%** | −37.3% | −6.0% | −1.2% | RAVE −35.6% |
| 2026-04-12 | −14.3% | −13.9% | −0.3% | −2.5% | RAVE −13.3% |
| 2026-04-13 | −11.1% | −8.3% | −2.8% | +6.0% | RAVE −8.4% |
| 2026-04-10 | −3.1% | −3.1% | +0.1% | +2.1% | RAVE −2.0% |
| 2026-07-18 | −3.0% | −3.0% | +0.1% | −0.3% | AKE −2.7% |

**RAVE alone contributed −63.8% of equity.** It went from 0.25 to 21.9 in about
two weeks, roughly ninety times, on Binance, Bybit and Gate alike, with funding
moving to hourly settlement. It was not a data error. It was a short squeeze.

The mechanism is the one FINDING-010 described for a single short, now inside a
diversified book:

1. On 2026-04-08 RAVE had the highest trailing funding in the cross-section.
   Crowded longs were paying, so the rule shorted it, at **−0.87%** of equity.
2. **Weights drift between rebalances, as registered.** A short on something
   that rises ninety-fold grows with it, and by 2026-04-14 that −0.87% was about
   a third of the book.
3. Funding turned sharply negative as shorts were squeezed, so the funding leg
   charged the position (−6.0% on the worst day) on the same days the price
   leg did.
4. On 2026-04-15 the rebalance **flipped it to a long** at +0.91%, because its
   funding was now the lowest. RAVE then fell from 21.9 to 0.64.

Without that one day the net is still **negative, −2.9%** (Sharpe −0.09).
Without the worst five it is +28.4% (Sharpe +1.35). That is the tail being
trimmed, and the registration forbids trimming it. It is reported only to
locate the damage: one episode, spread over a week.

## The mandate's shape, exactly

| | |
|---|---|
| Weeks positive | **61.5%** of 91 |
| Weekly median | **+0.52%** |
| Weekly mean | **−0.51%** |

This is frequent small gains, as the operator asked, and it lost half the
capital. The median and the mean disagree in sign. This is the third time this
project has met that shape (RESULT-004, RESULT-006, FINDING-010), and the first
time on a book with 174 names.

**Breadth did not protect against it**, and should not have been expected to.
Realised breadth on the price leg was 36 against the 174 names held. That is
diversification against ordinary moves, and it does nothing against one
position growing to a third of the book. FINDING-009 measured breadth on random
weights held fixed. A funding rule shorts the names that are rising, and drift
makes them larger.

## What held up

* **The funding leg is real and large.** +32.7% over 1.75 years at a Sharpe
  of 3.56. The crowded side pays, and it pays steadily.
* **Beta was not the story.** Realised beta to the equal-weighted market is
  −0.04.
* **The 3-day hold is not a finding.** The declared sensitivity printed a net
  Sharpe of **+1.158** at a 3-day hold, against −0.70 at 7 and −0.94 at 14. The
  registration allowed it to be reported once and not decided on, and it is
  exactly what that clause exists for. A shorter hold re-levels the squeezed
  short sooner, so the difference is very likely the same week of RAVE. Acting
  on it would be a second look at these 640 days.

## A gap in the risk engine this exposes

`max_position_pct_equity` is checked **when an order is sent**, never on a
position that grows by itself (`tradelab/risk/limits.py`). The account-level
limits are checked every tick. `max_daily_loss` would have halted the run on
2026-04-12, but a halt stops new orders and closes nothing. Under the engine as
it stands, this book would have halted on the 12th and gone on holding the
short into the −43% day.

Any book that is short something needs a **held-position cap that forces a
reduce-only trim**. It is independent of this study. It is an engineering
finding, and it should be built before any short is run on the testnet.

## What follows, and what does not

* **No second trial on these 640 days.** That covers no 3-day rerun, no
  per-name cap added and rerun, and no squeeze filter. Each is a new
  configuration fitted to a known episode.
* The registration names no continuation for rule 2. So any further work on
  funding carry is a **new registration evaluated only on data after
  2026-09-27**. Its construction must answer the squeeze **before** that data
  exists: a per-name cap that is re-imposed daily, or a price-based exit. It
  counts against this family's trial budget, and the bar rises with it.
* The survivorship rebuild in rule 3 is not triggered, because it was the
  consequence of a pass.

The finding that outlives this study is not about funding. **Frequent small
gains is a shape, and it is the shape of selling insurance.** A small-gains
mandate has to be written as a constraint on the loss tail. Rule 1 constrained
the gains, which was the wrong side.
