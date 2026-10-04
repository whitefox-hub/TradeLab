# TradeLab — Quantitative Research, Simulation and Trading Platform

> Architecture specification. Supersedes the original brief, which is preserved verbatim in Appendix A.
> Status: implemented through the simulator rung (§12). One strategy runs; see "Current state" below.

---

## Current state — 2026-10-01

**One strategy runs: the BTC ladder**, designed by the operator. It splits its budget into
parts (now 20), each bought at its own price and sold on its own, and never sells at a loss.

| Rule | |
|---|---|
| Entry | the ladder adapts both ways. Down: a new part each time the price is **1.5%** below the cheapest part held. On a correction: a new part each time the price falls **5%** from its high since the last buy (or the last full exit), at least 1.5% from every part held. The first part is bought at once on the first start |
| Exit | a part's minimum net gain grows with the days held: under 15 days +1%, 15–29 +3%, 30–44 +5%, 45–59 +8%, 60–89 +12%, 90 and over +20%. Once there it follows its high and sells on a 2% pullback, never below the minimum, and always at its cap (twice the minimum, at least +10%) |
| Never at a loss | a part below its minimum waits, however long it takes |
| Size | one part is the budget divided by the number of parts (now **$2,000 in 20 parts of $100**), grown by every realised profit, inside a $10,000 fake account so the parts stay within the active risk limits (§14.1); a part must be at least $50 |
| Costs | Binance spot: 0.10% fee and 1.5 bp slippage on each side, checked every 10 seconds |

| Where | |
|---|---|
| Rule | `tradelab/strategy/btc_ladder.py` (tests in `tests/unit/test_btc_ladder.py`) |
| Runner | `scripts/run_btc_ladder.py`, state in `data/forward/btc-ladder/` |
| Binance Demo | since 2026-10-03 the same rule also runs with `--venue demo`: real orders to Binance's Demo Mode (`tradelab/execution/binance_spot.py`, `BINANCE_TESTNET_*` keys), its own run ("BTC ladder (demo)") and state in `data/forward/btc-ladder-demo/`, started and stopped with the others. A buy's commission is taken in BTC, so a part counts only the whole lots (0.00001 BTC) that arrived and every decision is on what a sale can deliver; the remainders build up and leave with a later sale. With $100 parts that rounding costs up to ~0.8% of a part until it is swept, which the simulator does not have. `--venue live` is refused until the risk limits are decided for the real $2,000 sub-account (they would stop it at its first correction) |
| Two databases | since 2026-10-03, in the same Postgres: `tradelab`, the laboratory (research, the simulator, Binance Demo, every test), and `tradelab_live` (`TRADELAB_LIVE_DB`), real money only. The runner picks by mode and cannot cross them, and the setting refuses a live database named like the laboratory's. `tradelab_live` has the full schema (0022), the `binance` venue and a spot cost model (0.10% maker and taker, the sub-account's rates), and deliberately no risk limits: they are to be decided for the real account, and nothing live can start without them. New migrations are applied to both (`POSTGRES_DB=tradelab_live alembic upgrade head`) |
| Start / stop / status | `./tradelab.sh start` · `stop` · `status` (database, capture, the app, the book) |
| Where it runs | since 2026-10-04 the books (simulator and Demo) and the app run on the Contabo server, not on the development machine, so a reboot or a sleeping laptop no longer stops them: user `trade_lab`, `/home/trade_lab/TradeLab`, systemd units `tradelab-app` and `tradelab-ladder@sim` / `@demo` (restart on failure and at boot), Postgres in Docker on 127.0.0.1 (`/home/trade_lab/deploy`), nightly dumps of both databases and the books state to `/var/backups/tradelab` (30 days). The app is reached through `ssh -L 8010:localhost:8010`. Locally `./tradelab.sh start` no longer starts the books unless `TRADELAB_RUN_BOOKS=1`: two copies on one Demo account would each act on their own parts |
| Screen | `http://127.0.0.1:8010/home`: the position and its P&L; the **BTC ladder** card (price, the high since the last buy, the next buy down and on a correction, and every part's cost, gain, minimum, cap and sell point, published by the book every 10 s and read by `/api/ladder`); a **Close** button that sells every part at market, even at a loss, and pauses buying for 24 hours; **Buy one part now** and a **Sell** button on each part, at market, the sale even at a loss after a confirmation that shows the gain, without pausing anything; the ladder's sales with how each part came in and went out. Whatever the operator does by hand is marked **MANUAL** on the part, the sale, the order's intent and the audit trail, and a part bought by hand then sells by the same rules as any other |
| Parameters | `http://127.0.0.1:8010/parameters`: budget, parts, step, correction and pullback (now $2,000, 20, 1.5%, 5%, 2%), validated as typed and on the server. Saving ends the run and starts a new one under the new numbers within a minute; the time-scaled minimums are fixed in the code. The screen offers nothing else |
| Other screens | Audit and Risk limits, which page their lists 100 records at a time |

**What the history says about it** (BTC spot hourly closes, $10,000, the settings now in force:
20 parts, 1.5% step, 5% correction, 2% pullback): 2018-01 → 2026-09, $56,557 against $61,242
for holding BTC, with a worst drawdown of −65% against −77%; 2022-01 → 2026-09, $19,424
against $18,176, with −54% against −67%; 813 sales in 8.7 years, on average 64% of the money
in BTC. Sales rarely coincide. The parameters were tuned with the operator on these same
years, so these figures flatter it; a walk-forward of the tuning (choosing each year
from the years before it) earned less than any fixed choice. The forward run is the test.

The ladder replaced the **BTC +1%** book (one position, sold at +1%, bought again at once),
whose run is ended in the database.

**Removed, at the operator's instruction:**

* the forward books: the PREREG-009 trend book, the four BTC trend books (SMA 150, SMA 120,
  SMA 120 vol-sized, hold BTC) and Flash Strike. Their runs are ended in the database, not
  deleted, and the home screen lists open runs only;
* the studies PREREG-007 to PREREG-019, their results, scripts and outputs. They remain in git
  history before commit `3335646`. PREREG-001 to 006 and the FINDINGs are kept;
* the macro screen and its catalogue (§20.6, retired 2026-09-28): no strategy used them. The
  tables remain;
* the Algorithm and Market Sentiment screens, 2026-10-03, with their APIs (`/api/algorithm`,
  `/api/scenario`, `/api/fear-greed`) and the trend and fear/greed cards on Home: they belonged
  to the removed trend book and the ladder reads neither. The tables (`market_conditions`,
  `scenario_runs`, `sentiment_readings`) and the `tradelab/scenario` package remain;
* dead code, 2026-10-01: modules nothing imported (`engines/tier_b`, `orchestration/persist`,
  `storage/dataset`, `validation/baselines`, `validation/splits`), the 5-minute dataset
  pipeline of the Flash Strike studies, and unused functions. Comments were removed from the
  code (checked to leave every file's syntax tree unchanged); docstrings stay. Sections of
  this specification that describe those modules are design, not inventory.

What those studies established, in one line each: no 5-minute shock rule of 10,368 paid its
costs (011); taking +1% without ever realising a loss converts BTC's direction into small
realised gains and large carried losses, under every refinement tried (013, 014, 018, 019);
the one rule that beat holding BTC out of sample was long BTC above its 150-day average, flat
otherwise (015).

---

## 0. The question

The entire system exists to answer one question with statistical honesty:

> **Do we have sufficient evidence that this strategy works out-of-sample, across different market regimes, after discounting the number of hypotheses we have tested?**

That last clause was missing from the original brief and it reshapes the whole design. If you test a million combinations, the best of them will show a spectacular Sharpe ratio **even if the data is pure noise**. A lab that does not keep count of how many hypotheses it has evaluated is not a lab: it is a false-positive generator with a lot of infrastructure around it.

Design corollary: **the trial counter is a first-class citizen of the system**, on par with the data and the results.

### 0.1 Time horizon — the decision that constrains everything else

The brief never fixed a holding period, and almost every parameter downstream depends on it: bar resolution, label horizon, purge size, which data sources matter, the latency budget, the cost model, and above all whether an edge is verifiable at all.

It is not a matter of taste. Two constraints squeeze from opposite directions and their intersection is narrow.

**Constraint 1 — costs rise as the horizon shortens.** Liquid perpetual majors, retail fee tier, all-in round trip (taker fees both sides + spread + slippage) ≈ **0.13%**. Assuming a position is held continuously, and counting **365 trading days**, because crypto does not close:

| Average hold | Round trips / year | Annual cost drag | Verdict |
|---|---|---|---|
| 4 hours | 2,190 | **~285%** | Dead on arrival without maker fills and an exceptional edge |
| 12 hours | 730 | ~95% | Not recoverable |
| 1 day | 365 | **~47%** | Demanding even with a genuine edge |
| 2 days | 183 | ~24% | Workable |
| 3 days | 122 | ~16% | Comfortable |
| 5 days | 73 | ~9.5% | Comfortable |
| 1 week | 52 | ~6.8% | Comfortable |
| 1 month | 12 | ~1.6% | Costs irrelevant |

An earlier version of this table counted ~250 round trips per year at a one-day hold, which is the equities trading calendar. Crypto trades every day, so every figure was understated by 46%, and it made short horizons look far more viable than they are. The numbers above come from `CostModel.annual_drag`, so the table is computed rather than quoted and cannot drift from the engine again.

**The table covers turnover only.** A long-biased perpetual position also pays funding, measured at 11.8% a year on BTCUSDT (§9.5), and that term does not shrink with the holding period — it grows with it. Beyond about two days the carry is the larger cost, which sharpens rather than moves the conclusion: the band stays 2–7 days, and a long-biased strategy inside it needs to clear turnover **plus** carry.

**Constraint 2 — statistical power falls as the horizon lengthens.** Usable perpetuals history is roughly 2019 onward, so six to seven years. The sample that matters is **non-overlapping trades**, and crypto cross-sectional correlation (~0.7–0.8 against BTC) means 20 assets behave closer to 3–4 independent ones:

| Average hold | Nominal trades, 20 assets × 6 yr | Effective independent | Verdict |
|---|---|---|---|
| 1 day | ~30,000 | ~5,000+ | Ample |
| 2–3 days | ~12,000 | ~2,500 | Sufficient |
| 1 week | ~6,000 | ~1,200 | Workable |
| 1 month | ~1,440 | ~300, heavily overlapping | Below the §8.8 floor |
| 3+ months | ~480 | ~100 | Unverifiable. Also mostly BTC beta in disguise |

The two tables intersect at **holding periods of roughly 2 to 7 days**. The lower bound moved out from one day once the drag was counted over 365 days rather than 250: at a one-day hold, 47% of annualised return is consumed before any edge is measured.

### 0.2 The decision

| Band | Hold | Status |
|---|---|---|
| Ultra-short / HFT | < 1 minute | **Excluded.** Requires colocation and competes against firms with structurally better latency. Out of scope per §19 |
| Short / intraday | 1 min – 4 h | **Excluded as a primary target.** Cost drag of 95–285%/yr is not recoverable at retail fee tiers. Revisitable only if maker execution is proven and fee tiers improve |
| **Medium / swing** | **2 – 7 days, centred on 3** | **PRIMARY TARGET** |
| Long / position | > 3 weeks | **Excluded as a research target.** Not enough independent observations to validate; returns are dominated by BTC beta, which is obtainable by holding BTC at zero research cost |

Signals are evaluated on **1h and 4h bars**, with 15m available for entry timing. Triple-barrier maximum holding (§7) is set in the 2–7 day range, which in turn fixes the purge and embargo sizes in §8.2.

Why this band is also where the edge plausibly lives, not merely where the arithmetic permits it: the data this project is built around has its signal at exactly this frequency. Funding settles every 8 hours. Liquidation cascades resolve over hours. Open-interest build-ups and unwinds run over days. News reaction residuals (§11.3) decay over hours to days. None of that is visible at monthly horizons — it has already averaged out — and none of it is tradeable at second horizons without L3 microstructure and colocation. The market's own clock, for the features in this plan, runs at hours to days.

Practical consequences, applied throughout: latency budget is hundreds of milliseconds, not microseconds, so no colocation and no exotic infrastructure; L2 order book drops from critical to useful (§4.5), which is what keeps storage tractable; and funding, OI, liquidations and basis move to the top of the data priority list, where they already sit in §4.1.

### 0.3 What "profitable" realistically means here

Stated plainly, because the number matters more than the ambition.

**Expected outcome for a strategy that honestly clears §8**: out-of-sample Sharpe of 0.7–1.3, and plan for a 30–50% haircut between backtest and live. At a 15–20% annualised volatility target that is roughly **10–20% per year, with drawdowns of 15–25%**. A validated strategy showing backtested Sharpe above 2.5 at this horizon is far more likely to be a leak than a discovery, and §8 exists to catch it.

**Base rate**: most hypotheses fail. Expect a low single-digit percentage of tested ideas to survive honest validation, and expect some of those to decay within a year (§14.6). This is the normal state of the field, not a sign the lab is broken.

**Profit is return × capital.** A 15% year on €10,000 is €1,500. No architecture changes that. What the lab produces in its first year is not income — it is a *verified* edge and the machinery to keep finding more. That edge scales with capital up to the joint capacity ceiling (§13.5), which at this horizon and in liquid majors sits far above any realistic starting capital, so capacity will not be the binding constraint early. Capital will be.

**Timeline to the first euro**: phases 0–4 are roughly 4–6 months before a strategy can even be a candidate, then 60 days of simulator and 2 weeks of testnet before real money. Anything promising profitability sooner is skipping the validation, and the strategies that skip it are the ones that lose money live while looking excellent in the backtest.

This section is the honest answer to "I want profitability": the fastest route to *profit* is not the fastest route to *evidence*, and without evidence there is no profit, only variance that has not revealed itself yet.

### 0.4a What the evidence horizon actually permits

Added after three inconclusive campaigns, all inconclusive for the same reason.
This section governs §0.4 and should be read before it.

**The smallest annualised Sharpe a study can resolve depends on the calendar
span of the data, and not at all on how many bets it contains.**

Measured with `scripts/triage_hypotheses.py` on this system's own power module,
holding the span at 6.8 years and varying the betting frequency:

| Observations | Bets per year | Resolvable Sharpe |
|---|---|---|
| 82 | 12 | 0.637 |
| 530 | 78 | 0.632 |
| 2,482 | 365 | 0.631 |
| 59,568 | 8,760 | 0.631 |

A 726-fold increase in sample size moves the detection threshold by less than
one percent. Holding frequency fixed and varying the span instead:

| Years | Resolvable Sharpe |
|---|---|
| 1 | 1.656 |
| 3 | 0.952 |
| 7 | 0.622 |
| 15 | 0.425 |
| 30 | 0.300 |

The rule is **resolvable annualised Sharpe ≈ 1.65 / √years** at one trial. It is
not an artefact of this implementation; it is the standard result that the
t-statistic of an annualised Sharpe scales with the length of the record and not
with sampling frequency, because the estimate's noise and the annualisation
factor scale together.

**What this invalidates.** §0.4 ranked its hypotheses partly on how many
independent events each could produce, and said of new listings that a fresh
cross-section "raises statistical power by construction". For *annualised
Sharpe* that is false. Sequential events do not lower the bar. PREREG-001 and
PREREG-002 were both spent discovering this the expensive way.

**What this leaves.** With roughly seven years of perpetual history, no single
pre-registered hypothesis can resolve an annualised Sharpe below about 0.62.
Three responses, and only three:

1. **Wait.** The threshold falls as 1/√years and nothing else moves it. Reaching
   0.42 means fifteen years of history.
2. **Search less.** Already at one trial, which is the floor. Twelve trials would
   put the threshold at 1.19.
3. **Raise the signal instead of lowering the bar.** This is the only lever that
   acts quickly, and it is the one §0.4 should have been ordered by.

**Breadth raises the achievable Sharpe; it does not lower the detectable one.**
`IR ≈ IC × √BR` counts bets held *at the same time*, not one after another. A
strategy holding fifty weakly-correlated positions concurrently multiplies its
information ratio by about seven against a single-name version of the same
signal, and its track record still spans the same seven years. That is how a
real effect gets above 0.62 — not by finding more events, but by holding more of
them simultaneously.

So the operative question for every hypothesis is **how many uncorrelated
positions can be open at once**, and §0.4's ordering is corrected accordingly:

| Hypothesis | Concurrent bets | Verdict |
|---|---|---|
| 3. Cross-venue funding dispersion | many, one per asset per venue pair | **favoured** |
| 1. Capacity-constrained assets | many, one per name in a 120-name cross-section | **favoured** |
| 5. Event reaction residual | several, overlapping event windows | viable |
| 4. Post-liquidation-cascade reversion | few, cascades cluster in time | weak |
| 2. New listings | **one at a time** | parked — see research/RESULT-002 |

New listings fail this test badly. A listing is held alone for seven days, so
breadth is approximately one no matter how many listings accumulate. That, and
not the cross-venue confound, is the deeper reason PREREG-001 could not have
succeeded: at breadth one, the signal would have had to carry an annualised
Sharpe above 0.62 by itself.

### 0.4 Where the edge is hypothesised to live

A **skill** advantage requires being better than competitors with more people and
more money, which is a poor bet for one operator. A **structural** advantage comes
from constraints the competition has and you do not, and it is durable because
they cannot remove it.

| Structural advantages | Structural disadvantages |
|---|---|
| No capacity requirement: a €500k opportunity is worth having | No colocation, no competitive latency |
| No mandate to deploy: eighteen months in cash is permitted | Retail fee tiers, and VIP tiers unreachable |
| No venue restrictions: second-tier exchanges are available | One researcher |
| No LPs demanding explanations during a drawdown | No prime broker, no cross-venue margin |
| Iteration in a day rather than a three-week committee | Short history outside the majors |

The design rule that follows: **pursue only edges that live in the advantages and
do not require overcoming the disadvantages.** That excludes anything
latency-sensitive and anything fee-sensitive at high turnover, and points at what
is capacity-limited, requires patience, or suffers from operational fragmentation.

Where to look, in order:

1. **Capacity-constrained assets.** A fund with $50m cannot deploy into a
   strategy with $2m of capacity — not unwillingness, it simply does not move
   their needle while consuming the same research. Perpetuals ranked 30–150 by
   volume are effectively empty of institutional money.
2. **New listings.** Each is a fresh event, so a listing cohort is a genuinely
   independent cross-section — which is exactly what six years of BTC is not,
   being one correlated path. One venue yields far more than this section
   originally estimated: 583 Binance futures listings between 2019 and 2026, not
   50–200.

   **Amended after PREREG-001/002 (research/RESULT-002).** The claim that
   listings pool *across venues* is withdrawn. Listings are not exchangeable
   between venues: the set of tokens listed on venue B and not on venue A is
   selected by A's rejection of them, and that selection runs along asset quality
   — the same dimension the hypothesis measures. Measured on Bybit, the 417
   tokens Binance never listed were also concentrated in a different era, two
   thirds of them inside nine months.

   So the cross-section is independent *within* a venue and confounded *across*
   venues. More observations here come from time, not from stacking venues:
   roughly 78 Binance listings a year. Reaching the ~900 observations a Sharpe of
   0.5 needs at one trial is a matter of waiting, and that is the honest cost of
   this hypothesis.
3. **Cross-venue funding dispersion.** The same asset with different funding on
   eight exchanges. It requires fragmented margin and reconciliation in several
   places, which is precisely why institutions avoid it.
4. **Post-liquidation-cascade reversion.** Cascades are mechanical rather than
   informational: price moves because an engine must sell, not because anyone
   knows something.
5. **The dataset you build rather than buy.** Anything for sale is priced in. The
   event reaction residual of §11.10 is not sold by anyone, and it compounds: at
   two years you hold something a new competitor cannot purchase.
6. **The freedom to decline.** A fund *must* deploy. Most operators lower the bar
   because they cannot afford to reject everything for a year, and §15 exists to
   make lowering it an audited event.

**It is not free.** Worse data with more gaps, wider spreads that raise the drag
of §0.1, far more aggressive survivorship bias — mid-caps die, so the
point-in-time universe of §4.4 moves from advisable to essential — shorter
history per asset, and real venue risk. All of that is paid in data engineering
and operational exposure, which are controllable, rather than in latency and
capital, which are not.

---

## 1. What changed from the original brief

The original brief points in the right direction. These are the gaps found and how they are closed.

| # | Gap | Fix |
|---|---|---|
| 1 | No *multiple testing* control. It asks for "millions of combinations" and for anti-overfitting, but never connects the two. | Deflated Sharpe Ratio, PBO, Hansen SPA and Romano-Wolf. Trial count is a mandatory input. See §8. |
| 2 | No *purging* or *embargo* in cross-validation. | CPCV with purging and embargo. A naive temporal split leaks information through overlapping labels. See §8.2. |
| 3 | **What is being predicted** is never defined. It jumps from features straight to "rules". | Explicit labeling layer: triple barrier, meta-labeling, horizons. See §7. |
| 4 | No *null baselines*. "Good results" is not defined against anything. | Every experiment is compared against buy & hold, matched random entry and permuted signal. See §8.4. |
| 5 | No compute budget. A million event-driven backtests is not viable. | Multi-stage funnel with explicit cost. See §9. |
| 6 | A single timestamp per record → silent *look-ahead bias*, especially in news and macro. | `event_time` / `ingest_time` / `available_at` on every source. See §4.3. |
| 7 | Survivorship bias mentioned but not solved. | Point-in-time universe with listings and delistings. See §4.4. |
| 8 | Raw order book with no volume plan. That is TB/year. | Derived features are persisted; raw is capped to a short window and few assets. See §4.5. |
| 9 | TimescaleDB as the primary store. | Postgres for the registry, Parquet+DuckDB for the analytical archive, Timescale only for the hot window. See §3. |
| 10 | No lifecycle and no promotion *gates*. | Strategy state machine with hard criteria. See §15. |
| 11 | "Compare backtest vs paper" with no mechanism. | Same engine for both (nautilus_trader) plus a statistical divergence test. See §12. |
| 12 | MVP phases without acceptance criteria. | Every phase has a verifiable exit condition. See §18. |
| 13 | No data-leakage tests. | *Leakage canaries*: deliberately cheating features the suite must catch. See §16.5. |
| 14 | Live trading was a non-goal; it is now the destination, but "simulator → production" is only two rungs. | Four-mode ladder: backtest → simulator → testnet → live, with a capital ramp. See §12 and §14.5. |
| 15 | No risk controls or kill switch — mandatory the moment real money is involved. | Hard limits in an independent component, active from mode 1; state reconciliation; dead man's switch. See §14. |
| 16 | "Tuning parameters in the simulator" re-optimises on the live period and destroys the validation. | Explicit boundary between what is tunable in operation and what forces a return to the funnel. See §12.4. |
| 17 | Everything was single-strategy. A production book of N strategies can be one bet in disguise. | Portfolio layer: return-correlation clustering, HRP allocation, portfolio-level trial counting, joint capacity. See §13. |
| 18 | "Sustained degradation" was undefined, so retirement decisions would be made on feeling. | Pre-registered sequential test (CUSUM/SPRT) against the validated return distribution, conditioned on regime. See §14.6. |
| 19 | No statistical power analysis. Some edges are undetectable with 6–8 years of data regardless of compute. | Minimum detectable effect fixed before the search; searches below it are not run. See §8.8. |
| 20 | Cost model described only by its inputs; adverse selection on maker fills omitted entirely. | Full cost model spec, versioned in `cost_models`; promotion requires viability under pessimistic fills. See §9.5. |
| 21 | Backfilled and live-captured data silently disagree, contaminating backtest↔simulator divergence. | Live capture as a first-class source, periodic re-backfill comparison, blocking quality issues. See §4.6. |
| 22 | Six tables referenced in prose but never defined; feature-importance method unspecified. | Schema completed (`cost_models`, `oos_access_log`, `data_quality_issues`, `position_reconciliations`, `event_revisions`, `asset_universe`); clustered MDA under purged CV. See §5, §6.1. |
| 23 | No time horizon defined, although nearly every downstream parameter depends on it. | Horizon fixed at 1–7 day holds by the intersection of cost drag and statistical power; HFT and position trading excluded. See §0.1–§0.3. |
| 45 | §8.8's minimum detectable effect was a post-hoc diagnosis, so studies that could never conclude still spent trials — and the counter is monotonic, so they raised the bar for every study after them. | A precondition: the design is sized from a pilot that reads trade structure and never performance, and an underpowered study is refused rather than reported. Of four designs already run, only one could have concluded. See §8.8. |
| 44 | Macro had no screen, and nothing enforced that a consensus be recorded before its release. | Calendar, surprise in sigma, vintage availability and per-series freshness in the application; prices in Grafana. `consensus_at < scheduled_for` in the schema — a consensus recorded afterwards is a memory of one, and would make every macro surprise look predictive. See §20.6. |
| 43 | Macro listed as one Tier 2 line, hiding that it is two different things: continuous prices used as regime inputs, and scheduled releases where consensus makes surprise computable. Revisions and session alignment were undocumented. | Split in §4.7, with the policy rate treated as surprise rather than level, vintages required where a series is revised, and joins on `available_at` rather than calendar date. |
| 42 | Impact and direction existed as fields but with no defined unit, and a fourth event shape was missing: events that replace the baseline rather than deviate from it. | Ratings are in sigma of pre-event volatility, so model, human and outcome share one scale and calibration is computable. Regime shifts — war, venue collapse, a ban — go to §10 as a conditioning variable, since fitting a decay to them estimates a reversion that never happens. See §11.7. |
| 41 | The operator needs to annotate events, and an in-place edit would inject hindsight straight into historical labels through the interface. | Annotations are revisions with their own `available_at`, corrections separated from judgements by whether the value was knowable at publication, and judgements cannot be backdated. Operator calibration is tallied like the agent's. See §20.5. |
| 40 | Sign treated as the signal, and no shape for news that starts a process rather than being an event. | Sign is an input to the expected-reaction model; the residual is the signal. Three shapes — shock, schedule, process — the last a state machine whose transitions are measured separately and supersede their predecessors. See §11.7. |
| 39 | §11 modelled shocks only, so a scheduled event's anticipation had nowhere to live, and its schedule was assumed fixed when schedules are revised. | Anticipation path keyed on time-until, versioned `schedule_revisions`, and three distinct ways a signal ends rather than one decay constant. See §11.6, §11.8. |
| 38 | The engine ignored funding entirely, understating the cost of any long-biased perpetual strategy by 11.8% a year. | Charged against the position held and signed; on a real BTCUSDT study it moved the Sharpe from −0.45 to −0.59. Funding, OI and positioning features added, which is what the differentiation thesis actually rests on. See §9.5, §11.4. |
| 37 | Walk-forward was specified and absent, so non-stationarity had no treatment and the only answer to "seven years is a lot" was to cut the window — which costs power without fixing the problem. | Rolling and anchored walk-forward with purge gaps, accumulating trials across windows, reporting parameter churn. On real BTCUSDT it returned −0.37 with 40% churn where a single split had shown +0.53. See §8.1. |
| 36 | §8.6's resampling was described but absent, leaving §14.6's "a drawdown inside the validated distribution is not evidence of failure" with no distribution to consult. | Stationary bootstrap, trade-reordering drawdown distribution and price jitter. The iid alternative was measured and produces intervals narrow enough to change verdicts. See §8.6. |
| 35 | Parameter stability was described but never implemented, though §15 lists it as a gate. | Surface analysis that promotes the neighbourhood median rather than the selection-biased peak, and chooses the centre of the best region rather than the best point. See §8.5. |
| 34 | Overlapping labels were counted as independent observations, inflating the effective sample by up to 218× and every significance figure with it. | Concurrency-based uniqueness; the engine reports `effective_n` and the statistics take it instead of the bar count. The phase 3 exit criterion still passes, now under the stricter condition. See §7. |
| 33 | Regime classification with full-sample thresholds labels the past using the future, invisibly and flatteringly. | Expanding-window quantiles over past data only; regime labels are held to §16.3's truncation invariance like any feature, and unclassifiable warmup is marked rather than guessed. See §10. |
| 32 | PBO on net returns reads 0.000 on pure noise — not a broken estimator, but selection of minimum turnover, which persists out of sample without being alpha. | Computed on gross returns, with a turnover-correlation diagnostic so the confusion is visible; the turnover-matched random baseline is the control that catches what PBO cannot. See §8.3. |
| 31 | The §0.1 cost table counted 250 round trips a year at a one-day hold — the equities calendar. Crypto trades 365 days, so every drag figure was understated by 46%. | Recomputed at 365 days: a one-day hold costs 47%/yr, not 30%, and the horizon band moves out to 2–7 days. The table now comes from `CostModel.annual_drag`, so it cannot drift from the engine again. |
| 30 | Single operator collapses the roles, removing the separation of duties that stopped a threshold being lowered and used the same day. | Replaced with a temporal control: a 24h cooling-off before an approval may cite a relaxed gate, enforced in the approval path rather than the interface. See §20.3. |
| 29 | Operator interface never specified: no screens, no auth design, and an unresolved Grafana/application boundary. | Screen inventory by phase, the boundary rule, identity as an audit-trail requirement, and mode made non-editable by construction. See §20. |
| 28 | No limit on concurrent open positions in the enforced risk config, and no minimum position size at all. | `max_open_positions`, `max_positions_per_cluster`, `min_position_notional`; minimum viable size derived as `max(min_notional, lot_size x price)` point-in-time, and a floor added to the capital ramp. See §14.1. |
| 27 | Price levels and multi-asset selection absent, though both are how the system is meant to be operated. | Level families with declared confirmation windows, and a hard boundary between a backtestable selection rule and picking the winner. See §6.2, §6.3. |
| 26 | News handled as classification and magnitude only — no duration, no participation, no state conditioning, and no test of whether an effect outlives its trading costs. | Impulse response via local projections, two-component decay, in-house point-in-time state index, and a cost filter that rejects fast-decaying classes automatically. See §11.5. |
| 24 | Tests were scattered and framed as ordinary software testing, which catches none of the bugs that matter here. | Seven-layer suite built on invariants, synthetic ground truth and adversarial canaries; canary catch rate replaces coverage as the release criterion. See §16.2–§16.8. |
| 25 | Docker mentioned but not specified, although `image_digest` is already part of the reproducibility contract. | Three images split along the credential boundary, digest pinning, thread counts pinned for bit-identical results, execution behind compose profiles. See §2.2. |

---

## 2. Stack

| Layer | Technology | Rationale |
|---|---|---|
| Language | Python 3.12+, deps via `uv` | The only ecosystem with the statistics this project needs |
| Dataframes | **Polars** | Lazy, multithreaded, Parquet-native, predicate pushdown |
| Registry / operational | **PostgreSQL 16 + pgvector** | Referential integrity plus embeddings where the joins happen |
| Analytical archive | **Parquet (zstd, Hive) + DuckDB** | The real backtest engine; immutable and hash-versionable |
| Hot window | **TimescaleDB** (from phase 5 only) | Live ingestion + continuous aggregates |
| Artifacts | **MinIO** → S3 | Equity curves, blotters, feature matrices, models |
| Orchestration and lineage | **Dagster** | Software-defined assets *are* dataset versions. Lineage comes free |
| Backtest tier A | In-house vectorized engine (Polars/NumPy/Numba) | Cheap mass screening |
| Tier B, simulator, testnet and live | **nautilus_trader** | Rust core, event-driven; the same code runs backtest → simulator → testnet → live |
| Statistics | `scipy`, `statsmodels`, `arch`, `scikit-learn` | DSR, PBO, SPA, stationary bootstrap, CPCV |
| NLP | `sentence-transformers`, HDBSCAN, LLM with strict JSON schema | Event clustering and structured extraction |
| API / UI | FastAPI + SQLAlchemy + Alembic, React front end | Browsable registry, experiment comparison, lifecycle |
| Dashboards | **Grafana** over Postgres | Equity, ingestion health, drift. Zero charting code |
| Exploration | **marimo** | Reactive notebooks as plain `.py`, diffable, no hidden state |
| Environment | Docker Compose, three images pinned by `sha256` digest | The image is an input to the result, not a deployment detail (§2.2) |
| Tests | `pytest` + `xdist` + markers, `hypothesis`, `pytest-benchmark` | Seven-layer suite (§16.2): temporal invariants, synthetic ground truth, canaries, golden runs |

**Laravel** is kept out of the core for one concrete reason: in a hybrid architecture the core domain objects (`experiment`, `dataset_version`, `feature_definition`, the parameter contract) would exist twice and would drift. In an ordinary CRUD app that is an annoying bug; here it produces an experiment that **claims** to be reproducible and is not. See Appendix B for the Laravel variant if the decision is imposed externally.

### 2.1 Component map

Seven services with hard boundaries. Nothing calls across a boundary except through the interfaces named here.

| Service | Responsibility | Reads | Writes |
|---|---|---|---|
| `collectors` | One process per source. Backfill + stream + validate | Venue APIs | Parquet archive, `data_quality_issues` |
| `feature_engine` | Materialises feature matrices per (dataset, feature_set) | Parquet, `feature_definitions` | Parquet (features), artifact store |
| `experiment_runner` | Tier A/B/C execution, stateless workers | Parquet, registry | `experiments`, `experiment_results` |
| `validator` | Statistics only. Never runs a backtest | `experiment_results`, `studies` | `validation_reports` |
| `executor` | The only service that talks to a venue for trading | Live feed, registry | `runs`, `orders`, `fills` |
| `risk_engine` | Independent veto. Cannot be bypassed by a strategy | `runs`, positions | halts, `position_reconciliations` |
| `api` | FastAPI + UI (§20). Read-heavy; writes only approvals. Holds no venue credentials, so it cannot start live trading | Registry | approvals, lifecycle transitions |

Two boundaries matter more than the rest:

- **`validator` never runs a backtest, `experiment_runner` never computes a verdict.** Keeping measurement apart from judgement is what stops "the number came out low, let me adjust how it is computed."
- **`risk_engine` is a separate process from `executor`.** If it lived inside the strategy runtime, a bug in the strategy could disable it — and a kill switch that dies with the thing it is killing is not a kill switch.

Repository layout:

```
tradelab/
  collectors/        one module per source, all implementing Collector
  storage/           Parquet layout, manifests, DuckDB session helpers
  features/          registry + definitions, one file per family
  labeling/          triple barrier, meta-labeling, sample weights
  regimes/           point-in-time regime classification
  engines/
    tier_a/          vectorized screening
    tier_b/          nautilus adapters, cost models  (not built; removed 2026-10-01)
  validation/        CPCV, DSR, PBO, SPA, baselines, power
  portfolio/         correlation, allocation, aggregate capacity
  execution/         executor, venue adapters, reconciliation
  risk/              limits, kill switch, degradation tests
  registry/          SQLAlchemy models, Alembic migrations
  orchestration/     Dagster assets and jobs
  api/               FastAPI + schemas
  research/          marimo notebooks (never imported by anything above)
```

`research/` depends on everything; nothing depends on `research/`. It is where exploration happens and where nothing is allowed to become load-bearing.

### 2.2 Containerisation

Docker here is not a deployment convenience. `experiments.image_digest` and `runs.image_digest` are part of the reproducibility contract (§16.1): the image is an input to the result, exactly like the dataset and the git sha. Re-running an experiment means pulling **that digest**, not rebuilding from a tag.

Consequence: **every base image is pinned by `sha256` digest, never by tag.** `python:3.12-slim` moves under you; `python@sha256:...` does not. A tag in a Dockerfile silently breaks the golden run test (§16.6) months later, and the failure looks like a statistics bug.

#### Images

Three images, not one per service. Fewer images means fewer dependency sets to drift apart.

| Image | Contents | Used by |
|---|---|---|
| `tradelab-compute` | Python, Polars, scipy stack, nautilus, feature and validation code | collectors, feature_engine, experiment_runner, validator, dagster |
| `tradelab-exec` | Minimal: nautilus, venue adapters, risk engine. **No research dependencies, no notebook tooling, no LLM clients** | executor, risk_engine |
| `tradelab-api` | FastAPI, SQLAlchemy, built front end | api |

The split between `compute` and `exec` is a security boundary, not a size optimisation. §14.4 requires that LIVE credentials are reachable only by the execution process; that is enforceable only if the research image is a different image, on a different network, without those secrets mounted. A single fat image makes the requirement unenforceable no matter what the code does.

#### Services

```yaml
# docker-compose.yml (condensed — the structure, not the whole file)
services:
  postgres:
    image: postgres@sha256:...          # + pgvector extension
    volumes: [pgdata:/var/lib/postgresql/data]
    shm_size: 1g

  minio:
    image: minio/minio@sha256:...
    volumes: [artifacts:/data]

  dagster:
    image: tradelab-compute
    depends_on: [postgres]

  runner:                                # scaled: docker compose up --scale runner=N
    image: tradelab-compute
    volumes:
      - archive:/data/parquet:ro         # shared, read-only, memory-mapped (§9.6)
    deploy: { resources: { limits: { cpus: "4", memory: 8g } } }
    environment: *determinism

  api:
    image: tradelab-api
    ports: ["8000:8000"]

  grafana:
    image: grafana/grafana@sha256:...

  # --- execution: never started by a bare `docker compose up` ---
  executor:
    image: tradelab-exec
    profiles: [simulator, testnet, live]
    read_only: true
    secrets: [venue_key]                 # file mount, not environment
    networks: [exec_net]

  risk_engine:
    image: tradelab-exec
    profiles: [simulator, testnet, live]
    restart: always                       # separate container from executor, by design (§2.1)

volumes: { pgdata: , artifacts: , archive: }
secrets: { venue_key: { file: ./secrets/venue_key } }
```

Two safety properties come from this topology:

- **`profiles` means a bare `docker compose up` cannot start the executor.** Reaching a venue requires naming the profile explicitly (`--profile testnet`). Accidentally launching live execution while developing becomes impossible rather than merely unlikely.
- **`risk_engine` is its own container with `restart: always`.** §2.1 requires it to survive the thing it polices; a strategy bug that kills the executor leaves the risk engine up to cancel orders. A kill switch that dies with its target is not a kill switch, and a shared process cannot provide that guarantee.

#### Determinism inside containers

Float reduction order **can** depend on thread count, because parallel reductions combine partial sums in whatever order the threads finish. Whether it actually does is a property of the specific library and build, not a law: measured on the current stack (Polars group-by, OpenBLAS matmul), results are thread-invariant. Other builds and other libraries are not, and a GPU path almost certainly is not.

The response is not to assume either way. Threads are pinned as cheap insurance, and **thread-invariance is verified by a test** that runs the golden computation at 1, 2 and 8 threads and asserts the digests agree (§16.6). If a future dependency introduces the sensitivity, it is caught the day it lands rather than months later as unexplained non-determinism in a Sharpe ratio.

`PYTHONHASHSEED` is a separate matter and is not optional: set iteration order affects anything that builds a collection before reducing it.

Every compute container therefore pins:

```yaml
x-determinism: &determinism
  PYTHONHASHSEED: "0"
  OMP_NUM_THREADS: "1"
  MKL_NUM_THREADS: "1"
  RAYON_NUM_THREADS: "4"      # Polars; fixed, not derived from the host
  POLARS_MAX_THREADS: "4"
  NUMBA_NUM_THREADS: "4"
  TZ: "UTC"
```

Parallelism comes from running more `runner` containers, not from letting each one auto-detect cores. Throughput is identical, and a fixed thread count is one less variable when a result has to be reproduced on a different machine.

`TZ: UTC` is belt-and-braces: everything is `timestamptz` already (§5), but a container that believes it is in Madrid will produce confusing logs during the one week a year it matters.

#### Volumes and resources

- The Parquet archive is a **named volume mounted read-only into runners**, never baked into an image and never in the container writable layer. One copy per machine, memory-mapped by every worker (§9.6).
- Runners carry explicit CPU and memory limits. Without them a single backtest with an oversized feature matrix takes the host down, including Postgres.
- Postgres gets `shm_size: 1g`; the default 64 MB causes parallel query failures that surface as unrelated errors.

#### What Docker does not solve

- **Clock.** Containers inherit the host clock. §14.2's clock-drift requirement is satisfied by `chrony` on the **host**, and the measured offset per venue is recorded in `venues.clock_offset_ms`.
- **Latency.** Bridge networking adds tens to hundreds of microseconds. At the 1–7 day horizon fixed in §0.2 this is irrelevant by four orders of magnitude — one more thing the horizon decision buys. If it ever mattered, `network_mode: host` on the executor is the escape hatch.
- **Backups.** A volume is not a backup. §16.10 still applies in full.
- **Secrets.** Environment variables leak into `docker inspect`, logs and image history. Credentials are file-mounted secrets with restricted permissions, separate per mode (§14.4), and never present in the `compute` image at all.

---

## 3. Storage architecture

Four layers with hard boundaries. No component spans two levels.

### 3.1 Layer 1 — Registry (PostgreSQL + pgvector)

Everything that makes the system reproducible and auditable. Modest volume, maximum integrity.

Holds: experiments, strategies, hypotheses, dataset_versions, feature_definitions, runs, gates, news/events/embeddings, execution state, point-in-time universe.

Rules: real foreign keys, `JSONB` with constraints for params, all `timestamptz` in UTC.

### 3.2 Layer 2 — Analytical archive (Parquet + DuckDB)

**The primary store for backtesting**, not a secondary dump for "raw data".

```
s3://tradelab/market/
  trades/venue=binance/asset=BTCUSDT/year=2024/month=03/part-*.parquet
  ohlcv/tf=1m/asset=BTCUSDT/year=2024/...
  funding/venue=binance/asset=BTCUSDT/year=2024/...
  book_features/asset=BTCUSDT/res=1s/year=2024/...
```

Files are **immutable**. A correction is a new file with a higher `revision`, never an `UPDATE`. Zstd compression, row groups sized for the scan pattern (~128 MB).

### 3.3 Layer 3 — Hot window (TimescaleDB)

From phase 5 only. Hypertables covering the last 7–30 days for live ingestion and real-time features. Continuous aggregates for bars. Retention policy that compacts to Parquet and purges.

### 3.4 Layer 4 — Artifacts (MinIO/S3)

Equity curves, trade blotters, materialised feature matrices, models, reports. Content-addressed by SHA-256. The database stores the pointer, never the blob.

---

## 4. Data

### 4.1 Prioritisation

The original brief lists ~18 sources. Attempting all of them at once guarantees none of them is done properly. Ordered by signal-to-cost:

**Tier 1 — essential, free, phase 1**
Multi-timeframe OHLCV · trades/ticks · funding rates · open interest · liquidations · spot-perp basis · point-in-time universe

**Tier 2 — high value, moderate cost, phase 2**
L2 order book (derived features) · CVD / order flow · cross-asset correlations · BTC dominance · daily macro: policy rate, yields, DXY, gold, oil, SPX, NDX, VIX (§4.7)

**Tier 3 — expensive or of uncertain value, phase 3+**
Structured news · on-chain · options (IV, skew) · macro calendar with consensus and surprise

Rule: **a source does not enter the system until its quality test and its backfill procedure exist.** A source with silent gaps is worse than no source at all.

### 4.2 Ingestion contract

Every collector must implement:

```python
class Collector(Protocol):
    def backfill(self, start: datetime, end: datetime) -> Iterator[Batch]: ...
    def stream(self) -> AsyncIterator[Batch]: ...
    def validate(self, batch: Batch) -> ValidationReport: ...
    @property
    def schema(self) -> pa.Schema: ...
```

`validate` checks, at minimum: temporal monotonicity, absence of gaps longer than the source SLA, plausible ranges (price > 0, volume ≥ 0), duplicates by natural key, and cross-consistency (e.g. sum of trades vs bar volume).

Every anomaly is persisted to `data_quality_issues`; **nothing is silently corrected**.

### 4.3 The three timestamps

Non-negotiable rule. Every row carries:

- **`event_time`** — when it happened in the world
- **`ingest_time`** — when the system received it
- **`available_at`** — from which instant the system could have known and used it

`available_at` is the only one the backtest ever sees. For market data it is usually `event_time + latency`; for news it is publication time plus the feed delay; for macro it is the release time, and **later revisions do not rewrite the original row**, they create a new one.

Without this, the news engine and macro data inject look-ahead bias that goes undetected until paper trading looks nothing like the backtest.

### 4.4 Point-in-time universe

Table `asset_universe(asset, venue, listed_at, delisted_at, tick_size, lot_size, min_notional, fee_tier, valid_from, valid_to)`.

The backtest resolves the universe **as of the simulated date**, never the current universe. This is what kills survivorship bias: if 40 perpetuals existed in 2021 and 300 exist today, a 2021 backtest may only see 40. And the ones that died must remain in the data.

Exchange parameters (tick size, minimums, fee tiers) are point-in-time too: they change, and a backtest using today's values executes orders that would have been rejected at the time.

### 4.5 Order book: explicit policy

L2 at 100 ms with 20 levels across 20 assets is terabytes per year and will sink the project before a single strategy is validated. Policy:

- **Derived features are persisted** at fixed resolution (1s): imbalance per band, microprice, cumulative depth at ±5/±10/±25 bps, book slope, effective spread, time at best.
- **Raw L2 is retained** only for the 2–3 primary assets and on a rolling window (e.g. 90 days), enough to calibrate the impact model and validate the derived features.
- **Ticks, yes, in full**: far cheaper and the basis for realistic slippage.

### 4.6 Backfill and live capture must be reconciled

A trap that surfaces late and is usually misdiagnosed: REST backfill and the websocket stream do not return identical data. Aggregation differs, late trades get folded into earlier bars, and reconnections drop events. The consequence is specific and damaging — the backtest runs on backfilled data while the simulator runs on live-captured data, so §12.5 divergence gets contaminated by a data artifact and reads as a strategy problem.

Requirements:

- **The live stream is captured to the archive**, not just consumed. Live capture is a first-class data source with its own partition.
- **Periodic comparison**: a captured window is re-backfilled days later and the two are compared bar by bar. The discrepancy is persisted as a quality metric per source, not inspected ad hoc.
- **Where both exist, the backtest uses the live-captured version.** It is what the strategy would actually have seen.
- A discrepancy above threshold opens a `data_quality_issues` record and blocks promotion of anything validated on that window.

### 4.7 Macro: two different things wearing one label

Rates, oil, gold, DXY, yields, equity indices. They arrive under one heading and
split into two kinds that need entirely different handling.

**Continuous prices** — oil, gold, DXY, yields, SPX, NDX, VIX. These are traded
series, used as cross-asset features and as regime inputs (§10): correlation,
rolling beta, spreads, and whether the market is in a risk-on or risk-off state.

**Scheduled releases** — FOMC decisions, CPI, NFP. These are events of the
`schedule` shape (§11.6), and they carry an advantage no other event class has:
**consensus exists beforehand, so surprise is a number rather than an inference**.
That is why §11.1 puts macro at the top of the classes worth modelling despite a
thin sample.

#### The policy rate is not a price, and the level is not the signal

The Fed funds rate deserves separating out, because using it as a series is a
category error that looks reasonable.

It is a **level that changes at scheduled meetings**, and by the time it changes
everyone has known for weeks what was coming — rate futures price the expected
path continuously. A feature reading "the policy rate is 4.5%" carries almost no
information, because the market has been trading that 4.5% since before it
existed.

What carries information is the **surprise**: the decision minus what was priced,
and the revision of the *expected path* rather than of the current level. A
meeting that delivers exactly what futures implied is, informationally, a
non-event — and a model fed the level will see a large number and a large market
move on the same day and conclude they are related.

#### Macro is revised, and that is the trap

Severe enough to name separately. Employment, GDP and most activity series are
published and then **revised**, sometimes substantially, sometimes months later. A
backtest reading today's value for a date in 2022 is reading a number that did
not exist in 2022.

This is the classic macro backtesting error and it flatters, because revisions
move toward the truth and the truth is what subsequently happened.

§4.3's three timestamps handle it only if the **vintages** are stored: the value
as it was first published, plus each revision with its own `available_at`.
Fortunately this is solved upstream — FRED's ALFRED serves vintage data directly,
returning a series as it was known on a given past date, which is exactly the
point-in-time contract this system requires.

Where no vintage is available, the series is marked as such and any result
depending on it is caveated. A revised series used as if it were real-time is not
slightly optimistic; it is answering a different question.

#### Session alignment is a look-ahead trap

Crypto trades continuously; gold, oil and equity indices do not. A "daily" gold
bar closes at a session end in New York, and the UTC day containing that close
extends past it.

Aligning a gold close to the UTC day that contains it can therefore hand a crypto
bar information from **after** the period being predicted. The rule is the same as
everywhere else: a macro observation carries `available_at` = its publication or
session close plus the feed delay, and the join is on that, never on a calendar
date. Joining on dates is how a cross-asset study produces predictability that
evaporates in live trading.

#### Sources, all free

| Series | Source | Vintages |
|---|---|---|
| Policy rate, yields, activity data | FRED / ALFRED | **Yes** — ALFRED serves point-in-time |
| Consensus and actual for releases | Macro calendar feeds | Partially |
| Gold, oil, DXY, equity indices | Stooq, Yahoo daily bars | No — prices are not revised |

#### An honest caveat on relevance

The crypto-macro relationship is **not stationary**. It was strong through the
2022–2023 rate cycle and much weaker in other periods. A constant beta fitted
across the whole sample estimates an average of two different regimes and
describes neither.

So macro enters conditioned on regime (§10) rather than as a fixed relationship,
and a study claiming a macro effect has to show it in more than one regime before
§15's gate treats it as anything.

---

## 5. Data model

52 tables in Postgres, organised by domain. The count is deliberately modest because market data, feature matrices and tier A results do not live here — see §5.7.

Conventions throughout: all timestamps `timestamptz` in UTC; `code_hash`/`content_hash` are SHA-256 hex; `params`-style columns are `jsonb` with CHECK constraints against a registered JSON Schema; nothing is `UPDATE`d that represents a historical fact.

Tables are grouped by domain for reading, which is not creation order: nine foreign keys point forward (`studies→hypotheses`, `experiments→cost_models`, `experiments→regime_models`, `validation_reports→gate_configs`, `cost_models→venues`, `gate_configs→users`, `approvals→users`, `runs→portfolios`, `articles→events`). In Alembic those are added with `ALTER TABLE` after all tables exist, rather than reordering the schema into something nobody can navigate.

### 5.1 Research registry (17 tables)

```sql
-- Exactly which data existed
CREATE TABLE dataset_versions (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  spec            jsonb NOT NULL,        -- sources, assets, range, filters
  manifest_uri    text NOT NULL,         -- list of Parquet files + hash of each
  content_hash    text NOT NULL UNIQUE,  -- hash of the manifest -> dataset identity
  row_count       bigint,
  period_start    timestamptz NOT NULL,
  period_end      timestamptz NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Versioned definition of a feature (the code, not the output)
CREATE TABLE feature_definitions (
  id               uuid PRIMARY KEY,
  name             text NOT NULL,
  family           text NOT NULL,        -- momentum | volatility | orderflow | macro | news ...
  impl_ref         text NOT NULL,        -- module:function
  code_hash        text NOT NULL,
  param_schema     jsonb NOT NULL,       -- JSON Schema of admissible parameters
  lookback_bars    int NOT NULL,         -- max window -> drives purging and warmup (§8.2)
  is_point_in_time boolean NOT NULL DEFAULT true,
  UNIQUE (name, code_hash)
);

-- A materialised feature matrix. The heavy data is in Parquet; this is its identity.
CREATE TABLE feature_matrices (
  id                 uuid PRIMARY KEY,
  dataset_version_id uuid NOT NULL REFERENCES dataset_versions(id),
  feature_set        jsonb NOT NULL,     -- [{feature_definition_id, params}]
  content_hash       text NOT NULL UNIQUE,
  uri                text NOT NULL,      -- Parquet location
  n_rows             bigint NOT NULL,
  n_cols             int NOT NULL,
  built_at           timestamptz NOT NULL DEFAULT now()
);

-- How the target label is built
CREATE TABLE label_definitions (
  id              uuid PRIMARY KEY,
  method          text NOT NULL,         -- triple_barrier | fixed_horizon | meta
  params          jsonb NOT NULL,        -- pt, sl, max_holding, vol_target...
  horizon_bars    int NOT NULL,          -- with feature lookback, fixes the purge (§8.2)
  code_hash       text NOT NULL
);

-- A strategy is a rule template, not a parameterised instance
CREATE TABLE strategies (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  kind            text NOT NULL,         -- rule | ml | ensemble
  impl_ref        text NOT NULL,
  code_hash       text NOT NULL,
  param_space     jsonb NOT NULL,        -- declared search space
  constraints     jsonb NOT NULL,        -- forbidden combinations
  status          text NOT NULL,         -- draft|screening|validating|candidate|simulator|testnet|live_ramp|live|halted|rejected|retired
  UNIQUE (name, code_hash)
);

-- A search campaign. This is where the trial counter lives.
CREATE TABLE studies (
  id                    uuid PRIMARY KEY,
  strategy_id           uuid NOT NULL REFERENCES strategies(id),
  hypothesis_id         uuid REFERENCES hypotheses(id),
  search_method         text NOT NULL,   -- grid | random | tpe | cmaes
  planned_trials        int NOT NULL,
  executed_trials       int NOT NULL DEFAULT 0,  -- INPUT to DSR/PBO. Never reset.
  min_detectable_sharpe numeric NOT NULL,        -- fixed BEFORE the search (§8.8)
  hold_days_min         numeric NOT NULL,        -- horizon constraint (§0.2)
  hold_days_max         numeric NOT NULL,
  compute_budget_hours  numeric NOT NULL,        -- exceeded -> halt and ask (§9.6)
  compute_used_hours    numeric NOT NULL DEFAULT 0,
  created_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (hold_days_min > 0 AND hold_days_max >= hold_days_min)
);

-- An experiment = one concrete configuration, evaluated
CREATE TABLE experiments (
  id                  uuid PRIMARY KEY,
  study_id            uuid NOT NULL REFERENCES studies(id),
  dataset_version_id  uuid NOT NULL REFERENCES dataset_versions(id),
  feature_matrix_id   uuid REFERENCES feature_matrices(id),
  label_definition_id uuid NOT NULL REFERENCES label_definitions(id),
  params              jsonb NOT NULL,
  universe            jsonb NOT NULL,
  timeframe           text NOT NULL,
  cv_scheme           jsonb NOT NULL,    -- folds, purging, embargo
  splits              jsonb NOT NULL,    -- exact train/val/oos dates
  tier                smallint NOT NULL, -- 1 = vectorized screening, 2 = event-driven, 3 = full walk-forward
  cost_model_id       uuid NOT NULL REFERENCES cost_models(id),
  regime_model_id     uuid REFERENCES regime_models(id),
  seed                bigint NOT NULL,
  code_version        text NOT NULL,     -- git sha
  image_digest        text NOT NULL,     -- container digest
  config_hash         text NOT NULL UNIQUE,  -- identity -> dedup and cache
  realised_hold_days  numeric,           -- checked against studies bounds; out of band -> rejected
  status              text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (tier >= 2)                      -- tier 1 results live in Parquet (§5.7)
);

-- Results: one row per (experiment, split, regime). Tier >= 2 only.
CREATE TABLE experiment_results (
  experiment_id   uuid NOT NULL REFERENCES experiments(id),
  split           text NOT NULL,         -- train | val | oos | fold_k
  regime          text NOT NULL DEFAULT 'all',
  metrics         jsonb NOT NULL,        -- sharpe, sortino, calmar, mdd, turnover...
  n_trades        int NOT NULL,
  returns_uri     text,                  -- daily return series, for §13.1 correlation
  artifacts_uri   text,                  -- equity curve, blotter
  PRIMARY KEY (experiment_id, split, regime)
);

-- Statistical verdict, kept separate from the raw metrics
CREATE TABLE validation_reports (
  experiment_id      uuid PRIMARY KEY REFERENCES experiments(id),
  trials_considered  int NOT NULL,       -- from studies.executed_trials, incl. tier A discards
  deflated_sharpe    numeric,
  dsr_pvalue         numeric,
  pbo                numeric,            -- probability of backtest overfitting
  spa_pvalue         numeric,            -- Hansen SPA vs benchmark
  param_stability    numeric,
  min_trl_days       numeric,            -- minimum track record length
  regime_coverage    jsonb,
  baselines          jsonb,              -- buy&hold / random / permuted results (§8.4)
  cost_sensitivity   jsonb,              -- results under 1x / 2x costs, 3 fill models (§9.5)
  capacity_ceiling   numeric,            -- §12.7
  gate_config_id     uuid NOT NULL REFERENCES gate_configs(id),
  verdict            text NOT NULL,      -- pass | fail | inconclusive
  reasons            jsonb NOT NULL
);

-- Clustered MDA under purged CV. Descriptive only, never a gate (§6.1)
CREATE TABLE feature_importance (
  experiment_id   uuid NOT NULL REFERENCES experiments(id),
  cluster_id      text NOT NULL,         -- correlation cluster, not a single column
  members         jsonb NOT NULL,        -- feature_definition_ids + params in the cluster
  mda_mean        numeric NOT NULL,
  mda_std         numeric NOT NULL,
  PRIMARY KEY (experiment_id, cluster_id)
);

-- Hypotheses, including AI-generated ones
CREATE TABLE hypotheses (
  id              uuid PRIMARY KEY,
  origin          text NOT NULL,         -- human | ai_agent
  agent_model     text,
  statement       text NOT NULL,
  rationale       text,
  prior_belief    numeric,               -- recorded BEFORE execution
  created_at      timestamptz NOT NULL DEFAULT now(),
  outcome         text                   -- confirmed | rejected | inconclusive
);

-- Every look at the frozen OOS. No delete path, by design (§8.1)
CREATE TABLE oos_access_log (
  id              bigserial PRIMARY KEY,
  experiment_id   uuid REFERENCES experiments(id),
  strategy_id     uuid REFERENCES strategies(id),
  accessed_by     text NOT NULL,         -- human | validator | ai_agent
  purpose         text NOT NULL,
  accessed_at     timestamptz NOT NULL DEFAULT now()
);

-- Versioned cost assumptions. Results are never comparable across cost models (§9.5)
CREATE TABLE cost_models (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  venue           text NOT NULL REFERENCES venues(code),
  fill_model      text NOT NULL,         -- optimistic | realistic | pessimistic
  params          jsonb NOT NULL,        -- fees, impact coefficients, latency, adverse selection
  calibrated_from uuid,                  -- runs(id) whose own fills calibrated it
  code_hash       text NOT NULL,
  valid_from      timestamptz NOT NULL
);

-- Regime classifier versions. The regime time series itself is in Parquet (§10)
CREATE TABLE regime_models (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  method          text NOT NULL,         -- rules | hmm | clustering
  params          jsonb NOT NULL,
  code_hash       text NOT NULL,
  labels_uri      text NOT NULL,         -- Parquet: (ts, asset, regime), point-in-time
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- Promotion thresholds. Versioned, because lowering the bar must be auditable (§15)
CREATE TABLE gate_configs (
  id              uuid PRIMARY KEY,
  transition      text NOT NULL,         -- screening->validating, validating->candidate, ...
  thresholds      jsonb NOT NULL,        -- dsr_pvalue_max, pbo_max, min_oos_trades, ...
  rationale       text NOT NULL,         -- why it changed. Required.
  changed_by      uuid REFERENCES users(id),
  -- An approval may not cite a config younger than 24h (§20.3). With one
  -- operator this is what replaces separation of duties: relaxing a threshold is
  -- allowed, acting on it the same day is not.
  valid_from      timestamptz NOT NULL,
  valid_to        timestamptz
);

-- Every lifecycle transition. No transition exists outside this table (§15)
CREATE TABLE lifecycle_transitions (
  id              bigserial PRIMARY KEY,
  strategy_id     uuid NOT NULL REFERENCES strategies(id),
  from_state      text NOT NULL,
  to_state        text NOT NULL,
  gate_config_id  uuid REFERENCES gate_configs(id),
  evidence        jsonb NOT NULL,        -- snapshot of the criteria as evaluated
  actor           text NOT NULL,         -- system | user:<id>
  occurred_at     timestamptz NOT NULL DEFAULT now()
);

-- Human sign-off where the state machine requires one
CREATE TABLE approvals (
  id              uuid PRIMARY KEY,
  subject_kind    text NOT NULL,         -- strategy | capital_tier | gate_change
  subject_id      uuid NOT NULL,
  approved_by     uuid NOT NULL REFERENCES users(id),
  decision        text NOT NULL,         -- approve | reject
  notes           text,
  decided_at      timestamptz NOT NULL DEFAULT now()
);
```

### 5.2 Data and ingestion (5 tables)

Metadata only. The data itself is Parquet.

```sql
CREATE TABLE venues (
  code            text PRIMARY KEY,      -- binance, bybit, okx...
  name            text NOT NULL,
  kind            text NOT NULL,         -- cex | dex
  supports_testnet boolean NOT NULL,
  clock_offset_ms int,                   -- measured, refreshed periodically
  notes           text
);

CREATE TABLE sources (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  venue           text REFERENCES venues(code),
  kind            text NOT NULL,         -- ohlcv | trades | funding | oi | liquidations | book | news | macro | onchain
  tier            smallint NOT NULL,     -- 1 | 2 | 3 per §4.1
  sla_seconds     int NOT NULL,          -- max acceptable gap before it is an issue
  credibility     numeric,               -- point-in-time via valid_from (§11.2)
  valid_from      timestamptz NOT NULL,
  valid_to        timestamptz
);

-- Point-in-time venue universe and constraints (§4.4). Blocking requirement.
CREATE TABLE asset_universe (
  asset           text NOT NULL,
  venue           text NOT NULL REFERENCES venues(code),
  listed_at       timestamptz NOT NULL,
  delisted_at     timestamptz,
  tick_size       numeric NOT NULL,
  lot_size        numeric NOT NULL,
  min_notional    numeric NOT NULL,
  fee_tier        text NOT NULL,
  valid_from      timestamptz NOT NULL,
  valid_to        timestamptz,
  PRIMARY KEY (asset, venue, valid_from)
);

CREATE TABLE ingestion_runs (
  id              bigserial PRIMARY KEY,
  source_id       uuid NOT NULL REFERENCES sources(id),
  mode            text NOT NULL,         -- backfill | stream | recapture
  period_start    timestamptz NOT NULL,
  period_end      timestamptz,
  watermark       timestamptz,           -- resume point
  rows_written    bigint,
  output_uri      text,
  status          text NOT NULL,
  started_at      timestamptz NOT NULL,
  ended_at        timestamptz
);

-- Ingestion anomalies. Never silently corrected (§4.2, §4.6)
CREATE TABLE data_quality_issues (
  id              bigserial PRIMARY KEY,
  source_id       uuid NOT NULL REFERENCES sources(id),
  asset           text,
  kind            text NOT NULL,         -- gap | duplicate | out_of_range | cross_inconsistent | backfill_mismatch
  period_start    timestamptz NOT NULL,
  period_end      timestamptz NOT NULL,
  severity        text NOT NULL,         -- info | warn | blocking
  detail          jsonb NOT NULL,
  resolved_at     timestamptz
);
```

### 5.3 Execution (12 tables)

```sql
-- Versioned risk limit sets. Referenced, not embedded, so a change is traceable (§14.1)
CREATE TABLE risk_limit_configs (
  id                     uuid PRIMARY KEY,
  name                   text NOT NULL,
  max_open_positions     int NOT NULL,     -- enforced by the risk engine, not the strategy
  max_positions_per_cluster int NOT NULL,  -- a cluster is ONE bet (§13.1)
  max_open_orders        int NOT NULL,
  min_position_notional  numeric NOT NULL, -- floor: see §14.1
  max_position_notional  numeric NOT NULL,
  min_position_pct_equity numeric NOT NULL,
  max_position_pct_equity numeric NOT NULL,
  min_seconds_between_entries int NOT NULL, -- same asset; blocks churn
  max_gross_exposure     numeric NOT NULL,
  max_net_exposure       numeric NOT NULL,
  max_leverage           numeric NOT NULL,
  max_daily_loss         numeric NOT NULL,
  max_drawdown_from_peak numeric NOT NULL,
  max_orders_per_minute  int NOT NULL,
  max_consecutive_errors int NOT NULL,
  max_price_deviation_bps int NOT NULL,
  valid_from             timestamptz NOT NULL
);

-- Execution in any mode: backtest, simulator, testnet or live (§12)
CREATE TABLE runs (
  id                  uuid PRIMARY KEY,
  strategy_id         uuid NOT NULL REFERENCES strategies(id),
  portfolio_id        uuid REFERENCES portfolios(id),
  promoted_from       uuid REFERENCES experiments(id),
  mode                text NOT NULL,     -- backtest | simulator | testnet | live
  venue               text NOT NULL REFERENCES venues(code),
  params              jsonb NOT NULL,    -- IMMUTABLE: changing them requires a new run (§12.4)
  initial_capital     numeric NOT NULL,
  base_currency       text NOT NULL,
  risk_limit_config_id uuid NOT NULL REFERENCES risk_limit_configs(id),  -- mandatory in EVERY mode
  cost_model_id       uuid NOT NULL REFERENCES cost_models(id),
  capital_tier        text,              -- L0 | L1 | L2 | L3, live only
  code_version        text NOT NULL,
  image_digest        text NOT NULL,
  config_hash         text NOT NULL,
  status              text NOT NULL,     -- running | halted | stopped | completed
  halt_reason         text,
  started_at          timestamptz NOT NULL,
  ended_at            timestamptz
);

-- What the strategy WANTED to do. Append-only, written before any order is sent (§14.3)
CREATE TABLE intents (
  id              bigserial PRIMARY KEY,
  run_id          uuid NOT NULL REFERENCES runs(id),
  decided_at      timestamptz NOT NULL,
  asset           text NOT NULL,
  target_position numeric NOT NULL,
  reason          jsonb NOT NULL,        -- signal values that produced it
  vetoed_by       text,                  -- risk_engine rule that blocked it, if any
  attributed_to   uuid REFERENCES strategies(id)  -- survives venue-level netting (§13.4)
);

CREATE TABLE orders (
  id                 uuid PRIMARY KEY,
  run_id             uuid NOT NULL REFERENCES runs(id),
  intent_id          bigint REFERENCES intents(id),
  client_order_id    text NOT NULL,      -- deterministic -> idempotent retry (§14.3)
  venue_order_id     text,
  asset              text NOT NULL,
  side               text NOT NULL,
  order_type         text NOT NULL,
  qty                numeric NOT NULL,
  limit_price        numeric,
  intent_ts          timestamptz NOT NULL,
  sent_ts            timestamptz,
  ack_ts             timestamptz,
  status             text NOT NULL,
  reject_reason      text,
  UNIQUE (run_id, client_order_id)
);

CREATE TABLE fills (
  id                 uuid PRIMARY KEY,
  order_id           uuid NOT NULL REFERENCES orders(id),
  fill_ts            timestamptz NOT NULL,
  qty                numeric NOT NULL,
  price              numeric NOT NULL,
  expected_price     numeric NOT NULL,   -- per-trade slippage
  fee                numeric NOT NULL,
  liquidity          text NOT NULL       -- maker | taker
);

CREATE TABLE positions (
  run_id          uuid NOT NULL REFERENCES runs(id),
  asset           text NOT NULL,
  as_of           timestamptz NOT NULL,
  qty             numeric NOT NULL,
  avg_entry       numeric,
  unrealised_pnl  numeric,
  realised_pnl    numeric,
  funding_paid    numeric,
  PRIMARY KEY (run_id, asset, as_of)
);

CREATE TABLE balances (
  run_id          uuid NOT NULL REFERENCES runs(id),
  as_of           timestamptz NOT NULL,
  currency        text NOT NULL,
  equity          numeric NOT NULL,
  free            numeric NOT NULL,
  used_margin     numeric NOT NULL,
  PRIMARY KEY (run_id, as_of, currency)
);

-- Every time a limit fired. The risk engine writes here; nothing else does (§14.1)
CREATE TABLE risk_events (
  id              bigserial PRIMARY KEY,
  run_id          uuid NOT NULL REFERENCES runs(id),
  rule            text NOT NULL,
  observed        numeric NOT NULL,
  limit_value     numeric NOT NULL,
  action          text NOT NULL,         -- veto | halt | reduce_only | kill
  occurred_at     timestamptz NOT NULL DEFAULT now()
);

-- State vs the venue (§14.2). Distinct from `reconciliations`, which compares RETURNS.
CREATE TABLE position_reconciliations (
  id               bigserial PRIMARY KEY,
  run_id           uuid NOT NULL REFERENCES runs(id),
  checked_at       timestamptz NOT NULL,
  asset            text NOT NULL,
  qty_internal     numeric NOT NULL,
  qty_venue        numeric NOT NULL,
  balance_internal numeric,
  balance_venue    numeric,
  discrepancy      boolean NOT NULL,
  action_taken     text                  -- none | halt
);

-- Daily return divergence between execution modes (§12.5)
CREATE TABLE reconciliations (
  run_id             uuid NOT NULL REFERENCES runs(id),
  day                date NOT NULL,
  ref_kind           text NOT NULL,      -- backtest | simulator
  return_observed    numeric NOT NULL,
  return_reference   numeric NOT NULL,
  divergence_ci_low  numeric,
  divergence_ci_high numeric,
  attribution        jsonb NOT NULL,     -- {costs, timing, fills, data}
  verdict            text NOT NULL,      -- ok | warn | breach
  PRIMARY KEY (run_id, day, ref_kind)
);

-- Pre-registered sequential decay test (§14.6). Thresholds fixed before going live.
CREATE TABLE decay_tests (
  id              bigserial PRIMARY KEY,
  run_id          uuid NOT NULL REFERENCES runs(id),
  method          text NOT NULL,         -- cusum | sprt
  h0_sharpe       numeric NOT NULL,      -- from validation, fixed in advance
  statistic       numeric NOT NULL,
  threshold_warn  numeric NOT NULL,
  threshold_breach numeric NOT NULL,
  regime          text NOT NULL,         -- conditioned, so a dormant regime is not decay
  verdict         text NOT NULL,         -- ok | warn | breach | dormant
  computed_at     timestamptz NOT NULL
);

CREATE TABLE capital_ramp_events (
  id              bigserial PRIMARY KEY,
  run_id          uuid NOT NULL REFERENCES runs(id),
  from_tier       text,
  to_tier         text NOT NULL,         -- L0 | L1 | L2 | L3
  capital_after   numeric NOT NULL,
  reason          text NOT NULL,         -- advance | demote_limit_breach | demote_divergence
  approval_id     uuid REFERENCES approvals(id),
  occurred_at     timestamptz NOT NULL DEFAULT now()
);
```

### 5.4 Portfolio (5 tables)

```sql
CREATE TABLE portfolios (
  id                 uuid PRIMARY KEY,
  name               text NOT NULL,
  base_currency      text NOT NULL,
  allocation_method  text NOT NULL,      -- hrp | erc (§13.2)
  max_weight_strategy numeric NOT NULL,
  max_weight_cluster numeric NOT NULL,
  rebalance_days     int NOT NULL,
  promoted_count     int NOT NULL DEFAULT 0,  -- portfolio-level trial counter (§13.3)
  capacity_joint     numeric,            -- NOT the sum of parts (§13.5)
  created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE portfolio_members (
  portfolio_id    uuid NOT NULL REFERENCES portfolios(id),
  strategy_id     uuid NOT NULL REFERENCES strategies(id),
  cluster_id      text NOT NULL,         -- a cluster is ONE bet (§13.1)
  added_at        timestamptz NOT NULL,
  removed_at      timestamptz,
  PRIMARY KEY (portfolio_id, strategy_id, added_at)
);

-- Correlation of RETURNS, per regime. Crisis correlation is the one that matters.
CREATE TABLE strategy_correlations (
  strategy_a      uuid NOT NULL REFERENCES strategies(id),
  strategy_b      uuid NOT NULL REFERENCES strategies(id),
  regime          text NOT NULL DEFAULT 'all',
  window_days     int NOT NULL,
  correlation     numeric NOT NULL,
  n_obs           int NOT NULL,
  computed_at     timestamptz NOT NULL,
  PRIMARY KEY (strategy_a, strategy_b, regime, window_days)
);

CREATE TABLE portfolio_allocations (
  portfolio_id    uuid NOT NULL REFERENCES portfolios(id),
  effective_at    timestamptz NOT NULL,
  strategy_id     uuid NOT NULL REFERENCES strategies(id),
  weight          numeric NOT NULL,
  cluster_weight  numeric NOT NULL,
  turnover_cost   numeric,               -- charged to the rebalance decision (§13.2)
  PRIMARY KEY (portfolio_id, effective_at, strategy_id)
);

CREATE TABLE portfolio_results (
  portfolio_id      uuid NOT NULL REFERENCES portfolios(id),
  day               date NOT NULL,
  equity            numeric NOT NULL,
  return_net        numeric NOT NULL,
  drawdown          numeric NOT NULL,
  deflated_sharpe   numeric,             -- deflated by promoted_count (§13.3)
  PRIMARY KEY (portfolio_id, day)
);
```

### 5.5 News, events and impact (15 tables)

```sql
CREATE TABLE entities (
  id              uuid PRIMARY KEY,
  kind            text NOT NULL,         -- asset | protocol | company | person | regulator
  canonical_name  text NOT NULL,
  aliases         jsonb NOT NULL,
  linked_asset    text                   -- when the entity maps to a tradeable asset
);

CREATE TABLE articles (
  id              uuid PRIMARY KEY,
  source_id       uuid NOT NULL REFERENCES sources(id),
  url             text,
  dedup_hash      text NOT NULL,         -- exact-duplicate key
  title           text NOT NULL,
  body            text,
  published_at    timestamptz NOT NULL,  -- event_time
  ingested_at     timestamptz NOT NULL,
  available_at    timestamptz NOT NULL,  -- publication + measured feed delay (§4.3)
  embedding       vector(768),
  event_id        uuid REFERENCES events(id),
  UNIQUE (dedup_hash)
);

CREATE TABLE events (
  id              uuid PRIMARY KEY,
  event_type      text NOT NULL,         -- listing | hack | regulation | macro_release | unlock ...
  -- Which of the three shapes in §11.7, because each needs different machinery.
  shape           text NOT NULL DEFAULT 'shock',   -- shock | schedule | process
  first_seen_at   timestamptz NOT NULL,
  latest_revision int NOT NULL DEFAULT 1,
  resolved_at     timestamptz,           -- when the date passed, whatever happened
  cluster_method  text NOT NULL,
  code_hash       text NOT NULL,
  CHECK (shape IN ('shock', 'schedule', 'process'))
);

-- What the expected date was believed to be, and when that belief existed.
--
-- Unlocks are postponed and deadlines extended, so computing "days until the
-- event" from the date it eventually happened on assumes the market knew a date
-- it did not. That is look-ahead bias wearing a calendar, and it is invisible
-- afterwards because the labels look entirely reasonable — they are simply built
-- from a future nobody had (§11.6).
CREATE TABLE schedule_revisions (
  event_id        uuid NOT NULL REFERENCES events(id),
  revision        int NOT NULL,
  available_at    timestamptz NOT NULL,  -- when this date became public
  scheduled_for   timestamptz NOT NULL,  -- the date as believed at that moment
  confidence      text,                  -- fixed | estimated | rumoured
  source          text,
  PRIMARY KEY (event_id, revision)
);

-- Macro series catalogue (§4.7). `has_vintages` is operational, not decorative:
-- a revised series used as if it were real-time answers a different question, so
-- results depending on one are caveated rather than quietly reported.
CREATE TABLE macro_series (
  code            text PRIMARY KEY,      -- FEDFUNDS, DGS10, DXY, GOLD, WTI, CPIAUCSL
  name            text NOT NULL,
  kind            text NOT NULL,         -- rate | yield | price | index | activity
  source          text NOT NULL,         -- fred | alfred | stooq | manual
  has_vintages    boolean NOT NULL,
  is_revised      boolean NOT NULL,
  session_close   text,                  -- e.g. '17:00 America/New_York'; null if 24/7
  publish_lag_s   int NOT NULL DEFAULT 0
);

-- Scheduled releases: the one event family where surprise is a number rather
-- than an inference (§11.1). Consensus is what makes it so.
CREATE TABLE macro_releases (
  id              uuid PRIMARY KEY,
  series          text NOT NULL REFERENCES macro_series(code),
  scheduled_for   timestamptz NOT NULL,
  -- A consensus recorded after the release is not a consensus, it is a memory of
  -- one. Enforced here rather than in the form, as with §20.5's judgements.
  consensus       numeric,
  consensus_at    timestamptz,
  actual          numeric,
  actual_at       timestamptz,           -- publication instant, not the date
  available_at    timestamptz,           -- actual_at plus the measured feed delay
  surprise_sigma  numeric,               -- (actual - consensus) / dispersion
  vintage_of      uuid REFERENCES macro_releases(id),   -- revision chain
  CHECK (consensus IS NULL OR consensus_at IS NULL OR consensus_at < scheduled_for)
);

-- Human annotations on events (§20.5). Revisions, never edits: a rating made
-- today must be invisible to a backtest of last year, or hindsight enters the
-- historical labels through the interface.
CREATE TABLE event_annotations (
  id              uuid PRIMARY KEY,
  event_id        uuid NOT NULL REFERENCES events(id),
  kind            text NOT NULL,         -- correction | judgement
  -- A correction repairs a parse failure, so it inherits the event's own
  -- availability: the value was always there. A judgement creates information
  -- at the moment it is made and can never be backdated.
  available_at    timestamptz NOT NULL,
  payload         jsonb NOT NULL,        -- corrected fields, or the rating
  -- Signed expected move in STANDARD DEVIATIONS of the asset's pre-event
  -- volatility, not in percent and not on an arbitrary scale.
  --
  -- The unit is the whole point. §11.3 normalises every measured reaction to
  -- sigma, so a rating in sigma is directly comparable to what actually
  -- happened and can be scored. A rating of "8 out of 10 bullish" cannot be
  -- scored against anything, which means the annotator's calibration is
  -- unknowable — and an unmeasurable input is the one nobody ever corrects.
  --
  -- It is also the only unit comparable across assets: 2% on BTC and 2% on a
  -- memecoin are different events, 2 sigma is the same event.
  rating          numeric,               -- e.g. +2.0 means "I expect +2 sigma"
  confidence      numeric,               -- 0-1, how sure of the rating
  author          uuid NOT NULL REFERENCES users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (kind IN ('correction', 'judgement')),
  -- A judgement dated before it was written is hindsight with a form around it.
  CHECK (kind <> 'judgement' OR available_at >= created_at - interval '1 minute')
);

-- Transitions of a process event (§11.7). Each is measured separately, because
-- the reaction to `proposed` and to `decided` are different quantities and
-- pooling them estimates neither.
CREATE TABLE process_transitions (
  event_id        uuid NOT NULL REFERENCES events(id),
  sequence        int NOT NULL,
  state           text NOT NULL,         -- proposed|under_review|decided|implemented|withdrawn
  available_at    timestamptz NOT NULL,
  supersedes      int,                   -- sequence invalidated by this one
  payload         jsonb NOT NULL,
  PRIMARY KEY (event_id, sequence)
);

-- The anticipation path: response as a function of time UNTIL the date (§11.6).
-- Kept apart from event_impulse_response, which is time SINCE — the two carry
-- opposite signs often enough that merging them would cancel the effect.
CREATE TABLE event_anticipation (
  id              uuid PRIMARY KEY,
  event_class     text NOT NULL,
  state_bucket    text NOT NULL,
  days_until      int NOT NULL,          -- positive: before the date
  coef            numeric NOT NULL,
  ci_low          numeric NOT NULL,
  ci_high         numeric NOT NULL,
  n_events        int NOT NULL,
  fitted_through  timestamptz NOT NULL,
  UNIQUE (event_class, state_bucket, days_until, fitted_through)
);

-- Events accrete revisions; a backtest at t sees only revisions with available_at <= t (§11.2)
CREATE TABLE event_revisions (
  event_id        uuid NOT NULL REFERENCES events(id),
  revision        int NOT NULL,
  available_at    timestamptz NOT NULL,
  payload         jsonb NOT NULL,        -- entities, magnitude, surprise, credibility, horizon
  PRIMARY KEY (event_id, revision)
);

CREATE TABLE event_assets (
  event_id        uuid NOT NULL REFERENCES events(id),
  revision        int NOT NULL,
  asset           text NOT NULL,
  expected_direction smallint NOT NULL,  -- -1 | 0 | +1
  -- In sigma of pre-event volatility, matching `event_annotations.rating` and
  -- the z of `event_reactions`, so model, human and outcome are all on one
  -- scale and the residual of §11.10 is a subtraction rather than a conversion.
  expected_magnitude numeric,
  confidence      numeric,
  PRIMARY KEY (event_id, revision, asset)
);

-- Measured reaction per event, asset and horizon. The raw material of §11.5.
-- Horizons include NEGATIVE values: pre-event flatness is a mandatory diagnostic.
CREATE TABLE event_reactions (
  event_id         uuid NOT NULL REFERENCES events(id),
  asset            text NOT NULL,
  horizon_minutes  int NOT NULL,         -- -60 | -15 | 5 | 15 | 60 | 240 | 1440 | 4320 | 10080
  car              numeric NOT NULL,     -- cumulative abnormal return (§11.3)
  z                numeric NOT NULL,     -- CAR / (sigma_pre * sqrt(h)), the poolable quantity
  benchmark_kind   text NOT NULL,        -- cross_sectional | time_series
  beta_pre         numeric,              -- estimated on [t-30d, t-1d], embargoed
  sigma_pre        numeric NOT NULL,
  oi_change        numeric,
  funding_change   numeric,
  trade_count_z    numeric,              -- participation (§11.4)
  avg_trade_size_z numeric,
  -- Why the signal ended (§11.8). Decay is a rate; the other two are dates, and
  -- a model carrying only a rate keeps a position open on an item cancelled
  -- yesterday because its clock says half-life remains.
  ended_by         text,                 -- decay | superseded | resolved | open
  ended_at         timestamptz,
  PRIMARY KEY (event_id, asset, horizon_minutes)
);

-- Point-in-time market state. Built in-house so it cannot be revised (§11.4).
CREATE TABLE market_state (
  as_of            timestamptz NOT NULL,
  scope            text NOT NULL,        -- 'market' or an asset symbol
  funding_z        numeric,
  funding_dispersion numeric,
  basis_slope      numeric,
  oi_to_mcap       numeric,
  liq_asymmetry    numeric,              -- long vs short liquidated notional
  realised_vol_z   numeric,
  trade_count_z    numeric,
  avg_trade_size_z numeric,
  depth_25bps      numeric,
  effective_spread numeric,
  bucket           text NOT NULL,        -- 3-5 discrete states; continuous forbids conditioning
  model_id         uuid NOT NULL REFERENCES state_models(id),
  PRIMARY KEY (as_of, scope, model_id)
);

CREATE TABLE state_models (
  id              uuid PRIMARY KEY,
  name            text NOT NULL,
  params          jsonb NOT NULL,        -- component weights, bucket boundaries
  n_buckets       smallint NOT NULL,
  code_hash       text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CHECK (n_buckets BETWEEN 3 AND 5)
);

-- The estimated impulse response. One row per (class, state, horizon).
-- Fitted walk-forward: `fitted_through` is the only data it ever saw (§11.5 step 5).
CREATE TABLE event_impulse_response (
  id               uuid PRIMARY KEY,
  event_class      text NOT NULL,
  state_bucket     text NOT NULL,
  horizon_minutes  int NOT NULL,
  coef             numeric NOT NULL,     -- alpha(h) from the local projection
  surprise_beta    numeric,              -- beta(h)
  ci_low           numeric NOT NULL,
  ci_high          numeric NOT NULL,
  n_events         int NOT NULL,         -- shown wherever coef is shown. Non-negotiable.
  pooled           boolean NOT NULL,     -- true when empirical-Bayes shrunk (§11.8)
  shrinkage        numeric,              -- 0 = raw cell estimate, 1 = fully the prior
  fitted_through   timestamptz NOT NULL,
  model_id         uuid NOT NULL REFERENCES irf_models(id),
  UNIQUE (model_id, event_class, state_bucket, horizon_minutes)
);

-- Decay fit and the tradeability verdict, per (class, state). §11.5 steps 3-4.
CREATE TABLE irf_models (
  id               uuid PRIMARY KEY,
  event_class      text NOT NULL,
  state_bucket     text NOT NULL,
  amp_fast         numeric,              -- A
  tau_fast_minutes numeric,              -- mechanical reaction, largely uncapturable
  amp_slow         numeric,              -- B
  tau_slow_minutes numeric,              -- the only component that survives cost drag
  tau_slow_ci_low  numeric,
  tau_slow_ci_high numeric,
  half_life_minutes numeric,
  cost_filter_pass boolean NOT NULL,     -- §11.5 step 4
  implied_hold_days numeric,             -- must land inside the §0.2 band
  pre_event_flat_p numeric NOT NULL,     -- §11.10. Low p means the timestamp is lying.
  placebo_percentile numeric NOT NULL,
  n_events         int NOT NULL,
  fitted_through   timestamptz NOT NULL,
  code_hash        text NOT NULL,
  verdict          text NOT NULL         -- usable | inconclusive | rejected
);
```

### 5.6 Platform (3 tables)

```sql
-- One row. Exists so approvals and the audit log have a real actor to reference,
-- not to manage access (§20.3).
CREATE TABLE users (
  id              uuid PRIMARY KEY,
  email           text NOT NULL UNIQUE,
  role            text NOT NULL DEFAULT 'admin',
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id              bigserial PRIMARY KEY,
  actor           text NOT NULL,
  action          text NOT NULL,
  subject_kind    text NOT NULL,
  subject_id      text NOT NULL,
  payload         jsonb,
  occurred_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  severity        text NOT NULL,         -- info | warn | critical
  source          text NOT NULL,         -- risk_engine | collectors | validator | executor
  subject_id      text,
  message         text NOT NULL,
  acknowledged_by uuid REFERENCES users(id),
  raised_at       timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz
);
```

### 5.7 What does not go in Postgres

The table count stays around 48 because the high-volume data lives elsewhere. This boundary is a design rule, not an optimisation.

| Data | Where | Why |
|---|---|---|
| Market time series (OHLCV, trades, funding, OI, liquidations, book features) | Parquet | Columnar scans over years, no updates, no concurrency |
| Feature matrices | Parquet, identified by `feature_matrices` | Wide, regenerable, shared read-only by workers (§9.6) |
| **Tier A experiment results** | **Parquet, one file per study** | See below |
| Regime label series | Parquet, identified by `regime_models` | Time series |
| Equity curves, trade blotters, models | Object storage, URI in the row | Blobs |
| Hot window, last 7–30 days | TimescaleDB, from phase 5 | Live ingestion, continuous aggregates |

**The tier A rule.** A screening campaign of 10⁶ trials produces roughly 5×10⁶ result rows, and tens of millions per year. Postgres would survive it and the registry would become unusable to query.

So: **tier 1 results are written to Parquet, one partition per study, and only tier ≥ 2 enters Postgres** — enforced by the `CHECK (tier >= 2)` on `experiments`. Tier A is disposable mass screening; tier B is already a candidate and earns referential integrity.

Two things must survive the boundary, because the statistics depend on them:

- **`studies.executed_trials` counts tier A trials**, even though their rows are not in Postgres. The counter is what feeds the Deflated Sharpe (§8.3); losing tier A from the count would silently inflate every verdict.
- **Tier A `config_hash` values are retained** in the Parquet partition, so deduplication (§9.6) still works across campaigns.

This keeps the registry in the hundreds of thousands of rows rather than tens of millions, which is the difference between querying it instantly and having to think about it first.

### 5.8 Details that matter

- **`config_hash`** makes experiments idempotent: re-running the same configuration reuses instead of recomputing. It is what allows a large funnel without burning compute twice.
- **`studies.executed_trials`** is monotonic and never reset. If it is reset, the multiple-testing control is lying.
- **`studies.min_detectable_sharpe` and the hold bounds are set before the search**, not after. They are constraints on the campaign, not descriptions of it (§8.8, §0.2).
- **`hypotheses.prior_belief`** is recorded before the result. Prevents HARKing.
- **`runs.params` is immutable.** Tuning a signal parameter in the simulator does not edit the run: it creates a new strategy that re-enters the funnel (§12.4). This is the technical barrier against re-optimising on the live period.
- **`orders.intent_ts` vs `sent_ts`** separates decision from execution, which is what makes divergence attributable to timing or to fills (§12.5).
- **`intents` is written before the order is sent**, and records vetoed intents too. It is what makes per-strategy attribution survive venue-level netting (§13.4) and what lets you reconstruct why a position exists.
- **`reconciliations` and `position_reconciliations` are different things.** The first compares *returns* between execution modes (is the backtest honest?); the second compares *state* against the venue (do I hold what I think I hold?).
- **`gate_configs.rationale` is NOT NULL.** Lowering a threshold requires writing down why, and the reason is attached to every verdict issued under it.
- **`oos_access_log` has no delete path.** Its only purpose is to make OOS erosion visible, and a log you can prune is a log that will be pruned exactly when it matters.
- Heavy blobs go to object storage; Postgres stores URIs.

---

## 6. Feature engine

A feature is **(definition, parameters)**, not a column. The definition is registered once; parameterisation is expanded at experiment time.

```python
@feature(
    family="momentum",
    lookback=lambda p: p["period"] + 1,
    params={"period": IntRange(5, 60), "source": Choice("close", "vwap")},
)
def rsi(df: pl.LazyFrame, period: int, source: str) -> pl.Expr:
    ...
```

Contract requirements:

1. **Declare `lookback`.** It is what the system uses to compute warmup and, more importantly, the purge size in cross-validation. A feature that lies about its lookback silently breaks purging.
2. **Strict causality.** No feature may touch rows with `available_at > t`. Verified automatically (§16.3), not by human review.
3. **Output as a lazy Polars expression**, so the planner can fuse the computation of thousands of variants into a single pass.
4. **Explicit normalisation**: level features (price, OI) require a declared stationarity transform — log difference, rolling z-score, or **fractional differentiation** when memory should be preserved without sacrificing stationarity.

Families: momentum, trend, volatility, volume, market structure, VWAP/volume profile, order flow, OI, funding, liquidations, basis, options, macro, on-chain, news, cross-asset.

**Computation**: features are materialised once per (dataset_version, feature_set) to Parquet and cached by hash. Thousands of experiments share the same matrix. Without this, 90% of compute goes into recomputing RSI.

### 6.1 Feature importance and selection

The registry is meant to answer "which features have the most predictive power" (Appendix A). Doing that naively produces another overfitting channel, so the method is fixed here.

- **MDA, not MDI.** Mean Decrease Accuracy under purged CV: permute one feature, measure the drop. Mean Decrease Impurity is biased toward high-cardinality features and is computed in-sample.
- **Cluster before ranking.** With hundreds of parameterised variants, RSI 13 and RSI 14 are the same feature. Correlated features split each other's importance and all of them look weak. Features are clustered by correlation distance and importance is computed **per cluster**, not per column.
- **Selection happens inside the CV loop, never before it.** Choosing features by importance measured on the full dataset and then validating on the same data is leakage with extra steps. If selection is part of the strategy, it is refitted in every fold.
- **Importance is descriptive, not a gate.** It guides hypothesis generation; it never promotes anything. A feature with high MDA and no strategy that survives §8 has produced exactly zero evidence.

Aggregated clustered-MDA across experiments is what answers the registry question, and it is reported with the number of experiments behind it — a feature that looks strong across 4 experiments is not a finding.

### 6.2 Price levels as features

Support and resistance is the most overfitted idea in retail technical analysis, and not because it is false — there are real mechanisms — but because "a level" is enormously flexible. With enough candidate levels, every move has one nearby, and any move can be explained after the fact.

The discipline that makes it researchable: **a level comes from a deterministic, parameterised rule that enters the funnel like any other feature.** Nothing drawn by hand, nothing chosen by eye.

Five families, ordered by how defensible the mechanism is:

| Family | Parameters | Mechanism |
|---|---|---|
| **Liquidation clusters** | leverage buckets, OI window, bin width | **Orders that must execute.** Estimated from OI and the leverage distribution, validated against observed liquidations |
| Volume profile | lookback, bin width, HVN/LVN threshold | Where size actually traded; inventory sits there |
| Anchored VWAP | anchor (session, week, or an event from §11) | A reference price participants actually use |
| Prior extremes | fractal order, confirmation bars | Prior rejection; the classic, parameterised |
| Round numbers | tick granularity, significance | Order placement clusters on round figures |

Only the first corresponds to orders that exist and **will** execute regardless of what anyone believes. It is also computable from data already being captured, which is why it leads the list.

**What reaches the model** is not "there is a level", which is boolean and nearly information-free, but: distance to the nearest level normalised by ATR, level density in the neighbourhood, whether price is approaching from above or below, how many times the level has been tested, and the age of the level.

#### The leak that this family invites

A swing high is not known when it forms. It is known `N` bars later, once the confirmation window has passed. Marking a swing high at bar `t` using bars `t+1 … t+N`, and then using "distance to swing high" as a feature *at bar t*, is look-ahead bias — and it is the single most common way a level-based backtest produces beautiful, fictional results.

The machinery for this already exists and must be used without exception:

- Every level feature declares `lookback_bars` **including its confirmation window** (§6). A fractal of order 5 confirmed 5 bars later declares a lookback that accounts for both.
- Truncation invariance (§16.3) catches a violation generically: the level set computed on data truncated at `t` must equal the level set computed on full history and read at `t`.
- A canary in §16.5 injects exactly this bug, so the detector is known to work.

Volume profile and anchored VWAP have the same hazard in milder form: a profile computed over a window centred on `t` sees the future half.

#### Levels and labels

An option worth testing, not a default: triple-barrier barriers (§7) placed **at levels** rather than at volatility multiples. It makes the label correspond to the trade actually intended, but it changes the label definition, so it is a separate `label_definition` and a separate arm of the funnel — not a tweak to an existing one.

### 6.3 Cross-sectional asset selection

The requirement — configure a handful of assets and let the system decide which one currently fits the strategy best — has two readings that sound identical and are not.

**Valid**: at each decision point, score every configured asset and trade the best. That is cross-sectional selection, it is standard, and **it is part of the strategy**. The scoring rule lives in `strategies.params`, is versioned, and the whole thing is backtested jointly over all the assets — never one at a time.

**Invalid**: run the strategy on five assets, look at the results, keep the one that worked. That is five trials. `studies.executed_trials` increments by five, the Deflated Sharpe bar rises accordingly (§8.3), and the survivor must clear the higher bar. It feels like configuration and it is research, which is precisely why it slips through.

Rule, enforced by the same mechanism as §12.4: **the asset set and the selection rule are part of the configuration hash.** Changing either creates a new experiment and increments the counter. There is no path by which asset choice escapes the trial count.

#### Why five is too few to validate on

Per §0.1, twenty crypto assets behave like three or four independent ones, because cross-sectional correlation against BTC runs around 0.7–0.8. **Five behave like one and a half to two.**

That is very little diversification, and worse, very little statistical power — it collides directly with §8.8, which already identifies sample size as the binding constraint. A selection rule estimated on five assets is estimated on almost nothing.

So:

> **Validate on a wide universe. Deploy on a narrow one.**

The selection rule is estimated and tested across 20–50 assets, where the statistics can speak. Deployment is then restricted to however many the operator wants to run, for entirely legitimate operational reasons: capital, liquidity, fee tiers, margin fragmentation, attention. `runs.universe` (§5.3) holds the deployed set; `experiments.universe` holds the validated one, and they are deliberately different columns.

#### How the deployed set may be chosen

Not by past performance. Choosing the five that did best historically reintroduces survivorship bias at the universe level — exactly what §4.4 removes at the asset level, put back one layer up.

Legitimate criteria are operational and, where they reference market data, **point-in-time**: liquidity as of the decision date, not as of today; fee tier actually held; minimum notional actually in force. Selection by anything that required knowing the outcome is a trial, and is counted as one.

---

## 7. Labeling — the missing layer

The original brief goes from features to "rules" without defining what is being predicted. This is a layer of its own, and it is versioned.

**Triple barrier**: for each candidate instant, three barriers are set — take profit, stop loss and maximum horizon — **scaled by recent volatility**, not fixed. Maximum holding is bounded to the 1–7 day band fixed in §0.2, which is what makes the purge size in §8.2 tractable. The label is whichever is touched first. This is the right way to label because it bakes real risk management into the definition of the target.

**Meta-labeling**: a first model decides direction; a second decides *whether to trade* that signal at all. It separates "which way" from "with what confidence", and usually improves precision without touching the primary model.

**Uniqueness and sample weights**: labels with overlapping horizons are not independent observations. A label opened at t and determined at t+3d spans three days; so does the one opened an hour later, and they mostly observe the *same* three days. A label's uniqueness is the average, over the bars it spans, of one divided by how many labels span that bar; summing those gives the **effective** observation count.

The magnitude is not a detail. Measured on 20,000 synthetic bars:

| Max holding | Labels | Effective n | Inflation |
|---|---|---|---|
| 10 bars | 19,994 | 2,519 | **7.9×** |
| 60 bars | 19,943 | 492 | **40.5×** |
| 240 bars | 19,794 | 162 | **122×** |
| 720 bars | 19,280 | 89 | **218×** |

And it changes verdicts rather than adjusting them. The same return series at a 240-bar horizon gives a Deflated Sharpe of 1.000 and a MinTRL of 266 observations on the naive count — established beyond doubt — and a DSR of 0.024 with an infinite MinTRL on the effective count. Every significance figure in §8.3 and every power calculation in §8.8 takes the effective count, never the bar count.

For resampling, sequential bootstrap: an ordinary bootstrap over overlapping labels redraws the same few periods and understates variance, producing confidence intervals narrower than the data earns.

**Cost-net definition of success**: the target is always defined after fees and estimated slippage. A label based on gross return produces strategies that win on paper and lose in execution.

---

## 8. Validation and anti-overfitting

The heart of the system. No result leaves the lab without passing through here.

### 8.1 Temporal partitioning

- Train / validation / test strictly ordered in time
- **Frozen OOS**: defined once, up front, and touched as little as possible. Every OOS query is recorded in `oos_access_log`. An OOS you look at a hundred times stops being OOS.
- **Anchored and rolling walk-forward**, with periodic retraining. Both are worth running: anchored asks whether more history helps, rolling asks whether old history still applies, and disagreement between them is itself a finding about stationarity.

Walk-forward is what resolves the tension between sample size and regime relevance, and the instinct to resolve it by using less history is the wrong trade. Measured on this data, cutting seven years to one raises the smallest resolvable effect by **2.7×** — 0.060 to 0.161 — without addressing non-stationarity at all; it merely picks one regime instead of averaging several. Walk-forward uses the whole history while never assuming one relationship spans it.

Two outputs, and the second is the one that gets overlooked:

- **The concatenated out-of-sample track record.** A genuine result rather than a split someone chose.
- **The sequence of selected parameters.** If every window picks something different, there is no stable relationship to trade. A strategy can post a respectable walk-forward return while its parameters wander, and in that case the return came from re-selecting rather than from anything the parameters describe. `parameter_churn` reports it.

The trial count accumulates across windows: each spends its own, and the total deflates the final Sharpe. On the seven-year BTCUSDT run that was 3,024 trials rather than the 504 of a single window, and counting only the last would have lowered the bar by about 20%.

### 8.2 CPCV with purging and embargo

Combinatorial Purged Cross-Validation (López de Prado):

- **Purging**: training samples whose labels overlap the test period in time are removed. Without this, a label with a 24 h horizon leaks information across the split boundary.
- **Embargo**: a window immediately after the test set is additionally dropped, to cut serial autocorrelation.
- Purge size is derived automatically from `label_definitions.horizon_bars` and the feature set's maximum `lookback`. **It is not a hand-tuned parameter.**

CPCV produces multiple backtest paths instead of one, yielding a **distribution** of Sharpe rather than a single number. That distribution is the input to PBO.

### 8.3 Multiple-testing correction

With N evaluated trials:

- **Deflated Sharpe Ratio** — adjusts the observed Sharpe for trial count, skewness, kurtosis and sample length. Returns the probability that the true Sharpe is > 0.
- **PBO** (Probability of Backtest Overfitting) via CSCV — the probability that the configuration chosen in-sample falls below the median out-of-sample. PBO > 0.5 means the selection process is worse than choosing at random. **Computed on gross returns**, for the reason below.
- **Hansen SPA / White's Reality Check** — tests the best model in the set against a benchmark while controlling for the full search.
- **Romano-Wolf stepdown** — when the goal is to identify *several* surviving strategies under family-wise error control.
- **Minimum Track Record Length** — how many observations would be needed for that Sharpe to be significant. If it exceeds the available history, the result is inconclusive no matter how high it looks.

`trials_considered` comes from `studies.executed_trials`, which includes **those discarded in tier A**. Counting only the finalists is the most common way to fool yourself.

#### PBO must be fed gross returns, and why that is not obvious

Measured on pure noise, PBO computed on **net** returns came out at 0.000 — apparently flawless selection on data containing nothing to select. The estimator was not broken.

With heterogeneous turnover, net Sharpe correlates about **−0.95** with turnover. The search therefore selects the configuration that trades least, and trading least is genuinely persistent out of sample. PBO reports that persistence accurately; it simply is not alpha. Removing costs from the same data raises PBO to 0.667, which is the answer the question was actually asking for.

The consequence is a gap in the method worth stating plainly: **a low PBO is not evidence of skill when the candidates differ in turnover.** It can mean nothing more than that the cheapest configuration stays cheapest.

Two controls close it, and both are already required:

- PBO is computed on gross returns, and `turnover_correlation` is reported alongside it so a turnover-driven result is visible rather than silent
- §8.4's random-entry baseline is **matched on trade count and holding distribution**, which holds turnover fixed and isolates timing. That matching was always the point of it; this is the concrete failure it catches.

### 8.4 Null baselines

No result is interpreted in absolute terms. Every experiment is compared against:

1. **Buy & hold** on the asset, same period, same costs
2. **Matched random entry** — same trade count, same duration distribution, same size. Repeated 1,000+ times → empirical null distribution
3. **Permuted signal** — signals shuffled against the price, or applied to block-permuted price series
4. **Cost sensitivity sweep** — if the strategy dies when slippage doubles, it is not a strategy

A Sharpe of 1.8 that lands in the 60th percentile of the random-entry distribution is not a signal, it is a coincidence with good presentation.

### 8.5 Parameter stability

The **shape of the surface** is the evidence, not its maximum. RSI 14 excellent with 13 and 15 disastrous is a spike, and a spike is what noise produces; RSI 12–18 all working is a plateau, and a plateau is what a mechanism produces.

There is a subtlety that changes how this is measured. **The maximum of a noisy surface is biased upward by selection** — even on a perfectly flat true surface the sampled peak is an outlier, so any metric anchored on the peak is partly measuring that bias.

Two consequences, both implemented rather than described:

- **The robust score is the neighbourhood median, and it is what gets promoted.** It needs no bias correction because it is not the quantity that was maximised. A spike scores poorly because its neighbours drag the median down; a plateau scores well because its neighbours agree with it.
- **The configuration chosen is the centre of the best neighbourhood, not the best point.** Taking the argmax and then describing its surroundings fails on near-ties: the argmax lands on an arbitrary member of a plateau, and a corner member has most of its neighbourhood outside, so a genuine plateau is reported as a spike. Maximising the smoothed surface lands in the middle, which is where a configuration should be taken from anyway.

`is_spike` is then `robust_score < retention × peak_score` — does the good result survive smoothing? It is stated against the peak rather than against the chosen neighbourhood because an isolated spike is erased entirely by the median smoother, leaving a uniformly low surface whose "best neighbourhood" is arbitrary.

`plateau_width` answers §8.5's own question directly: the contiguous run of parameter values holding half the best score on their slice. A width of one is a spike.

### 8.6 Additional robustness

- **Monte Carlo over trade ordering → distribution of maximum drawdown.** The observed drawdown is not a property of the strategy; it is what happened to occur in the single ordering history produced. Reordering the same trades gives a different figure, usually worse: measured on one series, the observed −17.5% sat at the 71st percentile, with a median reordering of −19.9% and a 5th percentile of −30%. §14.6 depends on this distribution existing — without it the rule protecting a strategy from being retired during an ordinary drawdown has no reference to point at.
- **Stationary bootstrap** (Politis-Romano) over the return series, respecting serial dependence. Not optional: on a series with autocorrelation 0.3, the iid bootstrap returned an interval 28% narrower that **excluded zero** where the blocked one included it. A narrower interval than the data earns is a different conclusion, not a rounding difference.
- Input perturbation (price jitter within the spread). A result that moves materially when each price shifts by a fraction of the bid-ask spread is fitted to the exact prints rather than to the market.
- Results broken down **by regime** (§10), with a minimum trade count per regime
- Explicit complexity penalty: number of parameters, features and rules

### 8.7 Hard minimums

An experiment with fewer than ~100 trades in OOS is not evaluated: it is marked `inconclusive`. No statistic can rescue a sample that small, and allowing it is the entry point for all the noise in the system.

### 8.8 Statistical power: decide what you can detect before searching

The uncomfortable arithmetic, stated up front because it bounds the whole project.

Usable perpetual-futures history starts around 2019, so roughly six to eight years. After purging, embargo and holding out a frozen OOS, the number of genuinely independent observations is far smaller than the row count suggests — and for a strategy holding positions for hours, the effective sample is the number of **non-overlapping trades**, not the number of bars.

Consequence: there is a floor below which an edge is undetectable with this dataset, no matter how much compute is spent. A strategy with a true Sharpe of 0.3 cannot be distinguished from zero with a few hundred trades; searching for it only guarantees that what surfaces is noise that happened to look like 0.3.

Therefore:

- **Fix the minimum detectable effect before the search begins**, and make it a precondition rather than a diagnosis. A study estimates the effective observations its design will produce, compares them against what its declared target needs, and **refuses to run** if they fall short. Diagnosing it afterwards is not the same thing: the trials have been spent by then, and the counter is monotonic, so a study that could never have concluded still raises the bar for every study after it.
- **The sizing pilot reads structure, never performance.** It measures how often the design trades and computes no return, Sharpe or profit. A pilot whose performance is looked at is a trial and would have to be counted; one that cannot be selected on costs nothing against §8.3.
- **Do not search below the floor.** A search whose target is under the detection floor produces false positives by construction.

The arithmetic is unforgiving and worth stating in numbers. §0.3 expects an out-of-sample Sharpe of 0.7–1.3 from a genuine edge. Confirming a Sharpe of 1.0:

| Trials spent | Effective observations needed |
|---|---|
| 9 | 21,947 |
| 54 | 34,179 |
| 500 | 48,324 |

Measured against designs actually run: a pooled threshold study over 150 assets at a low threshold reached 59,000–67,000 and was adequately powered; the same study at a high threshold reached 3,800 and was not; a cross-sectional basket reached 500–2,500 and could only have confirmed a Sharpe above 3.5, which is not a hypothesis test but a formality.

**This is the design constraint the whole project turns on**, and it pulls against §0.1. Power wants many independent bets; costs want few trades. The only lever that delivers independence without turnover is **pooling across assets** — which is why a basket, collapsing 150 assets into one return series, is the worst of both.
- **MinTRL is the operational form of this** and is already in `validation_reports`: if the required track record exceeds available history, the verdict is `inconclusive`, never `pass`.
- Higher-frequency strategies have more power per unit of calendar time, which is a reason to prefer them at this stage — not because they are better, but because they are **falsifiable** with the data on hand.

This section exists to make one failure mode impossible: spending months discovering that the question was unanswerable with the available sample.

---

## 9. Experiment engine and compute budget

### 9.1 Multi-stage funnel

A million configurations under realistic event-driven simulation is not viable. Structure:

| Stage | Engine | Costs | Typical volume | Unit cost |
|---|---|---|---|---|
| **Tier A — screening** | Vectorized (Polars/Numba) | Calibrated parametric model | 10⁴–10⁶ | ~ms |
| **Tier B — validation** | nautilus_trader, event-driven | Real book, latency, partial fills | 10²–10³ | ~s–min |
| **Tier C — full walk-forward** | nautilus_trader, full CPCV | Identical to B | 10¹–10² | ~min–h |

Tier A filters on gross metrics and trade-count minimums. Only survivors reach B. Only those clearing B's gates reach C.

**Critical**: the trial count for DSR/PBO accumulates **from tier A onwards**. The funnel saves compute; it does not grant an exemption from statistical control.

### 9.2 Calibrating tier A against tier B

The vectorized cost model is periodically recalibrated against event-driven results. Systematic error is monitored; if tier A consistently overestimates returns, its filtering is biased and must be readjusted. This is a lab health metric with its own dashboard.

### 9.3 Search

- **Grid** for small spaces and for mapping parameter surfaces (required by §8.5)
- **Random** as the baseline — it beats grid in high dimensions
- **TPE / CMA-ES** (Optuna) in later phases, with a caveat: adaptive search **increases** overfitting risk because it concentrates trials where noise is favourable. It demands a stricter DSR.
- **Declarative constraints** in `strategies.constraints` to prune absurd combinations (e.g. fast EMA ≥ slow EMA) before spending compute.

### 9.4 Determinism

Every experiment pins: `seed`, `code_version` (git sha), `image_digest`, `dataset_version_id`. Re-running it must produce bit-identical metrics. A CI test verifies this on a known case; if it fails, there is hidden non-determinism (thread ordering, hash randomisation, float reduction) and it is a blocking bug.

### 9.5 Cost and impact model

Backtest realism lives almost entirely here. The model is versioned (`cost_models`) and referenced by every experiment and run, so a result can never be compared against one produced under different cost assumptions.

- **Fees**: maker and taker separately, at the point-in-time tier the account actually held. Not tier 0.
- **Spread**: taken from recorded quotes, never assumed constant.
- **Taker slippage**: walk the recorded book depth for the order size. A parametric approximation is allowed in tier A, calibrated against tier B (§9.2).
- **Maker queue position**: the genuinely hard part. A resting limit order fills only once the volume ahead of it trades through. Default assumption is the back of the queue; anything more optimistic must be justified against observed fills.
- **Adverse selection on maker fills**: this is the one that quietly inflates results. Passive orders fill preferentially when the market is about to move against them, so a maker strategy evaluated without it looks far better than it is. Modelled as a conditional drift applied after each passive fill, estimated from historical data and recalibrated from own fills once live.
- **Market impact**: square-root law on participation rate, parametric until enough own fills exist to calibrate it. Feeds §12.7 capacity directly.
- **Funding**: applied at the venue's real marks, on mark price, not last price. Not a detail: measured on BTCUSDT from 2020 to 2026, funding averaged **0.0108% every eight hours — 11.8% a year, 78.7% cumulative**. That is on the same scale as the entire turnover drag of a three-day hold, so a cost model omitting it roughly halves the measured cost of any long-biased perpetual strategy. It is charged against the position held and signed, so a long pays it and a short receives it.
- **Latency**: measured against the venue, applied between `intent_ts` and `sent_ts`.

Every promotion report carries results under all three fill models (optimistic / realistic / pessimistic). **Promotion requires viability under `pessimistic`.** A strategy that only works under optimistic fills is a strategy that only works in the backtest.

### 9.6 Scaling backtests

Backtests are embarrassingly parallel across configurations, which makes scaling a matter of not wasting work rather than of distributed computing.

- **Deduplicate before dispatch.** `config_hash` is checked first; already-computed configurations are never re-queued. In a grid search with overlapping sub-spaces this alone removes a large fraction of the work.
- **Share the feature matrix, do not copy it.** Workers memory-map the Arrow/Parquet matrix read-only: one copy per machine, not per process. This is usually the difference between 4 workers and 32 on the same box.
- **Vectorise across parameters, not just across time.** In tier A, all RSI periods are computed in one pass over the data rather than one pass per period. The cost of 40 variants should be close to the cost of one.
- **Partition by (strategy, parameter chunk)** as Dagster assets, so a failed chunk retries alone and progress survives restarts.
- **Per-study compute budget.** A study that exceeds its budget halts and asks rather than silently consuming the cluster. The budget is recorded next to the trial count, because both are costs of the same search.

Horizontal scaling is deliberately deferred: with deduplication, shared matrices and parameter-vectorisation, a single well-specified machine covers tier A volumes well past the point where the statistical bottleneck (§8.8) binds. Add machines when measurement says to, not before.

---

## 10. Market regime engine

A regime classifier, causal and point-in-time (it cannot use future data to label the past).

Dimensions: direction (bull/bear/neutral) · structure (trending/ranging) · volatility (high/low, expanding/contracting) · risk (risk-on/risk-off, via macro correlation) · liquidity (high/low).

Methods: explicit rules over realised volatility, the Kaufman efficiency ratio, Hurst or band width; alternatively Hidden Markov Models or clustering, **always fitted only on data preceding each point**.

The trap, stated plainly because it is easy to fall into and invisible afterwards: **the thresholds must not know the future.** Calling a period "high volatility" because it sits above the median of the whole sample uses data from years later to label a day in 2020. Nothing raises, the labels look entirely sensible, and every per-regime result computed from them is contaminated — and the contamination flatters, because regimes defined with hindsight separate outcomes better than any regime identifiable at the time.

Every threshold is therefore an **expanding-window quantile over past data only**, and a regime label is treated as a feature for testing purposes: it must satisfy the truncation invariance of §16.3. The cost is that the first bars cannot be classified at all, and they are returned as `unknown` rather than guessed.

Usage: `experiment_results` carries one row per regime. A strategy that only works in one regime is not discarded — it is annotated, and its deployment is conditioned on detecting that regime live. But it must still meet a minimum trade count within that regime.

---

## 11. News & Event Intelligence

This is the project's central research bet, so it carries the heaviest statistical burden, not the lightest.

**What is not the edge**: reading news fast, or classifying it well. LLMs commoditised both, and headline bots arbitrage the obvious events in seconds. Competing there means competing on latency, which §0.2 already ruled out.

**What might be the edge**: the *shape* of the reaction — how large, how long it lasts, and how both change with market state. That is a modelling problem rather than a speed problem, it is poorly studied, and it is where a small operator with good data and honest validation can be genuinely better.

The object being estimated:

```
E[ z(h) | event ] = f(class, surprise, asset) · g(state) · decay(h)
```

where `z(h)` is the volatility-normalised cumulative abnormal return at horizon `h`.

### 11.1 Event construction

```
ingest → exact dedup → embed → cluster → Event ID → structured extraction → class assignment
```

**Event ID**: articles group by semantic similarity + entity overlap + temporal proximity (HDBSCAN over embeddings, windowed). A later article about an existing event **updates** it with a new revision; it never creates a second signal.

**Closed taxonomy.** Event classes come from a fixed, versioned list — not free-form LLM output. Free-form classes fragment the sample into cells of three events each and make everything downstream unestimable. A new class is a deliberate schema change, not an emergent one.

Starting taxonomy, ordered by sample size, which is the order they should be worked on:

| Class | Expected occurrences, 6 yr | Surprise computable? |
|---|---|---|
| `listing` / `delisting` | hundreds across venues | Partially (pre-announcement window) |
| `margin_change`, `funding_change` | hundreds | Yes (parameter delta) |
| `macro_release` (CPI, FOMC, NFP) | ~70–100 per series | **Yes — actual minus consensus** |
| `unlock` (token vesting) | hundreds, scheduled | Yes (size vs float) |
| `regulation` | tens | No |
| `hack` / `exploit` | tens | Partially (loss size) |
| `partnership` / `rumour` | many, mostly noise | No |

The two at the top are where the statistics can actually speak: **exchange announcements** (structured, timestamped, free, and the core of the differentiation thesis) and **scheduled macro** (the only family where surprise exists as a number *before* the event, rather than being inferred afterwards).

Free-text news sits at the bottom for a reason: unreliable timestamps, no deep archive, and the narrative problem of §11.10.

### 11.2 Point-in-time discipline

- `available_at` = publication + **measured** feed delay, never the ingest timestamp of a backfill
- Event state is versioned in `event_revisions`; a backtest at `t` sees only revisions with `available_at <= t`
- Source credibility is point-in-time too: a 2021 article cannot be scored with a 2026 reputation
- **News history is not backfillable.** Feeds have no deep archive, so capture must start before it is needed — the same constraint as liquidations and open interest (§4.1)

This is where a news system leaks the future most easily. §11.10 contains the test that detects it.

### 11.3 Abnormal return: the benchmark

"The market moved after the news" is meaningless without a counterfactual. Two estimators, because two kinds of event need different ones:

**Asset-specific events** (listing, hack, unlock) — cross-sectional benchmark:

```
ar_a(t) = r_a(t) − β_a · r_BTC(t)
```

with `β_a` estimated on a pre-event window `[t−30d, t−1d]`, embargoed so the estimation window never touches the event.

**Market-wide events** (macro releases) — no cross-sectional benchmark exists, because everything moves together. The counterfactual is a time-series one: expected return given market state, estimated on non-event periods in the same state.

**Volatility normalisation**, applied to both:

```
z(h) = CAR(h) / (σ_pre · √h)
```

Without it a 2% move in a quiet altcoin and a 6% move in a volatile one are not comparable, and pooling across assets produces garbage.

### 11.4 Market state index

Built in-house, not bought. A vendor fear-and-greed index is a black box, is partly composed of inputs already held, and can be revised — which breaks point-in-time correctness (§4.3).

The state vector is derived from Tier 1 derivatives data already collected, so it is point-in-time by construction and decomposable when something behaves oddly:

| Dimension | Components |
|---|---|
| Positioning | Funding rate z-score and cross-venue dispersion; basis slope; OI relative to market cap |
| Stress | Liquidation asymmetry (long vs short notional); realised vol vs its own trailing distribution |
| Participation | Trade count z-score; **average trade size** — falling size with rising count is retail inflow |
| Liquidity | Depth within ±25 bps; effective spread |

Participation deserves the emphasis: users are not observable, and **volume is a poor proxy** because wash trading inflates it. Trade count and average trade size are far better, and both fall straight out of the trades archive at no extra cost.

State enters the model as **3–5 discrete buckets**, not as continuous interactions. Sample size forbids continuous conditioning; §11.8 explains why.

### 11.5 The impulse response algorithm

The core estimation. Per `(class, state_bucket)`:

**Step 1 — Horizon grid.** `h ∈ {5m, 15m, 30m, 1h, 2h, 4h, 8h, 1d, 2d, 3d, 5d, 7d}`. It spans the §0.2 band with resolution on both sides of it, because the answer to "is this tradeable" lives at the boundary.

**Step 2 — Local projections.** One regression per horizon (Jordà), rather than fitting a VAR and imposing a decay shape:

```
z_i(h) = α(h) + β(h)·surprise_i + γ(h)·log(liquidity_i) + δ(h)·participation_i + ε_i(h)
```

Local projections are used because they impose no functional form on the response and degrade gracefully when the model is misspecified — which it will be.

**Step 3 — Fit the decay, in two components:**

```
IRF(h) = A·exp(−h/τ_fast) + B·exp(−h/τ_slow)
```

This decomposition is the point of the whole exercise. `τ_fast` is the mechanical reaction — real, and largely uncapturable, since it resolves before an order can be sized. `τ_slow`, if it exists, is the persistent component, and it is the only part that survives the cost arithmetic of §0.1.

Half-life is `τ·ln 2`. Both components are reported with confidence intervals.

**Step 4 — The decisive filter.** An event class is tradeable only if:

```
B · (1 − exp(−H/τ_slow)) > cost_round_trip ,  for some H within the §0.2 band
```

In words: the slow component must be large enough to cover a round trip **within a holding period the cost structure permits**. A class with a 2-hour half-life fails this regardless of how strong the reaction is, and it fails automatically rather than by judgement.

The prior worth testing first: structural events (listing, delisting, margin change, regulation) persist for days because they change who can trade what; sentiment and rumour events decay in hours. If that holds, the monetisable subset is small and concrete — which is good news, because it bounds the search.

**Step 5 — Walk-forward estimation, always.** The IRF applied at time `t` is fitted only on events with `available_at < t`, expanding window, refitted on a fixed cadence. An IRF fitted on the full sample and applied retrospectively is a textbook leak and will produce spectacular, entirely fictional results.

### 11.6 Scheduled events: anticipation, not reaction

Everything above models a **shock**: information arrives at `t`, the market
responds, the response decays. A large class of events is not shaped like that at
all, and treating them as though they were discards most of what they offer.

A token unlock, a quarterly expiry, an FOMC meeting, an ETF decision deadline, a
protocol upgrade — for these the date is known in advance, sometimes years in
advance. The information is not the announcement. **The information is the
approach.** Positioning builds as the date nears, and then the date arrives and
the uncertainty resolves.

Three consequences, and each breaks an assumption made above.

**Two timestamps, not one.** A scheduled event has `announced_at` — when the date
became public — and `scheduled_for` — when it happens. §11.2's `available_at`
covers the first and says nothing about the second, and the second is the one the
market is looking at.

**Time-to-event is the variable.** For a shock the axis is time *since*; here it
is time *until*, and the response is a function of how close the date is. A
feature reading "days until the next unlock in this asset" has no equivalent in
the impulse-response machinery of §11.5.

**The sign flips.** Risk appetite typically rises into a scheduled event and
reverses at it — "buy the rumour, sell the news" names the pattern because it is
common enough to have earned a name. A two-component exponential decay cannot
express a build followed by a reversal; it can only describe a response that
starts at its maximum. So the anticipation path is modelled separately:

```
E[ z(k) | scheduled event ]      for k = days until the date, k > 0
E[ z(h) | the date passing ]     for h = hours since, using §11.5
```

and the tradeable object is usually the **discontinuity between them**, not
either path alone.

**The anticipation window is declared, never fitted.** Searching for the window
that worked is searching for a parameter, and it is counted as one (§8.3).

#### Schedules are revised, and that is a point-in-time problem

The sharp version of §11.2 for this class. An unlock gets postponed, a decision
deadline is extended, an upgrade slips. A model that uses today's schedule to
label the past assumes the market knew a date it did not know, which is
look-ahead bias wearing a calendar.

So the schedule is versioned exactly as event content is: `schedule_revisions`
records what the expected date was believed to be, and when that belief was
available. A feature computing "days until the unlock" at time `t` uses the date
that was published as of `t`, not the date it eventually happened on.

This is easy to get wrong and invisible afterwards, because the labels look
perfectly reasonable — they are simply built from a future nobody had.

#### Where the sample is

Same discipline as §11.1: the classes worth modelling are the ones with
occurrences to count.

| Class | Sample | Date known in advance | Mechanism |
|---|---|---|---|
| **Token unlocks** | hundreds, published vesting schedules | Yes, months ahead | Mechanical supply arriving |
| Quarterly expiry | 4 a year per venue, years of them | Yes, fixed | Positioning rolls or closes |
| Macro releases | ~70–100 per series | Yes, calendar | Consensus exists, so surprise is computable |
| Listings | hundreds | Partially, announcement precedes listing | Access changes |
| Halving | four, ever | Yes, by construction | No sample. Interesting, unestimable |

Unlocks lead for the same reason listings led in §11.1: hundreds of occurrences,
dates published ahead, and a mechanism that does not depend on anyone's
interpretation — a quantity of supply arrives whether or not anybody wrote about
it.

The halving belongs in the table only to be excluded. Four occurrences cannot
support a conditional model, and the temptation to reason about it anyway is
exactly what §8.8 exists to resist.

### 11.7 Three shapes, and why sign is not the signal

Events differ along two axes, and only one of them is the obvious one.

**Sign** — positive or negative for the asset — is the axis everyone reaches for
first and the less useful of the two. What is tradeable is the **residual** of
§11.10: observed reaction minus expected. Positive news that should have moved
+2σ and moved +0.2σ is bearish information; lukewarm news that moved +4σ is
bullish. Sign is therefore an **input to the expected-reaction model**, never a
signal in itself, and a system trading sign directly is trading the part of the
reaction everyone else has already priced.

**Shape** — how the response is distributed in time — is the axis that changes
the model. Three of them, each needing different machinery:

| Shape | When the market acts | Example | Modelled by |
|---|---|---|---|
| **Shock** | Immediately, then decays | Hack, surprise CPI print | §11.5 impulse response |
| **Schedule** | Builds before a known date, resolves at it | Unlock, expiry, FOMC | §11.6 anticipation path |
| **Process** | Gradually, as a sequence of resolutions | Regulatory consultation, governance vote, announced intent to accumulate | below |
| **Regime shift** | Immediately, and does not come back | War breaking out, FTX collapsing, an outright ban | §10, not §11 |

#### Regime shifts do not have a reaction, they have a new baseline

The fourth is the one the impulse-response framework cannot hold, and it matters
because the examples are the events people most want to trade.

Every model above estimates a **deviation from a baseline and a return to it**.
`τ_slow` is the rate of that return. A war breaking out, a major venue collapsing
or a jurisdiction banning the asset does not deviate from the baseline — it
**replaces** it. Fitting a decay to such an event estimates a reversion that
never happens, and the fitted `τ` is meaningless rather than merely imprecise.

Three consequences:

- **They belong to §10, not §11.** A regime shift is a change of market state, so
  it is handled by the regime classifier — which already conditions every result
  by regime — rather than by the reaction engine.
- **They are market-wide**, so §11.3's cross-sectional benchmark does not exist:
  there is no unaffected asset to difference against, and the time-series
  benchmark is the only option.
- **The sample is hopeless.** Wars, exchange collapses and bans number in the
  handful, and §8.8 applies. A conditional model over a handful of occurrences
  estimates nothing, and the honest use is as a **conditioning variable** — was
  the market in a post-shift regime — rather than as a signal.

The gap this exposes in §11.1's taxonomy is real: geopolitical events are not in
it. They are not added as a tradeable class, because the sample forbids it. They
are added as a regime input.

#### Processes: news that starts something rather than being something

The third is what the other two miss, and it is common. A regulator opens a
consultation; an exchange says it will "consider" a listing; a protocol proposes
an upgrade; a treasury announces an intent to buy over coming quarters. None is a
shock, because the outcome is not yet known, and none is a schedule, because
there is no date.

What exists instead is a **probability distribution over outcomes that updates as
the process advances**. The tradeable object is not the announcement — it is each
revision of that distribution, and the announcement is merely the first.

So a process event is a state machine, and every transition is an event with its
own reaction:

```
proposed → under_review → decided → implemented
                       ↘ withdrawn
```

Two consequences, both easy to get wrong:

- **Each transition is measured separately.** The reaction to `proposed` and the
  reaction to `decided` are different quantities, and pooling them estimates
  neither. The class in §11.1's taxonomy is `(process_type, transition)`.
- **A transition supersedes its predecessor.** This is the concrete case behind
  supersession below: when `withdrawn` arrives, a position built on `proposed` is
  not decaying, it is wrong. The feature goes to zero at the transition rather
  than fading from it.

The sample problem is severe — a regulatory process has a handful of occurrences,
not hundreds — so processes enter the research campaign last, after unlocks and
listings, and most likely as a conditioning variable rather than a signal of
their own.

### 11.8 When a news item expires

"How long does it last" has three distinct answers that a single decay constant
cannot carry, and conflating them is how a model keeps trading information that
stopped being information.

**Decay** — the residual reaction shrinks with time, which is `τ_slow` in §11.5.
Continuous, and the only one the impulse response models.

**Supersession** — a later event about the same subject makes the earlier one
stale. §11.1 already groups both under one Event ID and revises it, but a
revision is treated there as an *update*, when often it is an *invalidation*: a
rumoured partnership that is denied does not decay, it is cancelled. The feature
must go to zero at the revision, not fade from it.

**Resolution** — a scheduled event happens, and the anticipation it supported
ends discontinuously whatever the outcome. Not decay, not supersession: the thing
being anticipated no longer lies ahead.

Each needs its own treatment, so `event_reactions` carries which one ended the
signal rather than assuming time did.

The practical test that separates them: **decay is a rate, the other two are
dates.** A model with only a rate will keep a position open on an item that was
cancelled yesterday, because its clock says there is still half-life remaining.

### 11.9 Hierarchical pooling

Cells are sparse and always will be. Conditioning impact on `class × state × participation` with 7 classes, 4 states and 3 participation levels gives 84 cells; at 30 events per cell that needs 2,500 events **per class**. Only listings come close.

So estimates are **partially pooled**, shrinking cell estimates toward the class mean and class means toward the global mean, with shrinkage proportional to `1/n`:

```
α_{c,θ}(h) ~ Normal( α_c(h), σ²_class )
α_c(h)     ~ Normal( α_global(h), σ²_global )
```

Implementation is **empirical Bayes** (closed-form James–Stein style shrinkage), not MCMC. It is deterministic, which §9.4 requires; a sampler introduces randomness that then has to be seeded and defended.

`n_events` is stored alongside every coefficient and is **displayed wherever the coefficient is displayed**. A number backed by six events must never be presentable without that six being visible next to it.

### 11.10 The tradeable signal

At prediction time, for an event observed `h_obs` ago:

```
reaction_surprise   = z_observed(h_obs) − IRF_predicted(h_obs)
expected_remaining  = IRF_predicted(H) − IRF_predicted(h_obs)
```

Two features go to the feature engine (§6):

- **`reaction_surprise`** — under- or overreaction relative to what this class, in this state, normally does. Positive news that should move +2σ and moved +0.2σ is a different observation from lukewarm news that moved +4σ.
- **`expected_remaining`** — how much of the move is still ahead, net of what already happened.

The entry condition follows from the model rather than from a threshold someone chose:

```
expected_remaining · σ_pre  >  cost_round_trip + margin
AND  implied_hold(τ_slow)   ∈  [2d, 7d]     # §0.2
```

The algorithm enforces the horizon constraint itself. A signal whose implied holding period is three hours is rejected by construction, not by discipline.

### 11.11 Baselines and diagnostics

Event studies fail in characteristic ways. Three checks, all mandatory before any IRF is usable:

**Pre-event flatness.** The estimated IRF must be indistinguishable from zero for `h < 0`. A non-zero pre-event drift means one of two things, and both are serious: the `available_at` timestamp is wrong, or the information reached the market before your feed. This is the single most valuable diagnostic in §11, because it directly tests the honesty of the timestamp everything else depends on.

**Placebo events.** Same class, randomised timestamps, matched to the same state distribution. The IRF must be flat. Repeated 1,000 times, this gives the empirical null the real IRF must beat.

**Shuffled class labels.** Permuting class assignments across events must collapse the IRF. If it survives, the model is fitting something that is not the event class.

And the standing hazard, which no test fully removes: **the narrative fallacy.** Given any large move, financial media reliably produces an explanation. Retrospective matching of moves to headlines is overfitting with prose attached, and it is more persuasive than the numerical kind precisely because it comes with a coherent story. The residual formulation of §11.9 is the structural defence: it asks whether the reaction differed from expectation, not whether a story exists.

### 11.12 Gates

Before an IRF-derived feature reaches the feature engine:

- `n_events ≥ 30` in the cell, or the estimate is pooled and flagged as such
- Pre-event flatness passes
- IRF beats the placebo null at the 95th percentile
- `τ_slow` exists with a confidence interval excluding zero
- The §11.5 step 4 cost filter passes for some horizon inside the §0.2 band
- Walk-forward: the IRF fitted on data before `t` predicts reactions after `t` better than the unconditional mean

Clearing all six produces a *feature*, not a strategy. It then enters the funnel at §9 and faces §8 like anything else. That the news engine is the centre of the project is a reason to hold it to the standard, not a reason to exempt it — narrative is exactly where conviction outruns evidence.

---

## 12. Execution modes: the ladder to real money

The system operates in **four modes**, not two. Jumping straight from simulator to production is the most expensive mistake in projects of this kind, because "fake money" covers two distinct tests that do not substitute for each other: the **alpha** test (does the strategy make money?) and the **integration** test (can my code talk to the exchange without breaking?). A local simulator validates the first and never touches the second.

### 12.1 The four rungs

| Mode | Data | Execution | Money | What it proves | What it does NOT prove |
|---|---|---|---|---|---|
| **1. BACKTEST** | Historical | Simulated locally | Fake | That alpha exists historically | Anything about real time |
| **2. SIMULATOR** (paper) | Live, real | Simulated locally | Fake, configurable | That alpha survives on unseen data, in real time | That the exchange accepts your orders |
| **3. TESTNET** (sandbox) | Live, testnet | **Real**, against the exchange matching engine | Exchange fake money | That integration works: API, limits, rejections, precision, reconnection | Real impact and slippage (the testnet book is unreal) |
| **4. LIVE** | Live, real | Real | **Real**, ramped in tiers | Everything else: real queue position, real fills, real impact | — |

Why rung 3 is not optional: an entire family of failures appears only against a real exchange — rounding to `tick_size`/`lot_size`, `min_notional`, margin rejections, signature errors, rate limits, `-1021 timestamp outside recvWindow`, websocket reconnection with event loss, clock drift. None of these exist in a local simulator, and all of them show up on day one with real money if you have not hunted them down first.

Why rung 2 is not optional either, given rung 3: the testnet book is empty and its prices drift away from the real market. It validates integration, **not** performance. Testnet numbers feed into no decision about the strategy.

### 12.2 One engine, four adapters

The strategy is written once and does not know which mode it runs in. Mode is configuration, not code.

```python
class ExecutionMode(StrEnum):
    BACKTEST  = "backtest"
    SIMULATOR = "simulator"
    TESTNET   = "testnet"
    LIVE      = "live"

@dataclass(frozen=True)
class RunConfig:
    mode: ExecutionMode
    strategy_id: UUID
    params: dict                 # frozen: see §12.4
    initial_capital: Decimal     # fake or real capital
    base_currency: str
    universe: list[str]
    risk_limits: RiskLimits      # §14.1 — mandatory in EVERY mode
    cost_model_id: UUID
    data_feed: FeedRef
    venue_adapter: AdapterRef
    seed: int | None             # BACKTEST only
    code_version: str
    image_digest: str
```

The only thing that changes between modes is the pair (`data_feed`, `venue_adapter`). The decision code is byte-for-byte identical, and its `code_hash` is recorded on every run, so it is provable that the strategy running live is literally the one that passed validation.

nautilus_trader implements exactly this pattern, which is the main reason it is in the stack.

`risk_limits` are enforced **in simulator and backtest too**. If the limiter only activates in production, its first real execution is with real money — which is precisely what we want to avoid.

### 12.3 Simulator configuration

SIMULATOR mode parameters, all versioned alongside the run:

- **`initial_capital`** — starting fake amount
- **`sizing`** — fraction of equity per position, or fixed risk per trade as % of equity
- **`leverage_max`**, **`positions_max`**, **`exposure_max`** — these mirror `risk_limit_configs` (§14.1) rather than being simulator-only knobs; the same limits are enforced in every mode, so the limiter's first real execution is never with real money
- **`fee_tier`** — the real tier your volume would earn, not the optimistic tier 0
- **`latency_model`** — network latency measured against the venue, not assumed
- **`fill_model`** — `optimistic` (everything at mid), `realistic` (walks the book), `pessimistic` (worst price of the level). Defaults to `realistic`, and the promotion report includes all three.
- **`funding`** — applied at the venue's real funding marks
- **`reset_policy`** — whether equity resets between sessions or accumulates. Defaults to **accumulate**: a simulator that resets daily hides drawdown.

**Fake capital must be the same order of magnitude as the planned real capital.** Simulating with 1,000,000 and trading with 5,000, or vice versa, invalidates the test: market impact, exchange minimum viability and strategy capacity all depend on size. If the plan is to start with €5,000, the simulator runs with €5,000.

### 12.4 What can and cannot be tuned in the simulator

There is a trap here worth seeing before falling into it.

The requirement is to be able to "polish parameters" in the simulator. The problem: if you adjust signal parameters based on what you see in the simulator, that period stops being out-of-sample. After three or four iterations you have optimised the strategy over the last few weeks of market — overfitting exactly as in the backtest, only slower and more convincing, because it looks "live".

Operational boundary:

| Tunable in SIMULATOR | Must return to the funnel (§9) |
|---|---|
| Sizing and risk limits | Signal parameters (periods, thresholds, features) |
| Order type (limit vs market, offset, TTL) | Entry and exit rules |
| Cost and latency model calibration | Take profit / stop loss in the logic |
| Re-evaluation frequency, trading hours | Feature selection |
| Retries, timeouts, error handling | Asset universe |
| Alerting and monitoring thresholds | Label definition |

Hard rule: **touching anything in the right-hand column creates a new strategy** (new `strategy_id`, new `code_hash`) that returns to `screening` and adds its trials to the counter in §8.3. The system enforces it: a run's `params` are immutable, and modifying them produces a different run, never an edit.

What is legitimate to do in the simulator with the results: **conclude the strategy does not work** and retire it. Rejecting is free; re-tuning costs the entire validation.

### 12.5 Backtest ↔ simulator reconciliation

Not a visual comparison — an automated daily procedure:

1. The same period the simulator saw is re-backtested, using the data the simulator actually received (not the cleaner history available afterwards)
2. Paired comparison of the return series
3. Bootstrap over the difference series → confidence interval on the divergence
4. **Divergence decomposition** into four buckets: costs, signal timing, fill quality, data differences

The fourth point is the one that pays. Knowing *that* they diverge is useless; knowing that 80% comes from worse-than-modelled fills tells you exactly what to fix. And a large divergence is not a simulator failure — it is evidence the backtest was wrong, and it feeds back into the cost model.

### 12.6 Simulation dashboard

Grafana, live, over Postgres/Timescale:

Equity curve vs backtest equity for the same period · current and maximum drawdown · realised / unrealised P&L · positions and exposure per asset · trades with expected vs obtained price (per-trade slippage) · cumulative fees and funding · signal→order→fill latency · rejection and error rates · rolling metrics (30d Sharpe, win rate, profit factor, turnover) · cumulative divergence vs backtest with its confidence band.

Rolling metrics are **descriptive**, not decision criteria: a 30-day Sharpe has an enormous standard error and reacting to it is reacting to noise.

### 12.7 Capacity

Before any rung involving real money, the report must include a **capacity analysis**: the strategy is re-backtested at increasing position sizes using the impact model calibrated against the real book, locating the point where net alpha degrades below threshold.

That number is the strategy's capital ceiling. Trading above it guarantees it stops working, and it is the most frequent reason a validated strategy dies on scaling.

## 13. Portfolio construction and allocation

Everything up to here validates one strategy at a time. Production runs several at once, and that is a different problem: **five strategies that each pass §8 independently can be the same bet wearing five hats.** Their individual validations say nothing about that, and the failure shows up as a drawdown five times deeper than any of them predicted.

### 13.1 Strategy correlation

Correlation is measured on **realised return series**, not on parameters, features or asset overlap. Two strategies can share no features and be perfectly correlated; two can share every feature and be uncorrelated.

- Computed on OOS and simulator returns only — in-sample correlation is as meaningless as in-sample Sharpe
- Hierarchical clustering on correlation distance; a cluster is treated as **one bet**
- Computed **per regime as well as overall** (`strategy_correlations.regime`). Correlations converge toward 1 under stress, so the number that matters for risk is the crisis-regime correlation, not the average one
- Recorded with `n_obs`: a correlation estimated on 20 overlapping trades is noise

### 13.2 Allocation

Capital is allocated **across clusters first, then within them**.

Naive mean-variance optimisation is rejected: it is famously unstable when expected returns are estimated rather than known, and it concentrates capital into whichever strategy has the luckiest estimate. The default is **Hierarchical Risk Parity** or equal risk contribution across clusters — both are robust to estimation error, which is the dominant error here.

- Hard cap per strategy and per cluster, independent of what the optimiser wants
- Rebalancing on a fixed cadence, with turnover cost charged to the decision. A rebalance that costs more than the risk it reduces is not worth doing
- A new strategy enters at the bottom of the capital ramp (§14.5) regardless of what the allocator suggests

### 13.3 Portfolio-level multiple testing

The trial counter does not stop at the strategy boundary. **Every strategy promoted to the portfolio is itself a trial at the portfolio level**, and a portfolio assembled from the best of N validated strategies has a selection bias of exactly the kind §8.3 exists to correct.

- A portfolio-level counter mirrors `studies.executed_trials`, incremented on every promotion and every allocation change driven by performance
- Portfolio Sharpe is deflated by that count before it is reported anywhere
- Rule of thumb that follows: adding a marginal strategy to the portfolio has to clear a **higher** bar than the first one did, not a lower one

### 13.4 Netting and attribution

Two strategies may want opposite positions in the same asset. The decision is explicit: **net at the venue, attribute internally.** Netting saves fees and margin; the intent log (§14.3) preserves what each strategy wanted, so per-strategy P&L attribution survives.

What is not allowed is netting silently and then reporting per-strategy results as though each traded alone — that turns attribution into fiction precisely when it is needed to decide which strategy to cut.

### 13.5 Aggregate capacity

Individual capacity ceilings (§12.7) **do not add up**. Two strategies trading the same asset draw on the same liquidity, and their combined order flow competes with itself.

Portfolio capacity is computed jointly from the combined flow, not summed from the parts. It is recomputed whenever a strategy is added, and it caps the capital ramp: the portfolio never scales past its joint ceiling, even if every individual strategy still has room.

---

---

## 14. Risk controls and operational safety

These controls are a requirement for modes 3 and 4, and are exercised from mode 1 onward.

### 14.1 Hard limits and kill switch

Evaluated by a component **independent of the strategy**, able to veto any order:

- `max_open_positions` and `max_open_orders` — **counted by the risk engine, not by the strategy.** A strategy that miscounts its own positions is exactly the case this limit exists for
- `max_positions_per_cluster` — five strategies in one correlation cluster are one bet (§13.1), so limiting positions per strategy does not limit the exposure that matters
- `min_position_notional` and `max_position_notional` per asset
- `min_position_pct_equity` and `max_position_pct_equity`
- `min_seconds_between_entries` on the same asset — blocks churn from a signal flickering around its threshold
- `max_gross_exposure` and `max_net_exposure` for the portfolio
- `max_leverage`
- `max_daily_loss` → **automatic halt** and cancellation of open orders
- `max_drawdown_from_peak` → halt, with human review required to resume
- `max_orders_per_minute` → firebreak against order loops
- `max_consecutive_errors` → halt
- Price deviation: reject orders more than X bps away from mid (protects against corrupt data)

**Manual kill switch**: one command and one button that cancel everything, close positions in `reduce_only` mode and leave the system halted. It must work even if the orchestrator is down — meaning it cannot depend on the same process it is trying to stop.

**Dead man's switch**: if the system loses its heartbeat, the exchange cancels open orders on its own. Binance and other venues support this natively; it is always enabled in LIVE mode.

#### Minimum viable position, and why the capital ramp has a floor

The minimum tradeable size is not `min_notional`. It is:

```
min_viable(asset, t) = max( min_notional(t), lot_size(t) x price(t) )
```

resolved from `asset_universe` **as of the simulated instant** (§4.4), never from today's parameters. Measured on Binance USD-M in September 2026:

| Asset | Price | lot_size | min_notional | **min_viable** |
|---|---|---|---|---|
| BTCUSDT | 84,532 | 0.001 | 50 | **84.53** |
| ETHUSDT | 2,690 | 0.001 | 20 | 20.00 |
| SOLUSDT | 117 | 0.01 | 5 | 5.00 |
| ADAUSDT | 0.249 | 1.0 | 5 | 5.00 |

For BTC the lot size binds, not the notional minimum — so reading `min_notional` alone gives the wrong floor by a factor of 1.7.

Two consequences, and the second is a defect in §14.5 as originally written:

**Sizing granularity degrades at small capital.** Positions quantise to `lot_size x price`. Wanting €150 of BTC when the increment is €84.53 yields €84.53 or €169.06 — the realised position differs from the intended one by up to 44%, which means realised risk differs from intended risk by the same margin. Below roughly twenty increments of headroom, position sizing stops being a control.

**The capital ramp needs a floor.** §14.5 sets L0 at 1% of target. For a €5,000 target that is €50, and BTC cannot be opened at all at that size. A tier you cannot stand on is not a tier, so:

```
capital(L0) = max( 0.01 x target , positions_target x max_asset min_viable )
```

If that floor exceeds the target, the strategy is not deployable at that capital and the honest answer is to say so before the ramp starts rather than after L0 fails to fill.

### 14.2 State reconciliation with the exchange

The position the system *believes* it holds and the one it *actually* holds will diverge sooner or later: fills lost to disconnection, partial liquidations, orders the exchange accepted whose acknowledgement never arrived.

- Periodic reconciliation (every N seconds) of positions, open orders and balances against the venue
- **Mandatory reconciliation at startup, before issuing the first order.** A system that restarts after a crash and trades without reconciling can double an existing position.
- Discrepancy detected → immediate halt, never silent self-correction
- Everything recorded in `position_reconciliations` for audit

### 14.3 Idempotency and recovery

- Every order carries a **deterministic `client_order_id`**, so a retry after timeout does not create a second order
- State is persisted before sending, not after: if the process dies between send and acknowledgement, on startup it knows that order might exist and goes looking for it
- Append-only intent log: *what the strategy wanted to do*, in addition to what was executed. This is what makes it possible to reconstruct why a position was opened

### 14.4 Credentials and attack surface

- **Separate API keys per mode**, never shared between testnet and live
- Minimum permissions: trading yes, **withdrawals no**. No exceptions
- IP whitelist at the exchange
- Secrets outside the repository and outside the research container's environment: the lab does not need production keys
- LIVE keys are accessible only to the execution process, not to backtest workers and not to the AI agent

### 14.5 Capital ramp

Going to production is not a switch, it is a ramp. Each tier requires a minimum period and metrics within band:

| Tier | Capital | Minimum duration | Advancement criteria |
|---|---|---|---|
| L0 | 1% of target (or exchange minimum) | 2 weeks | Zero integration incidents, clean reconciliation, slippage within model |
| L1 | 10% | 4 weeks | Divergence vs simulator not significant, drawdown within expectation |
| L2 | 33% | 6 weeks | Same, plus ≥ 50 cumulative trades |
| L3 | 100% | — | Formal review and recorded approval |

Any limit breach, or sustained significant divergence, **drops one tier**. No tier is skipped, and the criteria are versioned exactly like the validation gates (§15).

### 14.6 Alpha decay detection

§15 retires a strategy for "sustained degradation". Left undefined, that phrase resolves to whatever the operator feels after a bad month, which is the worst possible decision rule: it retires good strategies inside normal drawdowns and keeps broken ones during lucky streaks.

It is therefore a pre-registered statistical test, fixed **before** the strategy goes live:

- Validation produces an expected return distribution (mean, volatility, and a Monte Carlo drawdown distribution from §8.6). That distribution is the null hypothesis for live performance.
- A **sequential test** runs continuously on live returns — CUSUM on the cumulative deviation, or an SPRT of "Sharpe equals the validated value" against "Sharpe is zero". Sequential, because the decision is being made repeatedly and a fixed-sample test applied daily is a multiple-testing violation in its own right.
- Thresholds and the required evidence to retire are set in advance and versioned.

Two rules that follow, and that matter as much as the test:

**A drawdown inside the validated distribution is not evidence of failure.** If Monte Carlo said a 22% drawdown occurs in 1 run out of 5, then seeing 22% is information you already had. Retiring there destroys a working strategy and, worse, teaches the lab to retire on discomfort.

**Separate "not working" from "its regime is absent."** A regime-conditional strategy producing nothing during a regime it was never meant to trade is behaving correctly. The test conditions on regime (§10) before concluding decay; a strategy flat in the wrong regime goes dormant, not retired.

Outcome of the test feeds the lifecycle: `warn` raises monitoring, `breach` halts and demands review, sustained `breach` retires.

---

## 15. Strategy lifecycle

State machine with transitions bound to hard criteria. No transition is manual without a record.

```
draft → screening → validating → candidate → simulator → testnet → live_ramp → live
                        ↓            ↓           ↓          ↓          ↓        ↓
                     rejected     rejected   rejected    rejected   halted   retired
```

| Transition | Criteria |
|---|---|
| `screening → validating` | Tier A: ≥ minimum trade count, beats buy & hold net of costs, exceeds the 95th percentile of the random null |
| `validating → candidate` | DSR p < 0.05 with full trial count · PBO < 0.3 · `param_stability` above threshold · ≥ 100 OOS trades · positive in ≥ 2 regimes · survives 2× costs · capacity analysis completed (§12.7) |
| `candidate → simulator` | Recorded human approval + leakage review + signed report |
| `simulator → testnet` | ≥ 60 days in simulator · divergence vs backtest not significant · ≥ 30 trades · zero unhandled errors |
| `testnet → live_ramp` | ≥ 2 weeks on testnet · zero rejections from formatting or precision bugs · clean reconciliation · kill switch and dead man's switch proven in drill |
| `live_ramp → live` | Tiers L0–L3 completed per §14.5 |
| `* → halted` | Hard limit breach, reconciliation discrepancy, or error rate above threshold |
| `* → rejected/retired` | Criterion failure, sustained degradation as defined by the §14.6 sequential test (never by operator judgement alone), or structural regime change |

Thresholds live in versioned configuration, not in code, and **changing them is an audited event**. Lowering the bar when nothing clears it is the most common way a lab stops being one.

One detail worth noting: `simulator` requires 60 days because a strategy with infrequent signals needs time to accumulate enough trades. If it does not reach 30 trades in 60 days, it has not failed — it cannot be evaluated, and the decision is whether to give it more time or discard it as unviable.

---

## 16. Reproducibility, testing and quality

### 16.1 Versioning

Four things are versioned by content hash and referenced from every experiment and every run:

1. **Data** — `dataset_versions.content_hash` (hash of the Parquet file manifest)
2. **Code** — git sha + container digest
3. **Definitions** — `code_hash` of features, labels, strategies, cost models and regime models
4. **Configuration** — `config_hash` of the experiment, or the `RunConfig` of the run

Dagster materialises each asset with its lineage, so "which data and which code produced this number" is a query, not an investigation.

Operational consequence: given a trade in production, the full chain can be reconstructed back to the experiment that promoted that strategy and the dataset it was validated against.

### 16.2 What the test suite must protect against

Testing this system is not like testing ordinary software, and designing the suite as though it were is the most likely way to end up with high coverage and no protection.

**The bugs that matter here do not crash.** A look-ahead bug does not raise an exception — it returns a *better* Sharpe ratio. A wrong fee tier does not fail a request — it makes a losing strategy look profitable. A mis-sized purge produces a clean, plausible, entirely invalid validation report. Conventional "does it run" testing catches almost none of this, because the failure mode is a wrong number that looks right.

Three consequences shape the suite:

- **For most outputs there is no expected value to assert.** Nobody knows what the correct Sharpe is. So the suite cannot be built on expected outputs; it is built on **invariants**, on **synthetic data where the truth is known by construction**, and on **differential comparison** between two implementations that must agree.
- **Coverage is the wrong metric.** 100% line coverage says nothing about whether the validator rejects noise. The metric that matters is the **canary catch rate** (§16.5): the fraction of deliberately broken inputs the suite detects.
- **The suite must be able to fail the science, not just the code.** The phase 3 exit criterion — the system rejects a strategy built on pure noise — is a test, and it runs nightly like any other.

Seven layers, ordered by how much they protect:

| Layer | Catches | Where |
|---|---|---|
| 1. Unit and contract | Ordinary bugs, schema drift, API breakage | Everywhere |
| 2. Temporal invariants | **Look-ahead bias, generically** | §16.3 |
| 3. Synthetic ground truth | A validator that accepts noise or rejects real edges | §16.4 |
| 4. Leakage canaries | Anti-leakage machinery that silently stopped working | §16.5 |
| 5. Differential and golden | Refactors that change results; tier A/B disagreement | §16.6 |
| 6. Execution and drills | Integration failures, unsafe recovery, risk limits that do not fire | §16.7 |
| 7. Data assertions | Bad data, continuously, in production | §16.7 |

Layers 2, 3 and 4 carry most of the value and are the ones almost nobody builds.

### 16.3 Temporal invariants (property-based)

Written with `hypothesis`, over generated inputs rather than fixed cases.

- **Truncation invariance.** For any feature and any t, the value computed on data truncated at t equals the value computed on the full dataset. This is the **generic look-ahead detector**: it finds leakage without knowing where it is, which is why it is the single most valuable test in the system.
- **Decision truncation invariance.** The same property for a whole backtest: decisions up to t are identical whether run on data truncated at t or on complete history.
- **Row-order invariance.** Reordering rows that share a timestamp does not change any output. Catches accidental dependence on arrival order.
- **Declared lookback honesty.** Perturbing rows older than a feature's declared `lookback_bars` must not change its output. A feature that reaches further back than it declared fails here — and since `lookback` drives purge size (§8.2), that is a correctness bug, not a documentation one.
- **Purge sufficiency.** For every experiment, `purge ≥ label_horizon + max(feature lookback)`. Asserted at construction, so an invalid CV scheme cannot be built.
- **`available_at` monotonicity.** No row is ever visible before it exists; revisions never lower the availability of their predecessor.

### 16.4 Synthetic ground truth

Seeded generators that produce data where the answer is known by construction. This is the only place where "the system got it right" is a checkable statement.

| Generator | Produces | The suite asserts |
|---|---|---|
| `make_noise(seed)` | Zero-drift series with GARCH volatility clustering, no edge | **Every strategy is rejected**, after 10⁵ trials. Phase 3's exit criterion |
| `make_edge(sharpe, horizon, seed)` | Noise plus a predictable component of known size | The edge is found, and the estimated Sharpe falls within its confidence interval |
| `make_subthreshold_edge(seed)` | A real edge *below* the study's minimum detectable effect | Verdict is `inconclusive`, never `pass`. This is what proves §8.8 actually bites |
| `make_regime_switch(seed)` | Two regimes with different dynamics and known switch points | The regime classifier recovers the switches within tolerance, using only past data |
| `make_costed(drag, seed)` | Known analytic cost drag | Backtest net return matches the analytic expectation to tolerance |
| `make_survivorship(seed)` | A universe where losers are delisted | Results computed on the point-in-time universe differ from the naive one, in the expected direction |

The `make_subthreshold_edge` case deserves emphasis: it tests the system's willingness to say "I do not know". A validator that passes it is one that claims more than the data supports, and that is the failure this whole project exists to prevent.

### 16.5 Leakage canaries

Deliberately broken inputs, injected on purpose, with an expected detector for each. The suite fails if any canary goes undetected.

| Canary | Expected detector |
|---|---|
| Feature returning next-bar return directly | Truncation invariance (§16.3) |
| Feature peeking exactly one bar ahead | Truncation invariance — the subtle case, and the one that proves the test has teeth |
| Feature using `event_time` instead of `available_at` | Causality verifier |
| Feature reaching further back than its declared `lookback` | Lookback honesty property |
| Swing-high level confirmed with future bars, then read at the bar it formed | Truncation invariance (§16.3). The classic level-based leak, and the reason §6.2 exists |
| Purging disabled | PBO spikes detectably on `make_noise` |
| Label horizon longer than the declared purge | Build-time assertion, before anything runs |
| Delisted assets silently dropped from the universe | Survivorship generator comparison |
| Fee tier set to zero | Cost sensitivity sweep flags implausible cost-free profitability |
| `studies.executed_trials` reset mid-campaign | Monotonicity constraint at the database level |
| Event timestamps shifted earlier by one hour | **Pre-event flatness** (§11.10): the IRF stops being flat before `h=0`. This is the canary that proves `available_at` is honest |
| IRF fitted on the full sample, applied retrospectively | Walk-forward gate (§11.11): the full-sample IRF beats the walk-forward one implausibly |

Two rules govern this set:

- **Catch rate must be 100%.** It is the suite's headline metric, reported in CI, and it replaces coverage as the number that matters.
- **The set only grows.** Every bug found in production becomes canary N+1 before the fix is merged. An anti-leakage system that has never caught a leak is not demonstrated to work; one whose canary set has grown from bugs found in the wild is.

### 16.6 Differential and golden tests

- **Golden run.** A pinned configuration whose metrics must reproduce bit-identically. Failure means hidden non-determinism — hash randomisation, float reduction order, a library upgrade that moved a number — and is a blocking bug (§9.4). A companion test runs the same computation at 1, 2 and 8 threads and asserts the digests agree, so thread-invariance is measured rather than assumed (§2.2).
- **Regression corpus.** Roughly 50 frozen experiments spanning the feature families. A refactor that changes any of their results must either be reverted or accompanied by an explicit, reviewed explanation of why the number changed. This is what makes it safe to keep refactoring a system whose outputs nobody can eyeball.
- **Tier A ↔ tier B differential.** The same configuration under the same cost model must agree within tolerance across the two engines. Divergence beyond tolerance means the screening filter is biased and is promoting the wrong candidates (§9.2).
- **Backtest ↔ simulator differential.** Replaying captured live data through the backtest must produce the same decisions the simulator made. Any difference is a data or timing bug, not a strategy result.
- **Performance regression.** Tier A throughput is a specified requirement (10⁴ configurations in under an hour, phase 2). It is benchmarked, and a regression fails CI — otherwise it degrades quietly until the funnel stops being viable.

### 16.7 Execution, failure drills and data assertions

**Execution tests** run against recorded venue responses (cassette-style fixtures) in CI, and against live testnet before any promotion:

- Idempotency: the same `client_order_id` sent twice produces exactly one order
- Precision: quantities and prices round correctly to `tick_size`, `lot_size` and `min_notional`, per venue and point-in-time
- Rejection handling: every documented venue error code has a defined behaviour and none of them crash the executor
- Reconciliation: an injected position discrepancy halts the run and writes to `position_reconciliations`
- Risk limits: **each rule in `risk_limit_configs` has a test that fires it at its threshold.** A limit without a test that has seen it fire is a limit that has never run

**Failure drills**, automated where possible, exercised in simulator and testnet before LIVE: websocket drop with an open position · process restart with a live position · order rejection for insufficient margin · corrupt data (zero price, 50% jump) · rate limit reached · clock drift · kill switch during execution · registry restore from backup (§16.10).

Each has a documented expected behaviour. If the system has never crashed in a drill, it will crash for the first time in production.

**Data assertions** are different in kind: they run continuously against production data, not in CI against code. Every ingestion batch is checked for gaps beyond the source SLA, duplicates, out-of-range values, cross-source inconsistency and backfill-vs-live mismatch (§4.6). Failures open `data_quality_issues`, and a `blocking` issue prevents promotion of anything validated on the affected window.

### 16.8 Running the suite

Speed tiers, because a suite that is not fast enough to run constantly will not be run.

| Tier | Budget | Contents | When |
|---|---|---|---|
| `fast` | < 15 s | Unit, schema, contracts | On save |
| `medium` | < 3 min | Property tests at reduced example budgets, canaries, golden run | On commit |
| `slow` | ~1 h | Full synthetic ground truth, full hypothesis budgets, regression corpus, tier A↔B differential, benchmarks | Nightly |
| `integration` | variable | Testnet cassettes and live testnet | On demand, and mandatory before any promotion |

Tooling: `pytest` with markers for the tiers, `pytest-xdist` for parallelism, `hypothesis` for properties, `pytest-benchmark` for the performance guards, small frozen Parquet fixtures committed to the repository, and seeded generators for everything synthetic.

Line coverage is tracked as hygiene and is explicitly **not** a release criterion. The release criteria are: canary catch rate at 100%, the noise rejection test green, the regression corpus unchanged or explained, and the golden run bit-identical.

### 16.9 Observability

Grafana metrics: freshness and completeness per source · open quality issues · experiment throughput and latency · funnel hit rate (tier A → B survivors) · cost model calibration error · backtest↔simulator↔live divergence · lifecycle state of each strategy · risk limit utilisation against caps · canary catch rate and regression corpus status · compute cost per promoted strategy.

That last metric is the lab's most honest number: what it costs to produce a strategy that survives.

### 16.10 Backup and recovery

The registry is the asset. Losing the Parquet archive costs a re-download; losing Postgres costs every experiment ever run, every validation verdict and the trial counters that make the statistics valid — and those cannot be reconstructed.

- Postgres: daily base backup plus continuous WAL archiving to object storage, retained 90 days
- Parquet archive and artifacts: versioned bucket with lifecycle rules; immutability makes incremental backup trivial
- Secrets: backed up separately, never in the same store as the data
- **Restore drill quarterly**, into a scratch environment, verifying that a known experiment reproduces bit-identically from the restored registry. An untested backup is a belief, not a backup.

---

## 17. AI research agent — with limits

AI participates where it adds value, and never touches the verdict.

**It may**: cluster and classify news, extract structured facts, detect data anomalies, propose hypotheses, suggest features, draft interpretations of results, compare experiments and summarise patterns across them.

**It may not**: declare that a strategy works, adjust validation thresholds, select the winning configuration, access the frozen OOS, **or have any access whatsoever to TESTNET and LIVE modes**. The agent lives in the lab; it does not touch execution.

**Mandatory protocol**:
1. The hypothesis is recorded in `hypotheses` with `prior_belief` **before** it is run
2. It is translated into a `study` with a declared search space
3. It runs through the same funnel and the same gates as a human hypothesis
4. The outcome is written to `hypotheses.outcome`, win or lose

This makes the agent's **historical hit rate** queryable. If it generates 400 hypotheses and its confirmation rate does not beat random configuration generation, the agent is not adding signal — and that is a measurable fact rather than an impression.

---

## 18. Implementation phases

Revised once the lab existed and started producing results. What changed is not
the list of components but their order, and the reason is in §18.0.

### 18.0 What building it taught, and what that reorders

Phases 0 through 4 are largely done, and running them produced findings that
should govern what comes next rather than being filed as trivia:

- **The lab rejects what it is shown.** RSI thresholds on BTC and ETH, 1,008
  configurations over seven years: `inconclusive` on a single split, and −0.37
  under walk-forward with 40% parameter churn. That is the correct answer to the
  question asked, and it says nothing about the differentiation thesis, because
  the thesis was never tested — RSI over close prices is the most mined signal in
  retail trading and BTC is the most efficient asset in the market.
- **Funding costs 11.8% a year** on a long perpetual position, 78.7% cumulative
  since 2020. It was absent from the engine and from §0.1's table. Carry grows
  with the holding period where turnover shrinks, so beyond about two days carry
  dominates, and a long-biased strategy must clear both.
- **Effective observations run 30–120× below the bar count.** Every significance
  figure depends on getting that number right, and it is the difference between
  "established" and "cannot be established" on identical returns.
- **The trial budget sets the bar.** Over six years of daily returns a survivor
  needs an annualised Sharpe of 2.00 after 1,000 trials and 2.66 after a million.
  Testing more hypotheses raises what the winner must clear, which contradicts the
  original brief's "millions of combinations".
- **The liquidation feed does not deliver.** Four hours connected, thirteen
  reconnections, zero messages, on a connection where `@trade` floods and Bybit
  behaves identically. One pillar of the differentiation thesis may simply be
  unavailable without paying for it.

**The reordering that follows.** Phases 5 through 7 build the machinery to deploy
a strategy: executor, risk engine, reconciliation, capital ramp. That is weeks of
careful engineering in service of something that does not yet exist — no strategy
has passed. Building deployment machinery before the lab has found anything to
deploy is speculative work, and it is the kind that feels productive.

So a research campaign comes first. It is the only phase whose outcome can make
the others unnecessary.

### Phase 4b — The research campaign (4–8 weeks, research not engineering)

Use the lab on what justifies it. The components exist; this phase spends them.

**Universe.** 193 assets carry both OHLCV and funding, 416 carry OHLCV.
Mid-caps and recent listings rather than majors, per §0.4: a small operator's
structural advantage is capacity, and the majors are where that advantage does
not apply.

**Hypotheses, in descending order of how well the data supports them:**

1. Funding extremes and persistence (§11.4). Funding is not a function of price —
   it says who is crowded and what it costs them — and it is fully backfilled.
2. Open-interest divergence from price. Positions opening into a move resolve
   differently from positions closing out of one, and price alone cannot tell
   them apart. Limited to the OI captured from 2026-09 onward.
3. Cross-sectional selection across the universe (§6.3), validated wide and
   deployed narrow.
4. Listing events from the announcement collector, which is the one event class
   with a real sample.

**Discipline.** The trial budget is declared per hypothesis before searching, and
the minimum detectable effect with it. A disciplined search is worth more than a
large one, and that is now arithmetic rather than advice.

**Exit, and it has two honest branches:**

- Something clears §15's `validating → candidate` gate under walk-forward, with a
  plateau rather than a spike and a capacity ceiling above the intended capital.
  Then phases 5 onward are justified and specified by what was found.
- Nothing clears it. Then the finding is that this data and this cost structure do
  not support a tradeable edge at this horizon — which is worth knowing, cost
  months rather than capital, and is the outcome the whole apparatus was built to
  be able to reach.

### Phase 4c — Tier B, gated on 4b (3–5 weeks)

Only for a strategy that survived 4b. nautilus_trader for the event-driven engine,
so backtest, simulator and live share one code path and the main cause of
backtest-to-live divergence disappears by construction (§12.1).

Also here: the realistic cost model against recorded book depth, tier A↔B
calibration (§9.2), and the capacity analysis that sets the capital ceiling
(§12.7).

**Exit**: a strategy traverses the full funnel and produces a validation report
that survives being read adversarially, including under pessimistic fills.

### Phase 5 — Simulator (4–6 weeks, gated on 4c)

TimescaleDB hot window, live ingestion, simulated execution with configurable
fake capital, the risk-limit engine active in every mode, daily reconciliation
with divergence attribution (§12.5), and the Grafana panel.

**Exit**: 60 days continuous, divergence within band and decomposed by cause,
risk limits fired at least once in a forced test.

### Phase 6 — Testnet and hardening (3–4 weeks)

The venue adapter against the sandbox, idempotent `client_order_id`, state
reconciliation, kill switch, dead man's switch, credential separation, and the
failure drills of §16.7.

**Exit**: two weeks with no rejections caused by our own bugs; every drill
executed with documented behaviour; kill switch proven with an open position.

### Phase 7 — Live with capital ramp (ongoing)

Segregated production keys, tiers L0–L3 per §14.5 with the minimum-viable-size
floor, monitoring, and the alpha decay test running continuously.

**Exit**: tier L0 complete — two weeks at minimum capital, zero integration
incidents, slippage within model, decay test green against pre-registered
thresholds.

### Phase 8 — Portfolio (3–4 weeks, gated on having 3+ strategies)

Return-correlation clustering, HRP allocation, portfolio-level trial counting and
joint capacity. Gated on strategies existing rather than on calendar time: below
three survivors there is no portfolio problem, and building the layer early is
speculative.

### Phase 9 — Event intelligence (parallel from now)

Split by urgency, because one part is already losing history:

- **Now**: keep capturing announcements and the macro calendar. Neither is
  backfillable beyond what the CMS endpoint paginates.
- **With 4b**: listing events are the one class with hundreds of occurrences, so
  they enter the research campaign directly rather than waiting.
- **After 4b**: local projections, two-component decay fitting, hierarchical
  pooling, and the §11.11 gates.

**Exit**: for at least one event class, `τ_slow` has a confidence interval
excluding zero, pre-event flatness passes, the impulse response beats the placebo
null, and the cost filter passes inside the §0.2 band. If no class clears that,
the honest conclusion is that news impact decays faster than this cost structure
can monetise — a finding, and a cheap one.

### Open decisions

**Liquidations.** Measured silent across two venues over four hours on healthy
connections. If it stays silent, the choice is a proxy inferred from sharp
open-interest drops coinciding with adverse price moves — labelled as derived
throughout, because it is not the same measurement — or a paid vendor. The proxy
costs nothing and the OI it needs is already accumulating.

**Base image digests.** The last open item from phase 0. `image_digest` is
recorded on every experiment and currently reads `unpinned`, which makes the
reproducibility contract of §16.1 nominal rather than real.

**Venue concentration.** Data, execution and capital all sit with one exchange.
Worth a second venue's collectors at least, so the data side survives an account
restriction.

---

## 19. Non-goals and risks

**Out of scope**: low-latency / HFT execution, market making, multi-tenancy, mobile app, managing third-party capital. Also out of scope as *research targets*: sub-hour holding periods (cost drag, §0.1) and holding periods beyond three weeks (unverifiable with available history, and largely BTC beta).

Real-money trading **is** a goal, but it is the end of the ladder in §12, not a starting point. No phase before 7 involves it.

**Principal risks**:

| Risk | Mitigation |
|---|---|
| OOS degradation through repeated use | `oos_access_log` plus a second OOS held untouched until the final decision |
| **Re-optimising on simulator results** | The §12.4 boundary enforced by the system: changing signal params creates a new strategy that re-enters the funnel |
| Portfolio of correlated strategies presenting as diversified | Correlation measured on returns and per regime; clusters treated as one bet; joint capacity ceiling |
| Retiring a working strategy inside a normal drawdown | Pre-registered sequential test (§14.6); Monte Carlo drawdown distribution is the reference, not intuition |
| Searching for an edge too small to detect | Minimum detectable effect fixed on the study before the search (§8.8) |
| Horizon drift — a strategy quietly becoming intraday as parameters are tuned | Holding period is a declared constraint on the study; a configuration whose realised average hold falls outside §0.2 is rejected before evaluation, not after |
| Expecting income before evidence exists | §0.3 states the realistic timeline and return band up front; the capital ramp (§14.5) makes early size impossible by construction |
| Maker strategies inflated by ignored adverse selection | Modelled explicitly in the cost model; promotion requires pessimistic-fill viability (§9.5) |
| Losing the registry and with it every trial counter | WAL archiving plus quarterly restore drill verifying bit-identical reproduction (§16.10) |
| Caving to "nothing clears the filters" | Versioned thresholds with a mandatory rationale, plus a 24h cooling-off before an approval may rely on a relaxed one (§20.3) |
| Jumping from simulator straight to live | Testnet rung mandatory in the state machine; no transition bypasses it |
| State desynchronised from the exchange | Periodic and startup reconciliation; discrepancy → halt, never self-correction |
| A capital tier too small to open the intended positions | L0 floored at `positions_target x min_viable`; if that exceeds the target, the strategy is declared undeployable before the ramp rather than after it fails |
| Losing money to a bug rather than to the market | Hard limits in an independent component, active from mode 1; capital ramp |
| Compute cost explosion | Funnel + `config_hash` cache + per-study budget |
| Silently bad data quality | Ingestion-time validation + cross-source reconciliation |
| Scope: 18 sources at once | Data tiers; no source enters without a quality test and backfill |
| Venue concentration: one exchange holds the data, the execution and the capital | Account restrictions and term changes arrive without notice. Keep only the required margin on the venue between trades, keep a second venue's collectors running so the data side survives, and treat single-venue dependency as a stated exposure rather than an assumption |
| Complexity nobody maintains | Hard boundaries between layers; every phase ships something usable |

---

## 20. Operator interface

The pieces exist across §2.1, §5.6 and §12.6 but were never assembled into screens. This section does that, and settles one boundary and one prohibition that matter more than the screen list.

### 20.1 The Grafana / application boundary

Two surfaces, and putting something on the wrong one costs either weeks of chart code or an unauditable decision:

| | Grafana | Application |
|---|---|---|
| Answers | "How is it going?" | "What is this, and what do I decide?" |
| Data | Time series | Relational registry |
| Writes | Nothing | Approvals and lifecycle transitions only |
| Identity | Not required | **Required** |

The rule: **if it reads a time series, it is Grafana; if it writes to the registry, it is the application.** Equity curves, drawdown, slippage per trade, latency, ingestion freshness and risk-limit utilisation are all Grafana, and cost no code. Anything that records a decision is the application, because a decision needs an actor and a timestamp.

The application **never computes a quantitative figure**. Every number it displays was computed by a worker and persisted, exactly as required of the Laravel variant in Appendix B — the constraint is about provenance, not about which framework draws the page.

### 20.2 Mode is not a toggle

The single most important thing in this section.

There must be **no control that switches a strategy from simulator to production.** Not a button, not a dropdown, not a config field an operator can edit. The reasons are structural rather than cautious:

- §15 makes every transition conditional on gate criteria plus a recorded approval. A toggle bypasses both by construction.
- §14.5 requires the capital ramp; production is reached through L0–L3, not entered.
- §14.4 puts LIVE credentials out of reach of everything but the executor. The `tradelab-api` image does not have them, so **the interface is incapable of starting live trading** even if its code asked to.

What the interface actually does is *request* a transition. The request lands in `approvals`; the gate evaluates `gate_configs` against `validation_reports`; the executor acts only on an approved transition whose criteria passed. The screen shows which criteria are met and which are not, and the button is disabled with the failing criterion named — not hidden, because knowing why you cannot promote is the useful part.

The mode of a running strategy is therefore **displayed, never edited**, and it is displayed prominently: mistaking simulator for live is the error this design is most concerned with.

### 20.3 A single operator, and the control that replaces separation of duties

The system has **one user, with the `admin` role**. No user management, no invitations, no permission matrix: a single credential from configuration, and `users` holds one row so that `approvals.approved_by` and `audit_log.actor` have something real to point at.

Authentication stays, but its purpose changes. With one identity it is not establishing *who*; it is a deliberate act before a decision can be recorded, and it keeps the surface closed if the port ever stops being local. Sessions are short-lived and `audit_log` records actor, action and subject for every write.

What matters is what is *lost* by collapsing the roles, because it is not nothing.

With separate `operator` and `admin` roles, one person lowering a gate threshold and a different person approving under it was a separation of duties. With one admin, **nothing prevents lowering the bar and approving three minutes later** — and that loop is the characteristic failure of a solo quant, the one §15 calls the most common way a laboratory stops being one. A `rationale` text box does not stop it: you will write a sincere-sounding reason, because at that moment you will believe it.

So the separation becomes **temporal rather than social**:

**Cooling-off on gate changes.** An approval may not cite a `gate_config` whose `valid_from` is less than **24 hours** old. Lowering a threshold is permitted; acting on the lowered threshold the same day is not. The constraint is enforced in the approval path, not in the interface, so it cannot be clicked past.

```sql
-- rejected: the gate this approval relies on was relaxed too recently
CHECK (now() - (SELECT valid_from FROM gate_configs g
                WHERE g.id = gate_config_id) >= interval '24 hours')
```

Three supporting properties, all already in the schema:

- `gate_configs.rationale` is `NOT NULL`, and every `validation_report` records the `gate_config_id` it was judged under — so a verdict always carries which bar it cleared
- `lifecycle_transitions.evidence` snapshots the criteria as evaluated, so a later reading shows what was true at the moment, not what is true now
- `audit_log` preserves the sequence, which is what makes "threshold lowered 14:02, approval 14:05" legible six months later

The point of an approval step with one operator is not that someone else checks the work. It is that **the decision is separated in time from the result that motivated it**, and that the separation is recorded. Twenty-four hours is short enough not to obstruct real work and long enough to outlast the feeling that produced the change.

### 20.4 Screens

Ordered by the phase that needs them, so none is built before there is something to put in it.

| Screen | Shows | Writes | Phase |
|---|---|---|---|
| **Sign in** | — | session, `audit_log` | 4 |
| **Audit** | Sequence of threshold changes, approvals and transitions, with elapsed time between them | — | 4 |
| **Home** | Running runs by mode, open alerts, capital tier, blocking data-quality issues | — | 4 |
| **Universe** | Configured assets, point-in-time `tick_size`/`lot_size`/`min_notional`, computed `min_viable` (§14.1), listing dates | deployed asset set | 4 |
| **Strategies** | Lifecycle state, gate status per criterion, cluster membership (§13.1) | transition requests | 4 |
| **Strategy detail** | Params (read-only), validation report, capacity ceiling, decay test | — | 4 |
| **Experiments** | Browse and filter by feature, parameter, regime; side-by-side comparison | — | 4 |
| **Approvals** | Pending transitions with the evidence snapshot that will be recorded | `approvals`, `lifecycle_transitions` | 4 |
| **Risk limits** | Current `risk_limit_configs`, live utilisation against each cap, breach history | limit changes (admin) | 5 |
| **Runs** | Active positions, orders, fills, realised and unrealised P&L, per-trade slippage | — | 5 |
| **Reconciliation** | Divergence vs backtest with its attribution into costs / timing / fills / data (§12.5) | — | 5 |
| **Data quality** | Open issues, which windows are blocked and what that blocks | issue resolution | 2 |
| **Capital ramp** | Current tier, days at tier, criteria for advancing | tier approvals (admin) | 7 |
| **News** | Events, extracted fields, ratings with their calibration, process state | `event_annotations`, `process_transitions` (§20.5) | 9 |
| ~~**Macro**~~ | *Retired 2026-09-28: no strategy used it (§20.6).* | — | — |

Parameter configuration deserves its own note, because it is where the interface is most likely to do damage: **signal parameters are not editable anywhere.** §12.4 makes changing one create a new strategy that re-enters the funnel, and `runs.params` is immutable in the schema. The interface exposes the operational settings of §12.4's left-hand column — sizing, risk limits, order type, alert thresholds — and displays the rest read-only.

### 20.5 The news screen, and the one rule that makes it safe

The operator needs to work on events directly: the LLM extraction will
misclassify, the taxonomy will miss things, and some events carry judgement no
classifier can supply. So the screen exists, and it allows adding an event,
correcting its extraction, rating it, and setting its state.

It collides with two principles, and the resolution is what makes it usable
rather than dangerous.

**It appears to break §20.1** — the application computes no quantitative figure —
because a human rating is a number entering through the interface. It does not,
because the rating is not a computed metric; it is **an observation, recorded
with the time it was made**. The application still computes nothing. It records
what someone asserted and when.

**It genuinely threatens §11.2**, and this is the one that matters. Editing an
event's rating today, in place, and letting a 2022 backtest read it is hindsight
injected straight into historical labels — the purest form of look-ahead bias
this system can suffer, because a human who knows how 2022 turned out is rating
2022 events.

#### A rating is a revision, never an edit

Nothing on this screen overwrites anything. Every annotation is a new row with
its own `available_at`, exactly as `event_revisions` already works for extracted
content (§11.2). A backtest at time `t` sees an annotation only if it was made
before `t`, so today's corrections are invisible to a backtest of last year —
which is the correct behaviour and will occasionally be frustrating.

#### Corrections and judgements are not the same thing

The distinction decides the timestamp, and getting it wrong is how hindsight
enters while looking like housekeeping.

| | Correction | Judgement |
|---|---|---|
| Example | "The ticker is PONSUSDT, not the release date the parser captured" | "I read this as strongly bullish for the sector" |
| Was the value knowable at publication? | Yes — the parser simply failed | No — it did not exist until you formed it |
| `available_at` | The original event's | **Now** |
| May be backdated | Yes, and should be | **Never** |

A correction repairs a mechanical failure to read something that was always
there. A judgement creates information at the moment it is made. The interface
requires choosing which one is being entered, does not offer a date field for
judgements, and the backdating path is closed in the approval layer rather than
in the form — a control in a form is a control that can be clicked past.

#### The operator's ratings are scored like the agent's

§17 requires the AI agent's hypotheses to carry a recorded prior and be tallied
against outcomes, so its hit rate is a measurable fact rather than an impression.
A human annotator entering ratings is an input to the same models and gets the
same treatment, for the same reason.

If ratings over two hundred events show no relationship to subsequent reaction
residuals, that is worth knowing, and it is only knowable if each rating was
recorded with a timestamp before the outcome existed. The screen therefore shows
the operator their own calibration next to the entry form — not to discourage
annotation, but because an unmeasured input with a confident human behind it is
the least examined thing in any research system.

#### State

For a `process` event (§11.7), state is the state machine: `proposed`,
`under_review`, `decided`, `implemented`, `withdrawn`. Setting it manually is
legitimate, because often no feed announces a transition — a consultation quietly
closing is not a press release.

A state change is a `process_transitions` row, and `supersedes` is set, so a
position built on the prior state goes to zero at the transition rather than
decaying from it (§11.8).

### 20.6 The macro screen, and where the boundary falls

> **Retired 2026-09-28.** The screen, its routes and its daily refresh were removed because no
> strategy read them; the `macro_series` and `macro_releases` tables remain. The design below
> is kept for the day a strategy needs it.

Macro is a clean illustration of §20.1's rule, because it lands on both sides of
it.

**Grafana** takes the prices: gold, oil, DXY, yields, indices. They are time
series, they are read and not decided upon, and they cost no code.

**The application** takes the calendar: which releases are scheduled, what the
consensus is, what was actually printed, and the surprise between them. That is
relational, it has state, and part of it is entered by a person.

What the screen shows:

- **Upcoming releases** with their consensus and days remaining — which is the
  anticipation window of §11.6 made visible, since the tradeable object for a
  scheduled event is the approach rather than the arrival.
- **Past releases** with consensus, actual and surprise in sigma, so a macro
  effect can be read on the same scale as everything else.
- **Which series carry vintages**, from `macro_series.has_vintages`. Operational
  rather than decorative: a revised series used as if it were real-time is
  answering a different question (§4.7), and results depending on one are
  flagged rather than quietly reported.
- **Freshness per series**, because a stale macro feed fails silently — the
  number is simply old, and old numbers look exactly like current ones.

What the operator may enter: **consensus, where no feed supplies it.** And it
carries the same constraint as §20.5's judgements, for the same reason.

> **A consensus recorded after the release is not a consensus. It is a memory of
> one.**

The whole value of macro as an event class is that expectations existed *before*
the print, so surprise is a number rather than a story told afterwards. A
consensus entered once the actual is known is that story, and it would make every
macro surprise look predictive. The check lives in the schema —
`consensus_at < scheduled_for` — rather than in the form, which is now the third
place this system refuses to trust a control that can be clicked past.

### 20.7 What the interface must not be

- **Not the kill switch's only path.** §14.1 requires it to work when the orchestrator is down; a browser is a weaker dependency still. The UI has a kill button, and a CLI kill exists that does not go through the API or the orchestrator.
- **Not a place metrics are computed.** A figure that appears only on screen is a figure nobody can reproduce.
- **Not a live-trading control surface.** It approves; the executor acts.
- **Not required for the system to run.** Collectors, backfill, validation and execution all run headless. The interface is for deciding and for looking, and if it is down, nothing stops.

---

## Appendix A — Original brief

Preserved verbatim (translated) as the source of requirements. §1 maps each gap found here to its fix.

> I want to build a professional quantitative research and paper trading platform for cryptocurrencies.
>
> Not simply an indicator dashboard or a fixed-rule bot. I want an **algorithmic laboratory** capable of accumulating data, generating hypotheses, testing strategies en masse, detecting overfitting, running walk-forward testing and ultimately executing paper trading in real time.
>
> **Objective** — build a system with this cycle:
> DATA → FEATURES → HYPOTHESIS → EXPERIMENTS → BACKTEST → VALIDATION → PAPER TRADING → EVALUATION → NEW HYPOTHESES
>
> The system must be designed from the start to experiment with thousands or millions of parameter combinations and to retain every experiment so it can be reproduced.
>
> **Data** — architecture prepared to store a very large historical base: trades/ticks, OHLCV, volume, order book, bid/ask imbalance, CVD/order flow, open interest, funding rates, liquidations, futures basis, options/IV/skew when available, on-chain data, BTC dominance, cross-asset correlations, traditional indices and markets, DXY, yields, Nasdaq, S&P, VIX, macroeconomic events, news. PostgreSQL + TimescaleDB as the main store for structured and time-series data; Parquet + object storage for massive raw datasets. The architecture must allow new sources without redesigning the system.
>
> **News Intelligence** — an advanced news system. Do not treat each article as an independent signal. Detect: entities, event, event type, affected assets, expected impact, surprise, source credibility, novelty, time horizon, event evolution. Multiple articles about the same fact must group into a single **Event ID**. If new information about the same event appears later, update the event rather than create a new signal. Use embeddings/semantic similarity + entities + temporality + structured fact extraction. AI may be used here for classification, extraction and semantic analysis.
>
> **Feature Engine** — an extensible feature system. It must allow the same indicator to be used repeatedly with different parameters (RSI 7, 9, 14, 21, 28…; EMA 10/20, 20/50, 34/89…; ATR 7, 14, 21…) and combine them with momentum, trend, volatility, volume, market structure, VWAP, volume profile, order flow, open interest, funding, liquidations, basis, options, macro, on-chain, news and cross-asset relationships. Do not assume in advance which indicators are useful. The goal is to discover that experimentally.
>
> **Experiment Engine** — an engine that generates and runs experiments automatically. Each experiment must have: experiment_id, strategy_id, dataset version, timeframe, assets, features used, parameters, rules, training period, validation period, out-of-sample period, results, timestamp, code/model version. It must be fully reproducible. Allow grid search, random search and more advanced optimisation later. Avoid absurd combinations via configurable constraints.
>
> **Backtesting** — a realistic backtesting engine accounting for fees, spread, slippage, liquidity, latency, partial fills, funding, position size, stop loss, take profit, position sizing and exchange restrictions. Never accidentally use future information. Avoid look-ahead bias and survivorship bias.
>
> **Anti-overfitting** — this is a priority. Do not select strategies solely by highest historical return. Implement: train/validation/test, out-of-sample, walk-forward testing, parameter stability, sensitivity analysis, Monte Carlo, bootstrap where appropriate, market-regime analysis, minimum trade count, complexity penalty, detection of strategies overly dependent on a specific period. Also evaluate the shape of the parameter surface — RSI 14 excellent with 13/15 disastrous is suspicious; RSI 12–18 with similar results is more robust.
>
> **Market Regime Engine** — identify market states: bullish/bearish, trending/ranging, high/low volatility, risk-on/risk-off, high/low liquidity, expanding/contracting volatility. Strategies must be evaluable separately within each regime.
>
> **Causal/Event Reaction Engine** — relate events to market reaction: news event → expected BTC reaction → actual BTC reaction → ETH reaction → altcoin reaction → volume reaction → OI reaction → funding reaction. Measure especially **underreaction / overreaction**. Positive news that should produce +2% but produces only +0.2% may differ from moderately positive news that produces +4%. Create features to measure this reaction.
>
> **AI Research Agent** — incorporate AI only where it provides a clear advantage. The agent may analyse news, group events, detect anomalies, analyse results, discover relationships, generate hypotheses, propose new experiments, explain why a strategy might work, and compare results across experiments. But the AI **must not declare that a strategy works**. Every AI-generated hypothesis must pass through the Experiment Engine and Backtest Engine. Quantitative results must be independent of the model's opinion. Record which hypotheses the AI generated and what result they obtained.
>
> **Paper Trading** — strategies that clear validation must be able to move to paper trading. Simulate real-time execution including orders, fills, fees, slippage, funding, P&L, drawdown, exposure, position and equity curve. Then compare BACKTEST vs PAPER TRADING and detect divergences.
>
> **Experiment Registry** — everything must be stored. I want to be able to answer questions like: "Which strategies used RSI + Open Interest + Funding?" "Which parameters worked best for BTC 4H?" "Which strategies were robust out-of-sample?" "Which AI-generated hypotheses turned out to be statistically useful?" "Which features have the most predictive power?" "Which strategies only work in certain regimes?"
>
> **Architecture** — before writing code: analyse the requirements; propose the complete architecture; define the services/components; define the PostgreSQL/TimescaleDB schema; define the main tables and relationships; define the data pipeline; define the interfaces between components; define a strategy for scaling backtests; define how to version datasets, features, strategies and experiments; propose a phased MVP. Do not build everything at once. Present the architecture and explain the technical decisions first, then implement the MVP incrementally.
>
> **Principles** — modularity, reproducibility, scalability, observability, scientific experimentation, avoid data leakage, avoid overfitting, separation between research and execution, separation between AI and quantitative results, every experiment must be reproducible.
>
> Prioritise a solid research/backtesting architecture first. Real trading is NOT the initial objective.
>
> The first objective is a system able to answer: **"Do we have sufficient statistical evidence that this strategy works out-of-sample and under different market conditions?"**
>
> When choosing between a quick implementation and an architecture that enables rigorous research and future evolution, prioritise the latter.

*Note: the brief's final constraint — real trading not being an initial objective — was superseded by a later decision. Real-money trading is now the destination; see §12 and §19.*

---

## Appendix B — Laravel variant

If the decision to use Laravel is imposed externally, it is workable under a strict boundary:

- **It enters at phase 4**, not earlier. Phases 0–3 are pure Python.
- **Laravel + Filament as an operations and read portal**: browse the registry, compare experiments, approve promotions, monitor simulator and production, manage users and permissions, record capital-tier approvals.
- **It does not own the schema.** Migrations belong to Alembic; Laravel maps existing tables read-only except for its own tables (users, approvals, comments).
- **It computes nothing quantitative.** Not one metric, not one Sharpe, not one aggregation of results. Every figure on screen was computed by Python and is persisted.
- **It does not invoke Python code and does not touch execution.** Communication happens via queue (Redis) and via tables. The kill switch has its own direct path, independent of both Laravel and the orchestrator.

Under that boundary Laravel stops owning domain and becomes presentation, which is where its productivity is real and its divergence cost is close to zero.
