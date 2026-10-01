"""Research registry: strategies, studies, experiments, verdicts, gates.

Plan.md §5.1, §17 phase 2-3.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="admin"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "cost_models",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("venue", sa.String(32), sa.ForeignKey("venues.code"), nullable=False),
        sa.Column("fill_model", sa.String(16), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("valid_from", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("fill_model IN ('optimistic','realistic','pessimistic')",
                           name="ck_cost_fill_model"),
    )

    op.create_table(
        "feature_definitions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column("impl_ref", sa.Text, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("param_schema", postgresql.JSONB, nullable=False),
        sa.Column("lookback_bars", sa.Integer, nullable=False),
        sa.Column("is_point_in_time", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("name", "code_hash", name="uq_feature_name_hash"),
    )

    op.create_table(
        "label_definitions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("horizon_bars", sa.Integer, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
    )

    op.create_table(
        "strategies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("impl_ref", sa.Text, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("param_space", postgresql.JSONB, nullable=False),
        sa.Column("constraints", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.UniqueConstraint("name", "code_hash", name="uq_strategy_name_hash"),
        sa.CheckConstraint(
            "status IN ('draft','screening','validating','candidate','simulator',"
            "'testnet','live_ramp','live','halted','rejected','retired')",
            name="ck_strategy_status"),
    )

    op.create_table(
        "gate_configs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transition", sa.String(48), nullable=False),
        sa.Column("thresholds", postgresql.JSONB, nullable=False),
        sa.Column("rationale", sa.Text, nullable=False),
        sa.Column("changed_by", UUID, sa.ForeignKey("users.id")),
        sa.Column("valid_from", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", TS),
    )

    op.create_table(
        "studies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("search_method", sa.String(16), nullable=False),
        sa.Column("planned_trials", sa.Integer, nullable=False),
        sa.Column("executed_trials", sa.Integer, nullable=False, server_default="0"),
        sa.Column("min_detectable_sharpe", sa.Numeric(10, 4), nullable=False),
        sa.Column("hold_days_min", sa.Numeric(8, 3), nullable=False),
        sa.Column("hold_days_max", sa.Numeric(8, 3), nullable=False),
        sa.Column("compute_budget_hours", sa.Numeric(10, 3), nullable=False),
        sa.Column("compute_used_hours", sa.Numeric(10, 3), nullable=False, server_default="0"),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("executed_trials >= 0", name="ck_study_trials_nonneg"),
        sa.CheckConstraint("hold_days_min > 0 AND hold_days_max >= hold_days_min",
                           name="ck_study_hold_band"),
    )

    op.create_table(
        "experiments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("study_id", UUID, sa.ForeignKey("studies.id"), nullable=False),
        sa.Column("dataset_version_id", UUID, sa.ForeignKey("dataset_versions.id"),
                  nullable=False),
        sa.Column("label_definition_id", UUID, sa.ForeignKey("label_definitions.id")),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("feature_set", postgresql.JSONB, nullable=False),
        sa.Column("universe", postgresql.JSONB, nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("cv_scheme", postgresql.JSONB, nullable=False),
        sa.Column("splits", postgresql.JSONB, nullable=False),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("cost_model_id", UUID, sa.ForeignKey("cost_models.id"), nullable=False),
        sa.Column("seed", sa.BigInteger, nullable=False),
        sa.Column("code_version", sa.Text, nullable=False),
        sa.Column("image_digest", sa.Text, nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("realised_hold_days", sa.Numeric(10, 4)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("tier >= 2", name="ck_experiment_tier_min"),
    )
    op.create_index("ix_experiments_study", "experiments", ["study_id"])

    op.create_table(
        "experiment_results",
        sa.Column("experiment_id", UUID, sa.ForeignKey("experiments.id"), primary_key=True),
        sa.Column("split", sa.String(16), primary_key=True),
        sa.Column("regime", sa.String(32), primary_key=True, server_default="all"),
        sa.Column("metrics", postgresql.JSONB, nullable=False),
        sa.Column("n_trades", sa.Integer, nullable=False),
        sa.Column("returns_uri", sa.Text),
        sa.Column("artifacts_uri", sa.Text),
    )

    op.create_table(
        "validation_reports",
        sa.Column("experiment_id", UUID, sa.ForeignKey("experiments.id"), primary_key=True),
        sa.Column("trials_considered", sa.Integer, nullable=False),
        sa.Column("deflated_sharpe", sa.Numeric(10, 6)),
        sa.Column("dsr_pvalue", sa.Numeric(10, 6)),
        sa.Column("pbo", sa.Numeric(10, 6)),
        sa.Column("spa_pvalue", sa.Numeric(10, 6)),
        sa.Column("param_stability", sa.Numeric(10, 6)),
        sa.Column("min_trl_days", sa.Numeric(14, 4)),
        sa.Column("regime_coverage", postgresql.JSONB),
        sa.Column("baselines", postgresql.JSONB),
        sa.Column("cost_sensitivity", postgresql.JSONB),
        sa.Column("capacity_ceiling", sa.Numeric(20, 4)),
        sa.Column("gate_config_id", UUID, sa.ForeignKey("gate_configs.id"), nullable=False),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reasons", postgresql.JSONB, nullable=False),
        sa.CheckConstraint("verdict IN ('pass','fail','inconclusive')", name="ck_verdict"),
    )

    op.create_table(
        "lifecycle_transitions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("strategy_id", UUID, sa.ForeignKey("strategies.id"), nullable=False),
        sa.Column("from_state", sa.String(16), nullable=False),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("gate_config_id", UUID, sa.ForeignKey("gate_configs.id")),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("occurred_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_transitions_strategy", "lifecycle_transitions",
                    ["strategy_id", "occurred_at"])


def downgrade() -> None:
    for table in (
        "lifecycle_transitions", "validation_reports", "experiment_results", "experiments",
        "studies", "gate_configs", "strategies", "label_definitions", "feature_definitions",
        "cost_models", "users",
    ):
        op.drop_table(table)
