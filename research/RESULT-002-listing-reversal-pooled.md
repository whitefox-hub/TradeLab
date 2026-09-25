# Result 002 — Pooled replication, and a flaw in its own design

Run 2026-09-25 against `research/PREREG-002-listing-reversal-pooled.md`
(`7ab340f`), committed before any Bybit return was fetched. The configuration
was not altered.

## Outcome as registered

| Cohort | n | Net mean | Median | Win | Sharpe |
|---|---|---|---|---|---|
| Binance | 531 | +1.99% | +8.36% | 65.3% | **+0.515** |
| Bybit-only | 404 | −1.12% | +1.51% | 56.9% | **−0.297** |
| **Pooled** | **935** | +0.65% | +4.49% | 61.7% | **+0.222** |

Resolvable at one trial: 0.631. Minimum track record: 8,053 observations against
935 available.

**Verdict: inconclusive.** The replication did not reproduce the Binance result.
The two venues disagree in sign.

## The design was wrong, and the flaw is in the registration

PREREG-002 argued that Bybit-only tokens are independent observations of the
same phenomenon. Checked after the run, they are not, for a reason that was
available before it and that I did not check:

**They are a different population.** The inclusion rule was "listed on Bybit,
never on Binance". That is not a neutral sampling criterion — it selects
precisely the tokens that failed Binance's listing bar. Smaller, thinner, lower
quality by the larger venue's own judgement.

**They are a different era.** Median listing year 2026 against Binance's 2024,
and **264 of the 404 are from 2026 alone** — two thirds of the cohort drawn from
the last nine months.

| | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| Binance | 30 | 22 | 97 | 87 | 186 | 45 |
| Bybit-only | 17 | 6 | 10 | 47 | 60 | **264** |

So the pooled figure of +0.222 is not one hypothesis tested on 935 comparable
events. It is two populations averaged, and the average is not a quantity anyone
asked about.

The survivorship weakness *was* registered in advance and still holds: Bybit's
endpoint returns only live instruments, so the collapsed listings — the ones a
short profits from — are missing from its half. That biases Bybit's −0.297
downward. Two of the three confounds were therefore invisible to me at
registration, and one was not.

## What is and is not concluded

The pooled result stands as registered: **inconclusive**. The pre-registration is
binding, and discarding the Bybit half now because the design turned out to be
poor would be exactly the manoeuvre the whole apparatus exists to prevent. The
number is reported.

What cannot be concluded is that the hypothesis is refuted. A negative result
from a cohort that is confounded three ways refutes nothing.

What also cannot be concluded is that Binance's +0.515 survives. It was
inconclusive on its own sample and remains so.

## The lesson, which is larger than this study

Plan.md §0.4 argues that new listings give "a genuinely independent
cross-section" and that a second venue therefore adds observations cheaply. This
run shows the second half of that is false as stated:

> **Listings are not exchangeable across venues.** The set of tokens listed on
> venue B and not on venue A is selected by A's rejection of them. Pooling
> across venues does not add independent draws from one population; it mixes
> two, and the selection runs along exactly the dimension — asset quality — that
> the hypothesis is about.

§0.4 should be amended to say so. The route to more observations for this
hypothesis is **time**, not venues: the cohort grows by roughly 78 Binance
listings a year on its own.

## What must not happen next

No stop, no volatility filter, no holding-period sweep, no re-run of the mirror
direction. RESULT-001 found a −202.7% worst case and the tail remains the real
obstacle; every candidate fix is a trial, and at twelve trials this data
resolves only 1.19. The hypothesis is parked, not tuned.
