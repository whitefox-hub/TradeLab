# Finding 005 — A rolling window pays for every shock twice

Found 2026-09-26 while decomposing RESULT-004. It is not a fact about funding
spreads; it is a fact about an estimator this project uses in three places.

## The mechanism

A rolling standard deviation over N bars has **two** edges. It takes an
observation on bar *t*, and it drops that observation on bar *t+N*. The drop
moves the estimate as sharply as the take did.

Nothing happened on bar *t+N*. The move is caused entirely by the window's
trailing edge arriving at an old observation, and it is indistinguishable, to
anything reading the estimator, from news.

Measured directly on a synthetic series with one shock bar:

| Window | Share of the shock's influence that vanishes in a single bar | Which bar |
|---|---|---|
| 60 | **99%** | shock + 59 |
| 100 | **100%** | shock + 99 |

Not "a jump". The entire memory of the shock leaves in one step, on schedule.

## What it cost, where it was measured

PREREG-004 sized a book by inverse trailing volatility on a 90-day window.
Resize cost came to 167% of gross return, and it was not spread out: **4.5% of
days carried 75.1% of it**.

The panel's largest single day is 2025-11-07, at +1.21% — already known to be
66.6% of the whole window's return. It leaves the 90-day window on 2026-02-06,
and that day's resize is **1.829 of leverage, the largest of all 640 days**. A
quarter after the fact, for no reason present in the market that day, the rule
traded almost the entire book.

The strategy paid for that day twice: once when it happened, and once when the
window let go of it.

## Where else this sits

Sizing is where it took money, but the estimator is the same wherever it drives
a decision rather than being read as an input:

| Site | What the estimator decides | What the cliff does |
|---|---|---|
| `labeling/barriers.py` | Barrier width | Labels a bar as touching nothing, because the barrier widened for a shock a full window in the past |
| `regimes/classifier.py` | The regime label | Files a day under the wrong regime |

The second is the one that bites this project specifically. §13.1's claim is
that correlation is a property of the **regime**, and FINDING-004 confirmed it
the expensive way — pooled breadth of 151 against 11 / 0 / 103 / 4 by quarter.
If the regime labels themselves carry a cliff, the per-regime correlations are
attributed to the wrong regime, and the measurement that the whole design now
rests on is measuring partly the window.

## The fix, and the boundary around it

Both sites now use an exponentially weighted standard deviation of the same
span. An EWM has no trailing edge — only decay — so a shock fades instead of
falling out. `tests/unit/test_estimator_echo.py` holds the line: it fails on
4 of 4 parameterisations with the previous implementation and passes on all
with this one, and it carries a control asserting the shock is still *seen*
when it happens, so an estimator flattened until nothing moves it would not
pass.

**The boundary is deliberate.** `features/volatility.py` still computes a
rolling standard deviation, and should. A feature is an input a model reads and
can learn to discount; a feature named for a rolling window ought to be one, and
its declared lookback is exact. The defect is not the estimator — it is an
estimator with a trailing edge driving an **action**.

The invariant tested is about the *shape* of forgetting, not its speed. An
estimator is entitled to remember a shock for a long time. It is not entitled to
forget it all at once on a bar where nothing happened. The first version of this
test asserted that two series agree a full window after a shock, which no honest
estimator can satisfy, because remembering is the point.

## What the change costs, stated rather than absorbed

On synthetic bars with a dozen shocks: **4.3% of barrier labels** and **13.0% of
regime labels** change. That is a large edit to the training signal, and it is
free only because of timing — nothing committed uses either site yet. No
registered result moves. Every existing synthetic test of both, including the
labelling ground-truth and uniqueness suites, passes unchanged, which is the
evidence that the properties those tests pin were not what the cliff was
providing.

One gap found and not closed: the golden digest is a thread-determinism canary
over a group-by, so it does not pin labelling output. A change to every label in
the project passed it without comment. The synthetic suites caught the
behaviour, but nothing pins the bits.
