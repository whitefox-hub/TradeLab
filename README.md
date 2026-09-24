# TradeLab

Quantitative research, simulation and trading platform for crypto derivatives.

The architecture, the statistical contract and the phase plan live in **[Plan.md](Plan.md)**.
This README covers only how to run what exists.

**Current status: phase 1 — Tier 1 data ingestion.**

---

## What this is for

One question, answered honestly:

> Do we have sufficient evidence that this strategy works out-of-sample, across
> market regimes, **after discounting the number of hypotheses we have tested**?

Everything here is built so that answering "yes" is hard and answering "no" is cheap.

## Quick start

```bash
make install          # venv + all dependency groups
make up               # research stack. Does NOT start the executor.
make migrate          # apply the registry schema
make test             # the commit-time suite
```

`http://localhost:8000` API · `http://localhost:3000` Grafana · `http://localhost:9001` MinIO

## Execution modes

Four rungs (Plan.md §12). The executor is behind a compose profile, so a bare
`docker compose up` **cannot** reach a venue:

```bash
docker compose --profile simulator up    # live data, simulated fills, fake capital
docker compose --profile testnet   up    # real orders, exchange matching engine, fake money
docker compose --profile live      up    # real money. See §13.5 capital ramp first.
```

No exchange account is needed for phases 0–4. See §4 for what requires one and,
more importantly, for the data that **cannot be backfilled** and must be captured now.

## Reproducibility

An experiment records four hashes: dataset content, git sha, container digest and
config. Re-running it must produce bit-identical metrics.

That only holds if thread counts are pinned — float reduction order depends on
them, so the same code gives different numbers on a 4-core and a 16-core machine:

```bash
make doctor           # verify this environment can produce recordable results
```

If `doctor` fails, results produced here must not be recorded.

## Testing

The bugs that matter here do not crash; they return a *better* Sharpe ratio. The
suite is built accordingly (Plan.md §16.2):

| Command | Contents | Budget |
|---|---|---|
| `make test-fast` | unit, schema, contracts | < 15 s |
| `make test-medium` | properties, canaries, golden run | < 3 min |
| `make test-slow` | synthetic ground truth, regression corpus, benchmarks | nightly |

**Release criteria are not coverage**: canary catch rate at 100%, noise-rejection
test green, regression corpus unchanged or explained, golden run bit-identical.

If the golden run fails, do not update it to make it pass. `make golden-update`
refuses to run without a written reason, on purpose.

## Layout

```
tradelab/
  collectors/   one module per source, all implementing Collector
  storage/      Parquet layout, manifests, content hashing
  features/     registry + definitions, one file per family
  labeling/     triple barrier, meta-labeling, sample weights
  engines/      tier_a (vectorized screening), tier_b (nautilus)
  validation/   CPCV, DSR, PBO, SPA, baselines, power
  portfolio/    correlation, allocation, aggregate capacity
  execution/    executor, venue adapters, reconciliation
  risk/         limits, kill switch, degradation tests
  registry/     SQLAlchemy models, Alembic migrations
  orchestration/ Dagster assets and jobs
  api/          FastAPI
research/       marimo notebooks — depends on everything, nothing depends on it
```

## Ingesting data

No exchange account is required (Plan.md §4.7):

```bash
tradelab universe --limit 20                                  # point-in-time universe
tradelab backfill BTCUSDT --start 2024-03-01 --end 2024-03-07 # bulk klines, free
```

**Start the capture collectors before you need them.** Liquidations have no
historical REST endpoint at all and open interest reaches back about 30 days, so
that history only exists if something was recording. Every week of delay is data
that never comes back.

## Phase 0 exit criteria

- [x] `docker compose up` starts the stack and does **not** start the executor
- [x] migrations apply cleanly (6 tables + pgvector)
- [x] `pytest -m fast` under 15 seconds (currently 2.6s)
- [x] golden run reproduces bit-identically; thread-invariance verified at 1, 2 and 8 threads
- [ ] base images pinned by `sha256` digest (`make pin-digests`)

## Phase 1 progress

- [x] Collector contract, three-timestamp schemas, ingestion validation
- [x] Bulk kline backfill from data.binance.vision, validated against real data
- [x] Point-in-time universe (702 perpetuals, listing dates, exchange constraints)
- [x] Immutable Parquet archive with revisions and capture parts
- [x] DuckDB query layer with a point-in-time filter, and derived bar aggregation
- [x] Dataset versioning by content hash, idempotent on rebuild
- [x] Open interest capture, verified against the live REST endpoint
- [ ] **Liquidation websocket capture — code written, socket path never exercised.**
      The development sandbox passes HTTPS but drops websocket frames, confirmed
      against a control stream. Run `pytest -m integration` on the target machine
      before trusting it.
- [ ] Trades, funding and exchange-announcement collectors
- [ ] 3+ years backfilled across the target universe
