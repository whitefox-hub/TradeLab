"""Registry ORM. Plan.md §5.

Phase 0-1 subset: the tables needed to ingest data reproducibly. Later phases add
their own tables per §17; nothing here is speculative.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


TS = DateTime(timezone=True)  # every timestamp is timestamptz in UTC (§5)


class Venue(Base):
    __tablename__ = "venues"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # cex | dex
    supports_testnet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    clock_offset_ms: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("kind IN ('cex','dex')", name="ck_venues_kind"),)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    venue: Mapped[str | None] = mapped_column(ForeignKey("venues.code"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # §4.1
    sla_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    # Point-in-time: a 2021 article cannot be scored with today's reputation (§11.2)
    credibility: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    valid_from: Mapped[datetime] = mapped_column(TS, nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (CheckConstraint("tier BETWEEN 1 AND 3", name="ck_sources_tier"),)


class AssetUniverse(Base):
    """Point-in-time universe. Blocking requirement: kills survivorship bias (§4.4)."""

    __tablename__ = "asset_universe"

    asset: Mapped[str] = mapped_column(String(32), primary_key=True)
    venue: Mapped[str] = mapped_column(ForeignKey("venues.code"), primary_key=True)
    valid_from: Mapped[datetime] = mapped_column(TS, primary_key=True)
    listed_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    delisted_at: Mapped[datetime | None] = mapped_column(TS)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    lot_size: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(TS)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # backfill|stream|recapture
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime | None] = mapped_column(TS)
    watermark: Mapped[datetime | None] = mapped_column(TS)
    rows_written: Mapped[int | None] = mapped_column(BigInteger)
    output_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("mode IN ('backfill','stream','recapture')", name="ck_ingestion_mode"),
        Index("ix_ingestion_runs_source", "source_id", "started_at"),
    )


class DataQualityIssue(Base):
    """Anomalies are recorded, never silently corrected (§4.2, §4.6)."""

    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    asset: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime] = mapped_column(TS, nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint("severity IN ('info','warn','blocking')", name="ck_dqi_severity"),
        CheckConstraint(
            "kind IN ('gap','duplicate','out_of_range','cross_inconsistent','backfill_mismatch')",
            name="ck_dqi_kind",
        ),
        CheckConstraint("period_end >= period_start", name="ck_dqi_period"),
        # Blocking issues gate promotion (§4.6), so they are queried constantly.
        Index(
            "ix_dqi_open_blocking", "source_id", "period_start",
            postgresql_where=text("resolved_at IS NULL AND severity = 'blocking'"),
        ),
    )


class DatasetVersion(Base):
    """Exactly which data existed. `content_hash` is the dataset's identity (§5.1)."""

    __tablename__ = "dataset_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manifest_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    period_start: Mapped[datetime] = mapped_column(TS, nullable=False)
    period_end: Mapped[datetime] = mapped_column(TS, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("content_hash", name="uq_dataset_content_hash"),)


# --------------------------------------------------------------------------- #
# Research registry. Plan.md §5.1, migration 0002.
# --------------------------------------------------------------------------- #

class User(Base):
    """One row. Exists so approvals and the audit log have a real actor to
    reference, not to manage access (§20.3)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, server_default="admin")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())


class CostModel(Base):
    """Versioned cost assumptions. Results are never comparable across models."""

    __tablename__ = "cost_models"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    venue: Mapped[str] = mapped_column(ForeignKey("venues.code"), nullable=False)
    fill_model: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("fill_model IN ('optimistic','realistic','pessimistic')",
                        name="ck_cost_fill_model"),
    )


class FeatureDefinition(Base):
    __tablename__ = "feature_definitions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False)
    impl_ref: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    param_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Drives warmup and, more importantly, the purge size in §8.2.
    lookback_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    is_point_in_time: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (UniqueConstraint("name", "code_hash", name="uq_feature_name_hash"),)


class LabelDefinition(Base):
    __tablename__ = "label_definitions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    horizon_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    impl_ref: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    param_space: Mapped[dict] = mapped_column(JSONB, nullable=False)
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")

    __table_args__ = (
        UniqueConstraint("name", "code_hash", name="uq_strategy_name_hash"),
        CheckConstraint(
            "status IN ('draft','screening','validating','candidate','simulator',"
            "'testnet','live_ramp','live','halted','rejected','retired')",
            name="ck_strategy_status"),
    )


class GateConfig(Base):
    """Promotion thresholds. `rationale` is NOT NULL: lowering a bar always
    carries its reason, attached to every verdict issued under it (§20.3)."""

    __tablename__ = "gate_configs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    transition: Mapped[str] = mapped_column(String(48), nullable=False)
    thresholds: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    valid_from: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(TS)


class Study(Base):
    """A search campaign. `executed_trials` is the counter that feeds the
    Deflated Sharpe: monotonic, never reset (§8.3)."""

    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    search_method: Mapped[str] = mapped_column(String(16), nullable=False)
    planned_trials: Mapped[int] = mapped_column(Integer, nullable=False)
    executed_trials: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    min_detectable_sharpe: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    hold_days_min: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    hold_days_max: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    compute_budget_hours: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    compute_used_hours: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("executed_trials >= 0", name="ck_study_trials_nonneg"),
        CheckConstraint("hold_days_min > 0 AND hold_days_max >= hold_days_min",
                        name="ck_study_hold_band"),
    )


class Experiment(Base):
    """Tier 1 results live in Parquet; only candidates earn referential
    integrity, which the tier check enforces (§5.7)."""

    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    study_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("studies.id"), nullable=False)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_versions.id"), nullable=False)
    label_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("label_definitions.id"))
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feature_set: Mapped[dict] = mapped_column(JSONB, nullable=False)
    universe: Mapped[dict] = mapped_column(JSONB, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    cv_scheme: Mapped[dict] = mapped_column(JSONB, nullable=False)
    splits: Mapped[dict] = mapped_column(JSONB, nullable=False)
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    cost_model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cost_models.id"), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    code_version: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    realised_hold_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("tier >= 2", name="ck_experiment_tier_min"),
        Index("ix_experiments_study", "study_id"),
    )


class ExperimentResult(Base):
    __tablename__ = "experiment_results"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id"), primary_key=True)
    split: Mapped[str] = mapped_column(String(16), primary_key=True)
    regime: Mapped[str] = mapped_column(String(32), primary_key=True, server_default="all")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    n_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    returns_uri: Mapped[str | None] = mapped_column(Text)
    artifacts_uri: Mapped[str | None] = mapped_column(Text)


class ValidationReport(Base):
    """The statistical verdict, kept separate from the raw metrics."""

    __tablename__ = "validation_reports"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id"), primary_key=True)
    # From studies.executed_trials, including tier A discards.
    trials_considered: Mapped[int] = mapped_column(Integer, nullable=False)
    deflated_sharpe: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    dsr_pvalue: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    pbo: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    spa_pvalue: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    param_stability: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    min_trl_days: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    regime_coverage: Mapped[dict | None] = mapped_column(JSONB)
    baselines: Mapped[dict | None] = mapped_column(JSONB)
    cost_sensitivity: Mapped[dict | None] = mapped_column(JSONB)
    capacity_ceiling: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    gate_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("gate_configs.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("verdict IN ('pass','fail','inconclusive')", name="ck_verdict"),
    )


class LifecycleTransition(Base):
    """No transition exists outside this table (§15)."""

    __tablename__ = "lifecycle_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    gate_config_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gate_configs.id"))
    # Snapshot of the criteria as evaluated, so a later reading shows what was
    # true at the moment rather than what is true now (§20.3).
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TS, nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_transitions_strategy", "strategy_id", "occurred_at"),)


# --------------------------------------------------------------------------
# Execution. Plan.md §12, §14. Added for phases 5-8, which were started ahead
# of the validation gate that normally precedes them.
# --------------------------------------------------------------------------


class RiskLimitConfig(Base):
    """Versioned limit sets, referenced rather than embedded (§14.1).

    Embedding the numbers in `runs` would make a limit change invisible: two
    runs would show the same config and behave differently. A reference plus
    `valid_from` makes the change a row someone can point at.
    """

    __tablename__ = "risk_limit_configs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_positions_per_cluster: Mapped[int] = mapped_column(Integer, nullable=False)
    max_open_orders: Mapped[int] = mapped_column(Integer, nullable=False)
    min_position_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    max_position_notional: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    min_position_pct_equity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_position_pct_equity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    min_seconds_between_entries: Mapped[int] = mapped_column(Integer, nullable=False)
    max_gross_exposure: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_net_exposure: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_leverage: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_daily_loss: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_drawdown_from_peak: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    max_orders_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    max_consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False)
    max_price_deviation_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(TS, nullable=False)


class Run(Base):
    """One execution in any mode (§12).

    `risk_limit_config_id` is NOT NULL in every mode on purpose. A run that
    could exist without limits would be a run whose limiter is first exercised
    in production.
    """

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id"), nullable=False)
    promoted_from: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiments.id"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    venue: Mapped[str] = mapped_column(ForeignKey("venues.code"), nullable=False)
    #: Immutable. Changing a parameter creates a new run, never an edit (§12.4).
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    risk_limit_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_limit_configs.id"), nullable=False)
    cost_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cost_models.id"), nullable=False)
    capital_tier: Mapped[str | None] = mapped_column(String(4))
    code_version: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    halt_reason: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TS)

    __table_args__ = (
        CheckConstraint(
            "mode IN ('backtest','simulator','testnet','live')", name="ck_runs_mode"),
        CheckConstraint(
            "capital_tier IS NULL OR capital_tier IN ('L0','L1','L2','L3')",
            name="ck_runs_tier"),
        Index("ix_runs_strategy_mode", "strategy_id", "mode"),
    )


class Intent(Base):
    """What the strategy wanted. Append-only, written before any order (§14.3)."""

    __tablename__ = "intents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    target_position: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    reason: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Which rule blocked it, if any. A veto is a decision and belongs here.
    vetoed_by: Mapped[str | None] = mapped_column(Text)
    #: Survives venue-level netting, so per-strategy attribution stays possible
    #: when two strategies want opposite positions in one asset (§13.4).
    attributed_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategies.id"))

    __table_args__ = (Index("ix_intents_run_time", "run_id", "decided_at"),)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    intent_id: Mapped[int | None] = mapped_column(ForeignKey("intents.id"))
    #: Deterministic, which is what makes a retry after a timeout idempotent.
    client_order_id: Mapped[str] = mapped_column(Text, nullable=False)
    venue_order_id: Mapped[str | None] = mapped_column(Text)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    #: Decision time and send time are separate columns because their difference
    #: is what makes divergence attributable to timing rather than to fills.
    intent_ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    sent_ts: Mapped[datetime | None] = mapped_column(TS)
    ack_ts: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("run_id", "client_order_id", name="uq_orders_client_id"),
    )


class FillRow(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    fill_ts: Mapped[datetime] = mapped_column(TS, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    #: Kept alongside the realised price so per-trade slippage is a stored fact
    #: rather than a reconstruction from a price series recorded elsewhere.
    expected_price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    liquidity: Mapped[str] = mapped_column(String(8), nullable=False)

    __table_args__ = (
        CheckConstraint("liquidity IN ('maker','taker')", name="ck_fills_liquidity"),
    )


class PositionRow(Base):
    __tablename__ = "positions"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    asset: Mapped[str] = mapped_column(String(32), primary_key=True)
    as_of: Mapped[datetime] = mapped_column(TS, primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    avg_entry: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    unrealised_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    realised_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    funding_paid: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))


class Balance(Base):
    __tablename__ = "balances"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    as_of: Mapped[datetime] = mapped_column(TS, primary_key=True)
    currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    equity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    free: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    used_margin: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)


class RiskEvent(Base):
    """Every time a limit fired. The risk engine writes here; nothing else does."""

    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    observed: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    limit_value: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        TS, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "action IN ('veto','halt','reduce_only','kill')", name="ck_risk_action"),
        Index("ix_risk_events_run", "run_id", "occurred_at"),
    )


class PositionReconciliation(Base):
    """Our position view against the venue's (§14.2).

    Distinct from `reconciliations`, which compares *returns*. Conflating the
    two loses the distinction between "we hold something different from what
    they hold" and "we earned something different from what we expected".
    """

    __tablename__ = "position_reconciliations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    qty_internal: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    qty_venue: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    balance_internal: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    balance_venue: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    discrepancy: Mapped[bool] = mapped_column(Boolean, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(String(16))


class Reconciliation(Base):
    """Daily return divergence between execution modes (§12.5)."""

    __tablename__ = "reconciliations"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    day: Mapped[datetime] = mapped_column(TS, primary_key=True)
    ref_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    return_observed: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    return_reference: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    divergence_ci_low: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    divergence_ci_high: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    #: {costs, timing, fills, data}. Knowing *that* two series diverge is
    #: useless; the decomposition is what says which one to fix (§12.5).
    attribution: Mapped[dict] = mapped_column(JSONB, nullable=False)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False)

    __table_args__ = (
        CheckConstraint("ref_kind IN ('backtest','simulator')", name="ck_recon_ref"),
        CheckConstraint("verdict IN ('ok','warn','breach')", name="ck_recon_verdict"),
    )


class CapitalRampEvent(Base):
    """Tier changes, with the evidence that justified them (§14.5)."""

    __tablename__ = "capital_ramp_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    from_tier: Mapped[str | None] = mapped_column(String(4))
    to_tier: Mapped[str] = mapped_column(String(4), nullable=False)
    capital: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    #: True when §14.1's minimum-viable floor raised the tier above its nominal
    #: fraction. Worth storing: a floored tier is not the tier §14.5 describes.
    floored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TS, nullable=False)
