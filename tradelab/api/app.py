"""The operator interface (FastAPI). Plan.md §20.

Invariants: it computes no figure (reads only worker-persisted values; time series belong to
Grafana, §20.1), and it cannot start live trading (no mode-switch route, no venue credential).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from tradelab.api.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    Operator,
    Sessions,
    operator_dependency,
    verify_password,
)
from tradelab.api.settings_rules import RULES
from tradelab.api.settings_rules import Invalid as InvalidSetting
from tradelab.api.settings_rules import validate as validate_setting
from tradelab.collectors.sentiment import CMC_SOURCE
from tradelab.collectors.sentiment import SOURCE as FNG_SOURCE
from tradelab.config import Settings
from tradelab.scenario import run as scenario_run
from tradelab.scenario.tilt import direction as trend_direction

#: Static screens and shared chrome, mounted at /static.
STATIC = Path(__file__).parent / "static"

#: The only writes §20.1 allows this surface; everything else is a read.
WRITABLE = frozenset({"approval", "transition_request"})


class SignIn(BaseModel):
    email: str
    password: str


class SettingRequest(BaseModel):
    """A change to an operator setting. Plan.md §12.4, §20.4. `rationale` is required."""

    value: dict[str, Any]
    rationale: str = Field(min_length=8)


class ScenarioRequest(BaseModel):
    """The market conditions a scenario simulation runs under. Plan.md §20.

    Deliberately no asset or date. `extra="forbid"`: unknown fields error, not dropped.
    """

    model_config = {"extra": "forbid"}

    #: Operator's news reading, 1-10 with **5 neutral** (leaves positions unchanged). Direction is
    #: derived from the side of 5, never stored; do not read magnitude as distance from zero.
    trend: int = Field(ge=1, le=10)
    #: Assumed market sentiment, 1-100. Kept separate from measured `sentiment_readings`.
    sentiment: int = Field(ge=1, le=100)
    #: 'manual' = person's reading, 'adopted' = computed value a person accepted, 'derived' = no
    #: person. PREREG-005 filters on this to test only the operator's own reading.
    source: str = Field(default="manual", pattern="^(manual|derived|adopted)$")

class RiskConfigRequest(BaseModel):
    """A new risk limit configuration. Plan.md §14.1, §12.4, §20.

    Created, never edited (§14.1: runs reference a version). `extra="forbid"`.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9 _.-]+$")
    #: Every limit (none may fall back to a default); bounds in tradelab.risk.bounds.
    limits: dict[str, Any] = Field(min_length=1)
    rationale: str = Field(min_length=10, max_length=1000)


class CloseRequest(BaseModel):
    """The operator's manual exit of one open position of a forward run."""

    model_config = {"extra": "forbid"}

    run_id: uuid.UUID
    asset: str = Field(min_length=1, max_length=40)


class ApprovalRequest(BaseModel):
    subject_kind: str = Field(pattern="^(strategy|capital_tier|gate_change)$")
    subject_id: uuid.UUID
    decision: str = Field(pattern="^(approve|reject)$")
    gate_config_id: uuid.UUID | None = None
    notes: str | None = None


def _plain(value: Any) -> str:
    """A limit as a person writes it: 0.25, 10000, 60. The database pads NUMERIC columns
    with zeros (0.25000000), which reads as noise."""
    number = Decimal(str(value))
    text = format(number.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".")


def create_app(engine: sa.Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or sa.create_engine(settings.database_url)
    make_session = sessionmaker(engine, expire_on_commit=False)

    secret = os.environ.get("TRADELAB_SESSION_SECRET", "")
    if len(secret) < 32:
        # Random per process, never a constant default secret.
        from tradelab.api.security import new_secret

        secret = new_secret()
    sessions = Sessions(secret)
    operator = operator_dependency(sessions)

    app = FastAPI(title="TradeLab", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def no_stale_chrome(request, call_next):
        """Force revalidation of every screen and /static file, so a cached page or app.js
        cannot show a form or sidebar older than the server."""
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    def db():
        with make_session() as session:
            yield session

    # A default, not a local `Annotated` alias: with `from __future__ import annotations` FastAPI
    # cannot resolve a local alias, treats `session` as a query param, and every route 422s.
    session_dep = Depends(db)

    def audit(session: Session, actor: str, action: str, kind: str,
              subject: str, payload: dict | None = None) -> None:
        session.execute(sa.text(
            "INSERT INTO audit_log (actor, action, subject_kind, subject_id, payload) "
            "VALUES (:a, :ac, :k, :s, CAST(:p AS jsonb))"
        ), {"a": actor, "ac": action, "k": kind, "s": subject,
            "p": None if payload is None else json.dumps(payload)})

    def _before_payload(before: Any) -> dict[str, Any] | None:
        return None if before is None else {
            "trend": before["trend"], "sentiment": before["sentiment"]}

    def _run_payload(run_id: uuid.UUID, outcome: Any,
                     seconds: float) -> dict[str, Any]:
        return {
            "id": str(run_id), "reference": outcome.reference,
            "bars": outcome.bars,
            "long_multiplier": outcome.long_multiplier,
            "short_multiplier": outcome.short_multiplier,
            "sentiment_bucket": outcome.sentiment_bucket,
            "baseline": outcome.baseline, "conditioned": outcome.conditioned,
            "reached_step": outcome.reached_step,
            "stopped_because": outcome.stopped_because,
            "notes": outcome.notes, "seconds": seconds,
        }

    # -- session ------------------------------------------------------------

    @app.post("/api/session")
    def sign_in(body: SignIn, response: Response, session: Session = session_dep) -> dict:
        row = session.execute(
            sa.text("SELECT email, role FROM users WHERE email = :e"), {"e": body.email}
        ).first()
        expected = os.environ.get("TRADELAB_ADMIN_HASH", "")
        salt = os.environ.get("TRADELAB_ADMIN_SALT", "")
        if row is None or not expected or not verify_password(body.password, salt, expected):
            # Same message for unknown user and bad password (no user enumeration).
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        response.set_cookie(
            SESSION_COOKIE, sessions.issue(row.email), httponly=True, samesite="strict",
            max_age=int(SESSION_MAX_AGE.total_seconds()),
        )
        audit(session, row.email, "sign_in", "session", row.email)
        session.commit()
        return {"email": row.email, "role": row.role}

    @app.delete("/api/session")
    def sign_out(response: Response) -> dict:
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(who: Operator = operator) -> dict:
        return {"email": who.email, "role": who.role}

    # -- reads --------------------------------------------------------------

    @app.get("/api/strategies")
    def strategies(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT s.id, s.name, s.kind, s.status, s.code_hash, "
            "       count(DISTINCT st.id) AS studies, "
            "       count(DISTINCT e.id) AS experiments "
            "FROM strategies s "
            "LEFT JOIN studies st ON st.strategy_id = s.id "
            "LEFT JOIN experiments e ON e.study_id = st.id "
            "GROUP BY s.id ORDER BY s.name"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/runs")
    def runs(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        """Runs, newest first. Mode is read-only: no route changes it (§20.2)."""
        rows = session.execute(sa.text(
            "SELECT id, strategy_id, mode, venue, status, capital_tier, halt_reason, "
            "started_at, ended_at FROM runs ORDER BY started_at DESC LIMIT 200"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/universe")
    def universe(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT asset, venue, listed_at, delisted_at, tick_size, lot_size, "
            "min_notional, fee_tier FROM asset_universe ORDER BY asset"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/data-quality")
    def data_quality(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT q.id, q.kind, q.severity, q.asset, q.period_start, "
            "       q.period_end, q.detail, q.resolved_at, s.venue "
            "FROM data_quality_issues q LEFT JOIN sources s ON s.id = q.source_id "
            "WHERE q.resolved_at IS NULL "
            "ORDER BY CASE q.severity WHEN 'blocking' THEN 0 WHEN 'warn' THEN 1 "
            "         ELSE 2 END, q.period_start DESC LIMIT 500"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/strategies/{strategy_id}")
    def strategy_detail(strategy_id: uuid.UUID, who: Operator = operator,
                        session: Session = session_dep) -> dict:
        """One strategy with its studies and verdicts. Parameters are read-only (§12.4)."""
        row = session.execute(sa.text(
            "SELECT id, name, kind, status, code_hash, param_space, constraints "
            "FROM strategies WHERE id = :i"), {"i": strategy_id}).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such strategy")

        studies = session.execute(sa.text(
            "SELECT id, search_method, planned_trials, executed_trials, "
            "       min_detectable_sharpe, hold_days_min, hold_days_max, created_at "
            "FROM studies WHERE strategy_id = :i ORDER BY created_at DESC"
        ), {"i": strategy_id}).mappings().all()
        # Via experiments: a verdict belongs to a configuration, not the strategy.
        reports = session.execute(sa.text(
            "SELECT v.experiment_id, v.verdict, v.deflated_sharpe, v.pbo, "
            "       v.trials_considered, v.min_trl_days, v.reasons, "
            "       g.transition, g.rationale "
            "FROM validation_reports v "
            "JOIN experiments e ON e.id = v.experiment_id "
            "JOIN studies st ON st.id = e.study_id "
            "LEFT JOIN gate_configs g ON g.id = v.gate_config_id "
            "WHERE st.strategy_id = :i"
        ), {"i": strategy_id}).mappings().all()
        return {
            **dict(row),
            "studies": [dict(r) for r in studies],
            # Each verdict carries the gate it was judged under (§20.3).
            "validation_reports": [dict(r) for r in reports],
        }

    @app.get("/api/experiments")
    def experiments(who: Operator = operator, session: Session = session_dep,
                    limit: int = 200) -> list[dict]:
        """Tier B+ experiments; Tier A screening lives in Parquet, not the registry (§5.7)."""
        rows = session.execute(sa.text(
            "SELECT e.id, e.study_id, e.tier, e.params, e.timeframe, e.status, "
            "       e.realised_hold_days, e.created_at, "
            "       r.split, r.regime, r.metrics, r.n_trades, "
            "       v.verdict, v.deflated_sharpe, v.pbo "
            "FROM experiments e "
            "LEFT JOIN experiment_results r ON r.experiment_id = e.id "
            "LEFT JOIN validation_reports v ON v.experiment_id = e.id "
            "ORDER BY e.created_at DESC LIMIT :l"
        ), {"l": min(limit, 500)}).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/ingestion")
    def ingestion(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        """Recent collector runs, newest first."""
        rows = session.execute(sa.text(
            "SELECT i.id, i.started_at, i.ended_at, i.status, i.rows_written, "
            "       i.mode, i.period_start, i.period_end, i.watermark, "
            "       s.kind, s.venue "
            "FROM ingestion_runs i LEFT JOIN sources s ON s.id = i.source_id "
            "ORDER BY i.started_at DESC LIMIT 100"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/gates")
    def gates(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        """Active gates with age; `citable` once 24h old (§20.3)."""
        rows = session.execute(sa.text(
            "SELECT id, transition, thresholds, rationale, valid_from, "
            "       now() - valid_from AS age, "
            "       (now() - valid_from) >= interval '24 hours' AS citable "
            "FROM gate_configs WHERE valid_to IS NULL ORDER BY valid_from DESC"
        )).mappings().all()
        return [dict(r) | {"age": str(r["age"])} for r in rows]

    @app.get("/api/audit")
    def audit_trail(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT actor, action, subject_kind, subject_id, occurred_at "
            "FROM audit_log ORDER BY occurred_at DESC LIMIT 500"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/approvals")
    def approvals(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT a.id, a.subject_kind, a.subject_id, a.decision, a.notes, "
            "       a.decided_at, u.email AS approved_by "
            "FROM approvals a JOIN users u ON u.id = a.approved_by "
            "ORDER BY a.decided_at DESC LIMIT 200"
        )).mappings().all()
        return [dict(r) for r in rows]

    # -- the one write ------------------------------------------------------

    @app.post("/api/approvals", status_code=status.HTTP_201_CREATED)
    def record_approval(body: ApprovalRequest, who: Operator = operator,
                        session: Session = session_dep) -> dict[str, Any]:
        """Record an approval decision.

        The 24h cooling-off is enforced by a DB trigger only; this maps its error to a 409.
        """
        user = session.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": who.email}
        ).first()
        if user is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no such operator")

        approval_id = uuid.uuid4()
        try:
            session.execute(sa.text(
                "INSERT INTO approvals (id, subject_kind, subject_id, approved_by, "
                "decision, gate_config_id, notes) "
                "VALUES (:i, :k, :s, :u, :d, :g, :n)"
            ), {"i": approval_id, "k": body.subject_kind, "s": body.subject_id,
                "u": user.id, "d": body.decision, "g": body.gate_config_id,
                "n": body.notes})
        except sa.exc.DBAPIError as error:
            session.rollback()
            message = str(getattr(error, "orig", error))
            if "24 hours" in message or "cooling" in message:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "this approval cites a gate threshold changed less than 24 hours "
                    "ago. Lowering a bar is permitted; acting on it the same day is "
                    "not (§20.3).",
                ) from error
            raise HTTPException(status.HTTP_400_BAD_REQUEST, message.split("\n")[0]) from error

        audit(session, who.email, f"approval.{body.decision}", body.subject_kind,
              str(body.subject_id), {"gate_config_id": str(body.gate_config_id or "")})
        session.commit()
        return {"id": str(approval_id), "recorded_at": datetime.now(UTC).isoformat()}

    # -- parameters ----------------------------------------------------------

    #: Operator-editable settings. LOCKED lists what §12.4 forbids tuning (it would be a new
    #: strategy); keys here must each have a rule in settings_rules.RULES.
    EDITABLE = {
        "research_universe": "Which assets are collected and studied. Not a "
                             "strategy's universe, which is part of its identity.",
        "history_years": "How many years of history a study may draw on.",
        "rebalance_minutes": "How often a running strategy re-evaluates.",
        "alert_drawdown_pct": "Drawdown at which an alert is raised.",
        "event_retention_days": "How far back the event corpus is kept. Lowering "
                                "it removes older events on the next prune; "
                                "raising it rebuilds them from the announcement "
                                "archive, which is the source of truth.",
    }

    LOCKED = {
        "Signal parameters": "periods, thresholds, feature parameters",
        "Entry and exit rules": "including take profit and stop loss in the logic",
        "Feature selection": "which features a strategy reads",
        "Label definition": "how an outcome is defined",
        "A strategy's asset universe": "distinct from the research universe above",
    }

    @app.get("/api/settings")
    def settings_list(who: Operator = operator,
                      session: Session = session_dep) -> dict[str, Any]:
        """Current value per key (newest row; settings are append-only), plus locked items."""
        rows = session.execute(sa.text(
            "SELECT DISTINCT ON (s.key) s.key, s.value, s.rationale, s.valid_from, "
            "       u.email AS changed_by "
            "FROM app_settings s LEFT JOIN users u ON u.id = s.changed_by "
            "ORDER BY s.key, s.valid_from DESC"
        )).mappings().all()
        current = {r["key"]: dict(r) for r in rows}
        return {
            "editable": [
                {"key": key, "description": description,
                 "accepts": RULES[key].describes if key in RULES else None,
                 "fields": list(RULES[key].fields) if key in RULES else [],
                 **(current.get(key) or {"value": None, "rationale": None,
                                         "valid_from": None, "changed_by": None})}
                for key, description in EDITABLE.items()
            ],
            "locked": [{"name": name, "detail": detail}
                       for name, detail in LOCKED.items()],
        }

    @app.get("/api/settings/{key}/history")
    def setting_history(key: str, who: Operator = operator,
                        session: Session = session_dep) -> list[dict]:
        rows = session.execute(sa.text(
            "SELECT s.value, s.rationale, s.valid_from, u.email AS changed_by "
            "FROM app_settings s LEFT JOIN users u ON u.id = s.changed_by "
            "WHERE s.key = :k ORDER BY s.valid_from DESC LIMIT 50"
        ), {"k": key}).mappings().all()
        return [dict(r) for r in rows]

    @app.post("/api/settings/{key}", status_code=status.HTTP_201_CREATED)
    def record_setting(key: str, body: SettingRequest, who: Operator = operator,
                       session: Session = session_dep) -> dict[str, Any]:
        """Append a new setting value (never update in place); non-editable keys get a 403."""
        if key not in EDITABLE:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{key!r} is not an operator setting. §12.4 puts it in the column "
                f"that creates a new strategy, which returns to screening and "
                f"adds its trials to the counter — it cannot be edited here.",
            )
        # Validated server side: the endpoint is reachable without the form.
        try:
            validate_setting(key, body.value)
        except InvalidSetting as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error

        user = session.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": who.email}).first()
        session.execute(sa.text(
            "INSERT INTO app_settings (key, value, rationale, changed_by) "
            "VALUES (:k, CAST(:v AS jsonb), :r, :u)"
        ), {"k": key, "v": json.dumps(body.value), "r": body.rationale,
            "u": user.id if user else None})
        audit(session, who.email, "setting.change", "app_setting", key,
              {"value": body.value, "rationale": body.rationale})
        session.commit()
        return {"key": key, "value": body.value}

    @app.get("/parameters", include_in_schema=False)
    def parameters_screen() -> FileResponse:
        return FileResponse(STATIC / "parameters.html")


    # -- the scenario, and the market's own reading of itself ---------------

    @app.get("/api/scenario")
    def read_scenario(who: Operator = operator,
                      session: Session = session_dep) -> dict[str, Any]:
        """The single market_conditions row (id=1, seeded by migration 0013)."""
        row = session.execute(sa.text(
            "SELECT trend, sentiment, source FROM market_conditions WHERE id = 1"
        )).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                "the conditions row is missing; migration 0013 seeds it")
        return {"trend": row["trend"],
                "direction": trend_direction(row["trend"]),
                "sentiment": row["sentiment"], "source": row["source"]}

    @app.put("/api/scenario")
    def set_scenario(body: ScenarioRequest, who: Operator = operator,
                     session: Session = session_dep) -> dict[str, Any]:
        """Update the single conditions row (idempotent PUT); a real change starts a run."""
        # Locked read in the same transaction, so the audited "from" is what was replaced.
        before = session.execute(sa.text(
            "SELECT trend, sentiment FROM market_conditions WHERE id = 1 FOR UPDATE"
        )).mappings().first()

        # No-op test lives in the WHERE (not a Python compare) to avoid a race; unchanged
        # saves touch no row and write no log.
        row = session.execute(sa.text(
            "UPDATE market_conditions SET trend = :t, sentiment = :s, "
            "source = :src WHERE id = 1 AND ("
            "  trend IS DISTINCT FROM :t OR sentiment IS DISTINCT FROM :s) "
            "RETURNING trend, sentiment, source"
        ), {"t": body.trend, "s": body.sentiment,
            "src": body.source}).mappings().first()

        if row is None:
            # Nothing moved, or the row is missing; the latter must surface as a 500.
            current = session.execute(sa.text(
                "SELECT trend, sentiment, source FROM market_conditions WHERE id = 1"
            )).mappings().first()
            if current is None:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "the conditions row is missing; migration 0013 seeds it")
            return {"trend": current["trend"],
                    "direction": trend_direction(current["trend"]),
                    "sentiment": current["sentiment"],
                    "source": current["source"], "changed": False}

        # Runs synchronously (ms on the current archive); `seconds` is reported to show growth.
        # The audit entry records from/to and the run id.
        started = time.perf_counter()
        try:
            outcome = scenario_run.execute(
                body.trend, body.sentiment)
        except FileNotFoundError as missing:
            # Keep the saved scenario even if the run cannot start.
            audit(session, who.email, "scenario.set", "scenario", "market",
                  {"trend": body.trend, "sentiment": body.sentiment,
                   "source": body.source,
                   "from": _before_payload(before)})
            session.commit()
            return {"trend": row["trend"],
                    "direction": trend_direction(row["trend"]),
                    "sentiment": row["sentiment"], "changed": True,
                    "run": None, "run_error": str(missing)}

        run_id = uuid.uuid4()
        session.execute(sa.text(
            "INSERT INTO scenario_runs (id, actor, trend, "
            "  sentiment, sentiment_bucket, long_multiplier, short_multiplier, "
            "  reference, bars, baseline, conditioned, reached_step, "
            "  stopped_because, source) "
            "VALUES (:i, :a, :t, :s, :b, :lm, :sm, :r, :n, "
            "  CAST(:base AS jsonb), CAST(:cond AS jsonb), :step, :why, :src)"
        ), {"i": run_id, "a": who.email, "t": body.trend, "s": body.sentiment,
            "b": outcome.sentiment_bucket, "lm": outcome.long_multiplier,
            "sm": outcome.short_multiplier, "r": outcome.reference,
            "n": outcome.bars, "base": json.dumps(outcome.baseline),
            "cond": json.dumps(outcome.conditioned),
            "step": outcome.reached_step, "why": outcome.stopped_because,
            "src": body.source})

        audit(session, who.email, "scenario.set", "scenario", "market",
              {"trend": body.trend, "sentiment": body.sentiment,
               "source": body.source,
               "from": _before_payload(before), "run": str(run_id)})
        session.commit()
        return {"trend": row["trend"],
                "direction": trend_direction(row["trend"]),
                "sentiment": row["sentiment"], "source": row["source"],
                "changed": True,
                "run": _run_payload(run_id, outcome,
                                    round(time.perf_counter() - started, 3))}

    @app.get("/api/scenario/history")
    def scenario_history(limit: int = 40, who: Operator = operator,
                         session: Session = session_dep) -> list[dict[str, Any]]:
        """Scenario changes, newest first, read from `audit_log` (no second history table).

        `from` is null on older entries; the screen treats those as changing both dials.
        """
        rows = session.execute(sa.text(
            "SELECT actor, occurred_at, payload FROM audit_log "
            "WHERE subject_kind = 'scenario' AND action = 'scenario.set' "
            "ORDER BY occurred_at DESC, id DESC LIMIT :n"
        ), {"n": max(1, min(limit, 200))}).mappings().all()

        out: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"] or {}
            out.append({
                "at": row["occurred_at"].isoformat(),
                "actor": row["actor"],
                "trend": payload.get("trend"),
                "sentiment": payload.get("sentiment"),
                "from": payload.get("from"),
            })
        return out

    @app.get("/api/scenario/runs")
    def scenario_runs(limit: int = 10, who: Operator = operator,
                      session: Session = session_dep) -> list[dict[str, Any]]:
        """Scenario runs, newest first. Conditions stored per run; never join market_conditions."""
        rows = session.execute(sa.text(
            "SELECT id, ran_at, actor, trend, sentiment, "
            "  sentiment_bucket, long_multiplier, short_multiplier, reference, "
            "  bars, baseline, conditioned, reached_step, stopped_because "
            "FROM scenario_runs ORDER BY ran_at DESC, id DESC LIMIT :n"
        ), {"n": max(1, min(limit, 100))}).mappings().all()
        return [{
            "id": str(r["id"]), "at": r["ran_at"].isoformat(), "actor": r["actor"],
            "trend": r["trend"], "direction": trend_direction(r["trend"]),
            "sentiment": r["sentiment"], "sentiment_bucket": r["sentiment_bucket"],
            "long_multiplier": float(r["long_multiplier"]),
            "short_multiplier": float(r["short_multiplier"]),
            "reference": r["reference"], "bars": r["bars"],
            "baseline": r["baseline"], "conditioned": r["conditioned"],
            "reached_step": r["reached_step"],
            "stopped_because": r["stopped_because"],
        } for r in rows]

    @app.get("/api/fear-greed")
    def read_fear_greed(who: Operator = operator,
                        session: Session = session_dep) -> dict[str, Any]:
        """CMC and alternative.me fear/greed side by side (never averaged), plus stored history
        points from the deeper series. Read back only (§20.1)."""
        def latest(source: str) -> dict[str, Any] | None:
            row = session.execute(sa.text(
                "SELECT day, value, classification, source, available_at "
                "FROM sentiment_readings WHERE source = :s "
                "ORDER BY day DESC LIMIT 1"
            ), {"s": source}).mappings().first()
            if row is None:
                return None
            return {
                "source": row["source"], "day": row["day"].isoformat(),
                "value": row["value"], "classification": row["classification"],
                "available_at": row["available_at"].isoformat(),
                "days_old": (datetime.now(UTC).date() - row["day"]).days,
            }

        primary = latest(CMC_SOURCE) or latest(FNG_SOURCE)
        if primary is None:
            return {"available": False}
        other = latest(FNG_SOURCE if primary["source"] == CMC_SOURCE else CMC_SOURCE)

        # History from the deepest series (CMC serves only its latest value); `source` names it.
        deep = session.execute(sa.text(
            "SELECT source, count(*) AS n, max(day) AS newest "
            "FROM sentiment_readings GROUP BY source ORDER BY n DESC LIMIT 1"
        )).mappings().first()
        history: dict[str, Any] = {"source": deep["source"], "entries": deep["n"]}

        def at(offset: int) -> dict[str, Any] | None:
            row = session.execute(sa.text(
                "SELECT day, value, classification FROM sentiment_readings "
                "WHERE source = :s AND day <= :d ORDER BY day DESC LIMIT 1"
            ), {"s": deep["source"],
                "d": deep["newest"] - timedelta(days=offset)}).mappings().first()
            return None if row is None else {
                "day": row["day"].isoformat(), "value": row["value"],
                "classification": row["classification"]}

        # "On or before", so a missing day yields the prior value rather than a blank.
        history |= {"yesterday": at(1), "last_week": at(7), "last_month": at(30)}

        extremes = session.execute(sa.text(
            "SELECT "
            " (SELECT json_build_object('day', day, 'value', value, "
            "         'classification', classification) FROM sentiment_readings "
            "   WHERE source = :s AND day > :from_day "
            "   ORDER BY value DESC, day DESC LIMIT 1) AS high, "
            " (SELECT json_build_object('day', day, 'value', value, "
            "         'classification', classification) FROM sentiment_readings "
            "   WHERE source = :s AND day > :from_day "
            "   ORDER BY value ASC, day DESC LIMIT 1) AS low"
        ), {"s": deep["source"],
            "from_day": deep["newest"] - timedelta(days=365)}).mappings().first()
        history |= {"year_high": extremes["high"], "year_low": extremes["low"]}

        return {"available": True, "primary": primary, "other": other,
                "history": history}

    # -- the screen ---------------------------------------------------------

    @app.get("/trade_lab", include_in_schema=False)
    def old_home() -> RedirectResponse:
        """Legacy path; redirects to /home."""
        return RedirectResponse("/home", status_code=308)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:          # an icon, not a screen: the menu tests skip it
        """Browsers ask the root for it even when a page links /static/favicon.png."""
        return FileResponse(STATIC / "favicon.png", media_type="image/png")

    @app.get("/login", include_in_schema=False)
    def login_screen() -> FileResponse:
        """Sign-in page. Its `next` param must be honoured only for same-origin paths."""
        return FileResponse(STATIC / "login.html")

    @app.get("/home", include_in_schema=False)
    def home() -> FileResponse:
        """Home screen."""
        return FileResponse(STATIC / "index.html")

    @app.get("/market-sentiment", include_in_schema=False)
    def market_sentiment_screen() -> FileResponse:
        """Operator's market trend and the measured fear/greed index on one page. §20."""
        return FileResponse(STATIC / "market_sentiment.html")

    @app.get("/audit", include_in_schema=False)
    def audit_screen() -> FileResponse:
        return FileResponse(STATIC / "audit.html")

    #: The decision sequence in run order, shown on /algorithm. Plan.md §8, §9, §12, §14.
    #: Each step has its invariant ("rule") and, where one exists, the defect behind it ("cost").
    ALGORITHM = [
        {
            "phase": "Research · one study",
            "steps": [
                {"n": 1, "name": "Load bars",
                 "does": "Read OHLCV for the asset and timeframe, from a "
                         "materialised view when one is fresh.",
                 "rule": "A bar's timestamp is its own close, not the moment it "
                         "was ingested. Three columns keep that separate: "
                         "event_time, ingest_time, available_at."},
                {"n": 2, "name": "Check power, before anything is spent",
                 "does": "Estimate the independent observations this design "
                         "will produce and compare against the effect it is "
                         "looking for.",
                 "rule": "A study that cannot resolve its own target is refused "
                         "here rather than discovered afterwards. The trial "
                         "counter is monotonic, so an underpowered search still "
                         "raises the bar for every study after it.",
                 "cost": "Two campaigns were spent before this existed. Their "
                         "negative results could only have confirmed a Sharpe "
                         "above 3.5 — a formality, not a test."},
                {"n": 3, "name": "Materialise features",
                 "does": "Compute every feature in the set over the whole series.",
                 "rule": "A value computed from bar t may only act from bar t+1. "
                         "The shift is applied centrally, once, rather than left "
                         "to each strategy to remember."},
                {"n": 4, "name": "Score each configuration",
                 "does": "For every parameter and threshold, build the position "
                         "series and evaluate it.",
                 "rule": "Scored on the TRAINING window only, and net of costs "
                         "and funding.",
                 "cost": "Both halves were defects. Selection saw the whole "
                         "series while the comment above it claimed otherwise — "
                         "BTCUSDT 4h went from 2.68 to 0.03 when fixed. And "
                         "selection scored gross, promoting a configuration at "
                         "+1.10 gross and -7.55 net over one at +0.94 net."},
                {"n": 5, "name": "Select on the smoothed surface",
                 "does": "Take the neighbourhood median of the score surface "
                         "rather than its highest single point.",
                 "rule": "The centre of the best region, not the luckiest draw. "
                         "A peak found by searching is mostly the noise that "
                         "made it a peak."},
                {"n": 6, "name": "Evaluate on the held-out window",
                 "does": "Run the chosen configuration on bars selection never "
                         "touched.",
                 "rule": "Net returns, and the effective observation count — "
                         "not the bar count.",
                 "cost": "Overlapping positions inflated the count 30 to 218 "
                         "times. On identical returns the deflated Sharpe went "
                         "from 1.000 to 0.024 once corrected."},
                {"n": 7, "name": "Deflate and judge",
                 "does": "Deflated Sharpe, PBO, minimum track record and "
                         "parameter stability, against a recorded threshold set.",
                 "rule": "Every trial raises the bar, including the discarded "
                         "ones. A positive result below what the sample can "
                         "resolve is inconclusive, not a pass.",
                 "cost": "The gate returned PASS on a Sharpe it had itself just "
                         "declared indistinguishable from chance."},
            ],
        },
        {
            "phase": "Execution · one order",
            "steps": [
                {"n": 8, "name": "Reconcile before anything is sent",
                 "does": "Compare the position we believe we hold against what "
                         "the venue says.",
                 "rule": "Mandatory at startup. A discrepancy halts and is never "
                         "silently corrected — adopting the venue's number hides "
                         "the bug that produced the gap, and the gap is the only "
                         "evidence it exists."},
                {"n": 9, "name": "Strategy asks",
                 "does": "The decision code produces a target position. It does "
                         "not know which mode it is running in.",
                 "rule": "Byte-identical across backtest, simulator, testnet and "
                         "live. Only the data feed and the venue adapter change."},
                {"n": 10, "name": "The scenario conditions the size",
                 "does": "In a scenario run, scale the intended position by the "
                         "market trend set on Market Sentiment: positions "
                         "agreeing with it up, positions disagreeing down, by up "
                         "to half at magnitude 10. **Not yet on the execution "
                         "path.** The tilt is imported by the scenario runner "
                         "and by nothing else, and the event damping written for "
                         "this step — half under a live regime shift, seven "
                         "tenths inside a scheduled one's anticipation window — "
                         "is constructed only in a test. Both are described here "
                         "as what this step will do, and marked so, because a "
                         "step described by how it ought to work reads as how it "
                         "does.",
                 "rule": "The market trend conditions, it does not signal. §11.3 puts "
                         "direction as an input to an expected-reaction model "
                         "and never a signal in itself — what is tradeable is "
                         "the residual, observed minus expected, and trading a "
                         "view everyone can read trades the half already priced. "
                         "So it scales a position and never creates one: applied "
                         "to a flat book it is still flat, and it cannot change "
                         "a sign.",
                 "cost": "The ceiling of half is chosen to bound the damage, not "
                         "because anything measured it. An operator who could "
                         "turn a systematic book into a directional bet with one "
                         "slider would have the slider as the strategy. Whether "
                         "the tilt helps at all is untested, which is why a run "
                         "reports it against the same run without it."},
                {"n": 11, "name": "Risk engine vetoes",
                 "does": "Sixteen limits, evaluated by a component independent "
                         "of the strategy.",
                 "rule": "Positions and orders are counted here, from observed "
                         "fills — never from what the strategy reports. A "
                         "strategy that miscounts its own state is the case "
                         "these limits exist for. Enforced in every mode, so "
                         "the limiter's first real execution is never with real "
                         "money."},
                {"n": 12, "name": "Write the intent, then send",
                 "does": "Record what was wanted, with a deterministic order id, "
                         "before calling the venue.",
                 "rule": "Written before, never after. A process that dies on "
                         "the next line leaves a record that startup can resolve "
                         "by asking about a known id rather than guessing from a "
                         "position delta."},
                {"n": 13, "name": "Fold the fill",
                 "does": "Update the position from the fill the venue reported.",
                 "rule": "Positions move from fills and nothing else. A view "
                         "derived from a second source can agree with the venue "
                         "while both are wrong."},
            ],
        },
    ]

    @app.get("/api/risk-limits")
    def risk_limits(who: Operator = operator,
                    session: Session = session_dep) -> dict[str, Any]:
        """Every enforced limit and recorded breach.

        Derived from the engine (`RiskLimits`, `HALTING`, `REQUIRES_HUMAN`), never a local copy.
        Values from the newest stored config, else defaults; `source` says which.
        """
        import dataclasses

        from tradelab.risk.bounds import BOUNDS
        from tradelab.risk.limits import (
            HALTING,
            PRESENTATION,
            REQUIRES_HUMAN,
            STOPS,
            Breach,
            RiskLimits,
        )

        defaults = RiskLimits()
        # The config in force; the single source for both values and the active badge.
        stored = session.execute(sa.text(
            "SELECT * FROM risk_limit_configs ORDER BY valid_from DESC LIMIT 1"
        )).mappings().first()

        halting = {b.value for b in HALTING}
        human = {b.value for b in REQUIRES_HUMAN}

        limits = []
        for field in dataclasses.fields(RiskLimits):
            name = field.name
            # Breach name = limit name, except `max_price_deviation_bps` -> `price_deviation`.
            # Membership match, so a rename shows as "halts nothing" rather than a crash.
            breach = name if name in halting or name in human else (
                "price_deviation" if name.startswith("max_price_deviation") else name)
            limits.append({
                "name": name,
                "value": _plain(stored[name]) if stored is not None and name in stored
                         else _plain(getattr(defaults, name)),
                "default": _plain(getattr(defaults, name)),
                "stops": STOPS.get(name, ""),
                # Halts the whole run, not just this order.
                "halts": breach in halting,
                # Requires a person to clear before resuming.
                "needs_a_person": breach in human,
                # Same bounds the POST checks, so the form matches the server.
                "low": str(BOUNDS[name].low), "high": str(BOUNDS[name].high),
                "whole": BOUNDS[name].whole, "bound": BOUNDS[name].describes,
                # The form's grouping and plain name; an unplaced limit lands in "Other".
                **dict(zip(("group", "label", "unit"),
                           PRESENTATION.get(name, ("Other", name, "")), strict=True)),
            })

        history = session.execute(sa.text(
            "SELECT c.id, c.name, c.valid_from, "
            "  (SELECT a.payload->>'rationale' FROM audit_log a "
            "    WHERE a.subject_id = CAST(c.id AS text) "
            "      AND a.action = 'risk_config.create' LIMIT 1) AS rationale, "
            "  (SELECT count(*) FROM runs r WHERE r.risk_limit_config_id = c.id) "
            "    AS runs "
            "FROM risk_limit_configs c ORDER BY c.valid_from DESC LIMIT 50"
        )).mappings().all()

        events = session.execute(sa.text(
            "SELECT rule, observed, limit_value, action, occurred_at, run_id "
            "FROM risk_events ORDER BY occurred_at DESC LIMIT 100"
        )).mappings().all()

        return {
            "source": "stored configuration" if stored is not None
                      else "engine defaults, no configuration stored",
            "config_name": None if stored is None else stored["name"],
            "valid_from": None if stored is None
                          else stored["valid_from"].isoformat(),
            "limits": limits,
            "halting": sorted(halting),
            "needs_a_person": sorted(human),
            # All Breach kinds, including those not tied to a configured limit.
            "breach_kinds": sorted(b.value for b in Breach),
            # Active = newest valid_from (unique constraint prevents ties); no stored flag.
            "history": [{
                "id": str(h["id"]), "name": h["name"],
                "valid_from": h["valid_from"].isoformat(),
                "rationale": h["rationale"], "runs": int(h["runs"]),
                "active": stored is not None and h["id"] == stored["id"],
            } for h in history],
            "events": [{
                "rule": e["rule"], "observed": str(e["observed"]),
                "limit_value": str(e["limit_value"]), "action": e["action"],
                "at": e["occurred_at"].isoformat(), "run_id": str(e["run_id"]),
            } for e in events],
            "event_count": len(events),
        }

    @app.post("/api/risk-limits", status_code=status.HTTP_201_CREATED)
    def add_risk_config(body: RiskConfigRequest, who: Operator = operator,
                        session: Session = session_dep) -> dict[str, Any]:
        """Create (INSERT, never UPDATE) a risk limit config (§14.1); bounds checked here."""
        from tradelab.risk.bounds import Invalid as InvalidLimit
        from tradelab.risk.bounds import validate as validate_limits

        try:
            parsed = validate_limits(body.limits)
        except InvalidLimit as refused:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(refused)) from None

        config_id = uuid.uuid4()
        columns = ", ".join(sorted(parsed))
        placeholders = ", ".join(f":{name}" for name in sorted(parsed))
        session.execute(sa.text(
            f"INSERT INTO risk_limit_configs (id, name, {columns}, valid_from) "
            f"VALUES (:id, :name, {placeholders}, now())"
        ), {"id": config_id, "name": body.name,
            **{k: str(v) for k, v in parsed.items()}})

        # Rationale is stored in the audit entry, not a column.
        audit(session, who.email, "risk_config.create", "risk_limit_config",
              str(config_id), {"name": body.name, "rationale": body.rationale,
                               "limits": {k: str(v) for k, v in parsed.items()}})
        session.commit()
        return {"id": str(config_id), "name": body.name,
                "limits": {k: str(v) for k, v in parsed.items()}}

    def _pending_closes(session: Session) -> set[str]:
        return {str(r) for r in session.execute(sa.text(
            "SELECT q.subject_id FROM audit_log q WHERE q.action = 'position.close_request' "
            "AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'position.closed' "
            "AND a.payload->>'request' = q.id::text)")).scalars()}

    @app.post("/api/positions/close", status_code=status.HTTP_202_ACCEPTED)
    def close_position(body: CloseRequest, who: Operator = operator,
                       session: Session = session_dep) -> dict[str, Any]:
        """Ask the run's process to close one position at market. Accepted, not done: the
        runner answers with a `position.closed` audit row within seconds."""
        latest = session.execute(sa.text(
            "SELECT p.qty, r.status FROM positions p JOIN runs r ON r.id = p.run_id "
            "WHERE p.run_id = :run AND p.asset = :a ORDER BY p.as_of DESC LIMIT 1"),
            {"run": body.run_id, "a": body.asset}).mappings().first()
        if latest is None or not latest["qty"]:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no open position")
        if latest["status"] != "running":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"run is {latest['status']}; nothing will act on it")
        subject = f"{body.run_id}:{body.asset}"
        if subject in _pending_closes(session):
            return {"subject": subject, "pending": True, "duplicate": True}
        audit(session, who.email, "position.close_request", "position", subject,
              {"run_id": str(body.run_id), "asset": body.asset, "qty": str(latest["qty"])})
        session.commit()
        return {"subject": subject, "pending": True, "duplicate": False}

    @app.get("/api/positions")
    def open_positions(who: Operator = operator,
                       session: Session = session_dep) -> dict[str, Any]:
        """Open positions of the runs still open, with stored P&L (read, never computed; §20.1).

        `positions` is a snapshot series: current = newest row per (run, asset). Zero-qty rows are
        excluded from the list but their realised P&L still counts in the totals.
        """
        rows = session.execute(sa.text(
            "SELECT DISTINCT ON (p.run_id, p.asset) "
            "  p.run_id, p.asset, p.as_of, p.qty, p.avg_entry, "
            "  p.unrealised_pnl, p.realised_pnl, p.funding_paid, "
            "  p.guide, p.trend, p.trend_source, r.params->>'label' AS book, "
            "  r.mode, r.venue, r.status, r.base_currency "
            "FROM positions p JOIN runs r ON r.id = p.run_id WHERE r.ended_at IS NULL "
            "ORDER BY p.run_id, p.asset, p.as_of DESC"
        )).mappings().all()

        def number(value: Any) -> float | None:
            return None if value is None else float(value)

        pending = _pending_closes(session)

        open_rows = [{
            "run_id": str(r["run_id"]), "asset": r["asset"],
            "as_of": r["as_of"].isoformat(), "qty": number(r["qty"]),
            "side": "LONG" if (r["qty"] or 0) > 0 else "SHORT",
            "avg_entry": number(r["avg_entry"]),
            "unrealised_pnl": number(r["unrealised_pnl"]),
            "realised_pnl": number(r["realised_pnl"]),
            "funding_paid": number(r["funding_paid"]),
            # Mode must always be displayed, never editable (§20.2).
            "mode": r["mode"], "venue": r["venue"], "status": r["status"],
            "currency": r["base_currency"],
            # PREREG-009: operator guided if trend != 5, else the algorithm; null if no guide.
            "guide": r["guide"], "trend": r["trend"], "trend_source": r["trend_source"],
            "book": r["book"],
            "close_pending": f"{r['run_id']}:{r['asset']}" in pending,
            "can_close": r["status"] == "running",
        } for r in rows if (r["qty"] or 0) != 0]

        totals = session.execute(sa.text(
            "SELECT coalesce(sum(unrealised_pnl), 0) AS unrealised, "
            "       coalesce(sum(realised_pnl), 0) AS realised, "
            "       coalesce(sum(funding_paid), 0) AS funding "
            "FROM (SELECT DISTINCT ON (p.run_id, p.asset) p.unrealised_pnl, "
            "        p.realised_pnl, p.funding_paid FROM positions p "
            "        JOIN runs r ON r.id = p.run_id WHERE r.ended_at IS NULL "
            "      ORDER BY p.run_id, p.asset, p.as_of DESC) AS latest"
        )).mappings().first()

        return {
            "positions": open_rows,
            "open_count": len(open_rows),
            # Totals over the latest snapshot of every position, closed ones included.
            "unrealised_pnl": float(totals["unrealised"]),
            "realised_pnl": float(totals["realised"]),
            "funding_paid": float(totals["funding"]),
            "runs_total": int(session.execute(
                sa.text("SELECT count(*) FROM runs")).scalar() or 0),
            "runs_live": int(session.execute(sa.text(
                "SELECT count(*) FROM runs WHERE ended_at IS NULL")).scalar() or 0),
            "risk_events": int(session.execute(
                sa.text("SELECT count(*) FROM risk_events")).scalar() or 0),
            "fills": int(session.execute(
                sa.text("SELECT count(*) FROM fills")).scalar() or 0),
            # Forward books log fills outside `fills`, so fills == 0 does not mean nothing ran.
            "snapshots": int(session.execute(
                sa.text("SELECT count(*) FROM positions")).scalar() or 0),
        }

    @app.get("/api/algorithm")
    def algorithm(who: Operator = operator,
                  session: Session = session_dep) -> dict:
        """The decision sequence with stored counts and the last scenario run (§20.1)."""
        def scalar(query: str) -> int:
            try:
                return int(session.execute(sa.text(query)).scalar() or 0)
            except Exception:
                session.rollback()
                return 0

        last = session.execute(sa.text(
            "SELECT id, ran_at, trend, sentiment, "
            "  sentiment_bucket, long_multiplier, short_multiplier, reference, "
            "  bars, baseline, conditioned, reached_step, stopped_because, source "
            "FROM scenario_runs ORDER BY ran_at DESC, id DESC LIMIT 1"
        )).mappings().first()

        return {
            "phases": ALGORITHM,
            "observed": {
                "trials": scalar("SELECT coalesce(sum(executed_trials),0) FROM studies"),
                "experiments": scalar("SELECT count(*) FROM experiments"),
                "verdicts": scalar("SELECT count(*) FROM validation_reports"),
                "passed": scalar(
                    "SELECT count(*) FROM validation_reports WHERE verdict='pass'"),
                "scenario_runs": scalar("SELECT count(*) FROM scenario_runs"),
                # Execution runs (steps 8-13), distinct from scenario runs.
                "runs": scalar("SELECT count(*) FROM runs"),
                "risk_events": scalar("SELECT count(*) FROM risk_events"),
            },
            # Declared by the scenario runner, not assumed by the screen.
            "scenario_steps": {
                "executed": list(scenario_run.EXECUTED_STEPS),
                "substituted": list(scenario_run.SUBSTITUTED_STEPS),
                "gate": scenario_run.GATE_STEP,
            },
            "last_run": None if last is None else {
                "id": str(last["id"]), "at": last["ran_at"].isoformat(),
                "trend": last["trend"],
                "direction": trend_direction(last["trend"]),
                "sentiment": last["sentiment"],
                "sentiment_bucket": last["sentiment_bucket"],
                "long_multiplier": float(last["long_multiplier"]),
                "short_multiplier": float(last["short_multiplier"]),
                "reference": last["reference"], "bars": last["bars"],
                "baseline": last["baseline"], "conditioned": last["conditioned"],
                "reached_step": last["reached_step"],
                "stopped_because": last["stopped_because"],
                "source": last["source"],
            },
        }

    @app.get("/risk-limits", include_in_schema=False)
    def risk_limits_screen() -> FileResponse:
        """Risk limits screen; limits are versioned configs, not editable settings (§12.4)."""
        return FileResponse(STATIC / "risk_limits.html")

    @app.get("/algorithm", include_in_schema=False)
    def algorithm_screen() -> FileResponse:
        return FileResponse(STATIC / "algorithm.html")

    @app.get("/pipeline", include_in_schema=False)
    def old_pipeline() -> RedirectResponse:
        """Legacy path; redirects to /algorithm."""
        return RedirectResponse("/algorithm", status_code=308)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "computes_metrics": False, "can_start_live_trading": False}

    return app
