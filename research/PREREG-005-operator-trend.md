# Pre-registration 005 — Does the operator's read of the news predict anything?

Registered 2026-09-26. The `market_conditions` table was created today and its
change log holds only the settings made while building the screen. Nothing below
can have been fitted to data that does not exist.

**The cutoff, so "before" is a timestamp and not a claim.** Only settings
recorded strictly after

```
2026-09-26T14:49:00-03:00
```

count as observations. That is the commit timestamp of this registration
(`3c3e29399d8d`). The entries already in `audit_log` were made while testing the
endpoint and are excluded by that clause rather than deleted — an audit log that
can be tidied is not one. The analysis script must filter on it, and a result
computed without it is void.

## Why this one is registered now and run later

Every previous registration here was written against a panel that already
existed, and PREREG-004 had to admit it could refute but not confirm, because
the 640 days it tested had all been seen. This one is the opposite: **the data
starts accruing today**, so the rule can be fixed genuinely in advance and a
positive result would mean something.

That window closes immediately. Each trend set before the rule is written is an
observation seen before the measurement was defined, and the honest thing then
is to discard it. So this is registered now and measured in a year or more.

## What is being claimed

The Market Trend is the operator's reading of the news, expressed as one number.
The claim implicit in setting it is that this reading carries information about
where the market goes next. That claim has never been tested here, and the tilt
built on it in §20 is applied on the strength of it.

> **H1.** When the operator sets a directional trend, the market moves that way
> over the following horizon more often than chance.

Note what is *not* claimed. Not that the tilt improves a return — a tilt on a
long-only book is leverage and cannot change a Sharpe, as RESULT-004's arms
showed. Direction first, because if the calls are not directionally right there
is nothing for any sizing rule to exploit.

## The rule

| | |
|---|---|
| Observation | One per **change** to the trend direction, taken from `audit_log` at `scenario.set` |
| Included | Changes to `UP` or `DOWN` **with `source = 'manual'`**. A change to `NORMAL` is the absence of a call, not a call |
| Market | BTCUSDT perpetual, Binance, close to close from the archive |
| Entry | The first bar whose `available_at` is strictly after the setting's timestamp |
| Horizon | **7 days**, or until the trend changes again, whichever is shorter |
| Outcome | `sign(return) == +1 for UP, -1 for DOWN` |
| Test | One-sided binomial against 0.5 |

Seven days is the longest horizon on §11.5's grid, taken from there rather than
chosen here. A trend read from the news is a view about the following days, not
the following minutes, and picking the horizon afterwards is the sweep this
project keeps refusing.

Truncating at the next change makes the observations **non-overlapping by
construction**, which is what lets them be counted as independent. It also
measures the call the operator actually made: a view replaced after two days was
a two-day view, whatever was intended.

### Amendment, 2026-09-26, before any observation exists

The trend is to become settable by something other than a person: §11.4's
participation dimension — trade count and average trade size — read out of the
bars. That is a good idea and it is the Plan's own, but it would void this study
as registered, because a log that cannot say who set a value cannot attribute a
hit. A trend computed from a trade count, credited to the operator, would
confirm the wrong hypothesis and nothing afterwards could separate them.

So every setting now records a `source`:

| | |
|---|---|
| `manual` | a person's own reading. **The only kind this study counts.** |
| `adopted` | a computed value a person accepted. Excluded: the judgement exercised was whether to accept, not what to set. |
| `derived` | written without a person. Excluded. |

Added before anything could write it, which is the only time it is free, and
defaulted to `manual` because until now nothing else could set a trend.

The amendment narrows the sample and does not touch the rule, the horizon or the
stopping count. Nothing has been measured, and the 22 settings already in the
log fall before the cutoff above and are excluded twice over.

**`adopted` is excluded deliberately, and it costs something.** If the derived
trend turns out to be good, the operator will accept it often, and those are the
settings that will accumulate fastest — so this study will run slower the more
useful the computed one is. Measuring whether the derived trend predicts
anything is a separate question with a separate sample, and it needs its own
registration rather than a share of this one.

## How long this takes, stated before anyone waits for it

One-sided, α = 0.05, 80% power:

| True hit rate | Calls needed | At 2 calls a week |
|---|---|---|
| 55% | 617 | 5.9 years |
| 58% | 240 | 2.3 years |
| 60% | 153 | **1.5 years** |
| 65% | 67 | 0.6 years |
| 70% | 37 | 0.4 years |

And the same arithmetic the other way, which is the binding one:

| Calls accumulated | Smallest hit rate it can resolve | Months at 2 a week |
|---|---|---|
| 30 | **71.9%** | 3.5 |
| 50 | 67.2% | 5.8 |
| 100 | 62.3% | 11.5 |
| 200 | 58.7% | 23.1 |

**Read the second table before starting.** At three months this study can only
detect a 72% hit rate on macro direction. Nobody has one. So an early look is
not an early answer, it is a guaranteed INCONCLUSIVE, and treating it as
evidence either way is the error this whole table exists to prevent.

## The breadth problem, which is a design consequence

§0.4a says the detection threshold is fixed by calendar span and that breadth —
uncorrelated bets held at the same time — is the only fast lever. **This study
has a breadth of one by construction.** There is a single global trend, so there
is one call at a time however many assets exist.

That is the price of the simplification made on the screen. A trend per asset
would give ten or forty calls where this gives one, and the table above would
read in months rather than years. It was removed for a good reason — with no
dates there is nothing to order two contradictory trends by — but the reason was
about the interface and the cost lands on the measurement. Recorded here so the
trade is visible rather than discovered when the study fails to conclude.

## Confounds, recorded in advance

* **The operator sees this screen.** Fear and Greed sits beside the dial, so a
  trend set after reading it is partly that index and not only the news. What is
  measured is therefore "the operator with this screen", which is the thing that
  will actually be used, but it is not "the operator's own judgement".
* **Calls are not independent of the market.** A trend is likelier to be set
  after a large move, so the sample is drawn from moments that are already
  unusual. The distribution of prior 7-day returns at entry is reported with the
  result.
* **Frequency is a free variable.** The operator decides when to call, and a
  hit rate over self-selected moments is not the same as one over a calendar.
  The count and spacing of calls are reported and not adjusted for.
* **BTCUSDT is a proxy for "the market"** and a poor one in an alt-led move. It
  is used because it is the series already collected at the resolution needed.

## What counts as an answer

* **Hit rate clearly above 0.5, with enough calls for the table above to make it
  resolvable** → the reading carries information, and the next question is what
  to do with it, which is a different registration.
* **Inconclusive** → the effect is smaller than the accumulated calls can see.
  The answer is more calendar, not a longer look at the same window.
* **At or below 0.5** → reported as a refutation, and the §20 tilt should come
  off the screen rather than stay on it as decoration.

## What must not happen

No horizon sweep: 1-day and 3-day are run **once** as a declared sensitivity,
reported in full, and the decision rests on 7 days whatever they say. No dropping
calls that were "obviously about something else". No re-labelling a `NORMAL` as a
weak directional call to raise the count. No peeking at the hit rate to decide
when to stop — the stopping point is a count fixed here, **150 directional
calls**, and the first look is at that count and not before.
