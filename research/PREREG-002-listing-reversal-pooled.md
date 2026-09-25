# Pre-registration 002 — The same hypothesis, a second venue

Registered 2026-09-25, before any Bybit listing return was computed.

## Why this is replication and not a second trial

RESULT-001 returned inconclusive: annualised Sharpe +0.515 on 531 events
against a minimum track record of 895. The shortfall is observations, not
effect size.

The configuration below is **identical** to PREREG-001 — same direction, same
entry, same holding period, same weighting, same costs. Nothing is searched.
The trial counter exists to price a search over configurations on one sample;
re-testing one fixed configuration on additional independent events is
replication, and the deflation arithmetic treats it as a single trial on a
larger N. Had anything been adjusted in light of RESULT-001, this would be a
second trial and the resolvable Sharpe would rise from 0.633 to 0.80.

The real hazard here is not configuration search but **sampling to a foregone
conclusion** — adding venues until the number crosses. So the stopping rule is
fixed now: Binance plus Bybit, reported once, whatever it says. A third venue
would require its own registration written before its data is fetched, and
would be declared as such.

## Inclusion rule, fixed before running

Bybit `instruments-info` gives `launchTime` directly, so no announcement parser
stands between the hypothesis and the data on this venue.

Measured before registering, on listing dates alone and no returns:

* 777 Bybit USDT perpetuals
* **417** whose base token never appeared in the Binance cohort
* 360 in both venues; of those, 151 separated by more than 30 days

**Only the 417 Bybit-only tokens are included.** A token that listed on both
venues within days is one price move observed twice, and pooling it would
inflate N while adding no information — which is precisely the failure the
power argument exists to prevent.

The 123 tokens listed on both but more than 90 days apart are arguably separate
events. They are **excluded anyway**, because "arguably" is doing load-bearing
work and the threshold that makes them qualify is a knob I would be choosing
after seeing RESULT-001. Excluding them costs observations and buys the right to
report one number.

Expected pooled N: 531 + 417 = **948**, against the 895 that MinTRL requires.
That the two numbers are close is the reason this is worth running and also the
reason the stopping rule above is written down.

## Known weakness, recorded in advance

Bybit's endpoint returns **currently trading** instruments. Delisted Bybit
perpetuals do not appear, so the Bybit half of the cohort is survivor-only in a
way the Binance half is not — there, delisted contracts were recoverable and
exposure was measured at 4%.

Survivors of a listing cohort are the ones that went up, and this hypothesis is
**short**. The bias therefore works *against* the registered direction: it
removes the listings that collapsed, which are the ones a short profits from. A
positive result would be achieved despite it. A negative or inconclusive result
cannot be attributed to it without argument, and that asymmetry is why the run
is still worth making.

The magnitude is not measurable from this endpoint. It is not estimated here and
will not be estimated after the fact.

## What counts as an answer

* Sharpe above 0.633 with the gate passed, at N≈948 → the hypothesis survives
  replication on independent events and earns a sizing study.
* Inconclusive again → the effect, if real, is smaller than roughly 0.6, and no
  further venue-stacking is warranted without a different mechanism.
* Negative → reported as a refutation of the pooled hypothesis.

The tail remains unaddressed and this registration does not address it. RESULT-001
found a worst case of −202.7%; no stop, filter or sizing rule is introduced here,
because each one is a trial and this study has no budget for one.
