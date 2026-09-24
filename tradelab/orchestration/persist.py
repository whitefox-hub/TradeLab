"""Persisting a study. Plan.md §5.7, §8.3, §16.1.

Where a result lands is not a storage preference, it is the tier boundary of
§5.7:

**Tier A results go to Parquet**, one partition per study. A screening campaign
of 10^6 configurations would put five million rows into Postgres and make the
registry unusable to query, in exchange for referential integrity on results that
are disposable by design.

**Only tier >= 2 enters Postgres**, enforced by a CHECK constraint on
`experiments`. A candidate that survived screening has earned integrity; the
999,999 it beat have not.

Two things must cross that boundary anyway, because the statistics depend on them:

- `studies.executed_trials` counts every tier A configuration, even though their
  rows are not in Postgres. That number deflates the Sharpe of whatever survives
  (§8.3); losing it makes every verdict issued afterwards more confident than the
  evidence supports.
- Tier A `config_hash` values are kept in the Parquet partition, so deduplication
  still works across campaigns (§9.6).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from sqlalchemy.orm import Session

from tradelab.orchestration.study import StudyResult
from tradelab.registry.models import (
    CostModel,
    Experiment,
    ExperimentResult,
    GateConfig,
    Strategy,
    Study,
    ValidationReport,
)


def image_digest() -> str:
    """Digest of the image this process is running in.

    Recorded on every experiment (§16.1). It read "unpinned" until the base
    images were pinned, which made the reproducibility contract nominal: an
    experiment citing a tag cites something that moves.
    """
    import os

    for source in (os.environ.get("TRADELAB_IMAGE_DIGEST"), _cgroup_image()):
        if source:
            return source
    return "unpinned"


def _cgroup_image() -> str | None:
    """Container id from cgroup, as a fallback identity inside Docker."""
    from pathlib import Path as _Path

    try:
        text = _Path("/proc/self/cgroup").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if "docker" in line and len(line.rsplit("/", 1)[-1]) >= 12:
            return f"container:{line.rsplit('/', 1)[-1][:12]}"
    return None


def code_version() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "uncommitted"


def json_safe(value):
    """Make a metrics dict storable as JSONB.

    JSON has no Infinity, and `min_trl_obs` is legitimately infinite whenever the
    observed Sharpe does not exceed the null benchmark — the track record needed
    is unbounded. Dropping the key would lose that meaning, and coercing it to a
    large number would invent a finite answer, so it becomes the string
    "infinity" and stays readable as what it is.
    """
    import math

    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        if math.isinf(value):
            return "infinity" if value > 0 else "-infinity"
        if math.isnan(value):
            return None
    return value


def config_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def write_tier_a(result: StudyResult, study_id: uuid.UUID, root: Path) -> Path:
    """Every screened configuration, to Parquet. Disposable but not discarded.

    Kept because the trial count has to be auditable after the fact: a verdict
    citing 1,008 trials should be checkable against 1,008 rows, not taken on
    trust.
    """
    target = root / "tier_a" / f"study={study_id}" / "results.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(result.tier_a_rows)
    frame.write_parquet(target, compression="zstd")
    return target


def ensure_gate_config(session: Session, thresholds: dict, rationale: str) -> GateConfig:
    """Fetch or create the active gate. `rationale` is mandatory (§20.3)."""
    existing = (
        session.query(GateConfig)
        .filter_by(transition="validating->candidate", valid_to=None)
        .one_or_none()
    )
    if existing is not None:
        return existing
    config = GateConfig(
        id=uuid.uuid4(), transition="validating->candidate",
        thresholds=thresholds, rationale=rationale, valid_from=datetime.now(UTC),
    )
    session.add(config)
    session.commit()
    return config


def persist(
    result: StudyResult, session: Session, root: Path,
    dataset_version_id: uuid.UUID, venue: str = "binance",
    digest: str | None = None,
) -> uuid.UUID:
    """Record a study: tier A to Parquet, the candidate to Postgres."""
    spec = result.spec

    strategy = _ensure_strategy(session, spec)
    cost_model = _ensure_cost_model(session, spec, venue)
    gate = ensure_gate_config(
        session,
        thresholds=dict(result.gate.metrics.get("thresholds", {})) or {"source": "defaults"},
        rationale="Initial thresholds, as specified in Plan.md §15.",
    )

    payload = {
        "strategy": strategy.name, "params": result.best_params,
        "timeframe": spec.timeframe, "trials": result.executed_trials,
        "dataset": str(dataset_version_id), "code": code_version(),
    }
    digest = config_hash(payload)

    # Idempotent by configuration (§5.1). Re-running an identical study reuses
    # its record rather than failing on the unique constraint: that constraint is
    # there to make repetition free, not to punish it.
    existing = session.query(Experiment).filter_by(config_hash=digest).one_or_none()
    if existing is not None:
        return existing.id

    study = Study(
        id=uuid.uuid4(), strategy_id=strategy.id, search_method="grid",
        planned_trials=spec.planned_trials,
        # Every configuration screened, not only the survivor (§8.3).
        executed_trials=result.executed_trials,
        min_detectable_sharpe=spec.min_detectable_sharpe or 0,
        hold_days_min=2, hold_days_max=7, compute_budget_hours=1,
    )
    session.add(study)
    session.flush()

    write_tier_a(result, study.id, root)

    experiment = Experiment(
        id=uuid.uuid4(), study_id=study.id, dataset_version_id=dataset_version_id,
        params=result.best_params,
        feature_set=[{"name": spec.feature, "params": result.best_params}],
        universe={"assets": [spec.name.split("-")[0]]}, timeframe=spec.timeframe,
        cv_scheme={"method": "holdout", "oos_fraction": spec.oos_fraction},
        splits={"oos_fraction": spec.oos_fraction},
        # Tier 2: it survived screening, so it earns a row (§5.7).
        tier=2, cost_model_id=cost_model.id, seed=0,
        code_version=code_version(), image_digest=digest or image_digest(),
        config_hash=digest, status="completed",
    )
    session.add(experiment)
    session.flush()

    session.add(ExperimentResult(
        experiment_id=experiment.id, split="oos", regime="all",
        metrics=json_safe({**result.gate.metrics, **result.stability,
                           **result.robustness, "pbo": result.pbo}),
        n_trades=result.best.n_trades if result.best else 0,
    ))
    session.add(ValidationReport(
        experiment_id=experiment.id,
        trials_considered=result.executed_trials,
        deflated_sharpe=_finite(result.gate.metrics.get("deflated_sharpe")),
        pbo=result.pbo,
        param_stability=_finite(result.stability.get("robust_score")),
        # Numeric column, so an unbounded requirement is stored as NULL and the
        # readable form lives in `reasons`.
        min_trl_days=_finite(result.gate.metrics.get("min_trl_obs")),
        baselines=json_safe(result.robustness) or None,
        gate_config_id=gate.id,
        verdict=str(result.verdict),
        reasons={"reasons": result.gate.reasons,
                 "min_trl_obs": json_safe(result.gate.metrics.get("min_trl_obs"))},
    ))
    # One transaction for study, experiment, results and verdict. An earlier
    # version committed each in turn, and a failure partway left an experiment
    # with no verdict attached — which is not a record, it is debris that later
    # queries would count.
    session.commit()
    return experiment.id


def _finite(value: float | None) -> float | None:
    import math

    if value is None or not math.isfinite(value):
        return None
    return value


def _ensure_strategy(session: Session, spec) -> Strategy:
    code = hashlib.sha256(f"threshold:{spec.feature}".encode()).hexdigest()
    existing = session.query(Strategy).filter_by(name=spec.feature, code_hash=code).one_or_none()
    if existing is not None:
        return existing
    strategy = Strategy(
        id=uuid.uuid4(), name=spec.feature, kind="rule",
        impl_ref="tradelab.engines.tier_a.engine:threshold_position", code_hash=code,
        param_space={"period": [min(spec.periods), max(spec.periods)],
                     "entry": [min(spec.entries), max(spec.entries)]},
        constraints={}, status="screening",
    )
    session.add(strategy)
    session.commit()
    return strategy


def _ensure_cost_model(session: Session, spec, venue: str) -> CostModel:
    params = {
        "taker_fee": spec.costs.taker_fee, "maker_fee": spec.costs.maker_fee,
        "half_spread": spec.costs.half_spread, "slippage": spec.costs.slippage,
    }
    code = config_hash(params)
    existing = session.query(CostModel).filter_by(code_hash=code).one_or_none()
    if existing is not None:
        return existing
    model = CostModel(
        id=uuid.uuid4(), name="binance-retail", venue=venue, fill_model="realistic",
        params=params, code_hash=code, valid_from=datetime.now(UTC),
    )
    session.add(model)
    session.commit()
    return model
