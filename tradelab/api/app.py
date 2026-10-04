"""The operator interface (FastAPI). Plan.md §20.

Invariants: it computes no figure (reads only worker-persisted values; time series belong to
Grafana, §20.1), and it cannot start live trading (no mode-switch route, no venue credential).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
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
from tradelab.config import Settings

STATIC = Path(__file__).parent / "static"

WRITABLE = frozenset({"approval", "transition_request"})


class SignIn(BaseModel):
    email: str
    password: str


class SettingRequest(BaseModel):
    """A change to an operator setting. Plan.md §12.4, §20.4. `rationale` is required."""

    value: dict[str, Any]
    rationale: str = Field(min_length=8)


class RiskConfigRequest(BaseModel):
    """A new risk limit configuration. Plan.md §14.1, §12.4, §20.

    Created, never edited (§14.1: runs reference a version). `extra="forbid"`.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9 _.-]+$")
    limits: dict[str, Any] = Field(min_length=1)
    rationale: str = Field(min_length=10, max_length=1000)


class CloseRequest(BaseModel):
    """The operator's manual exit of one open position of a forward run."""

    model_config = {"extra": "forbid"}

    run_id: uuid.UUID
    asset: str = Field(min_length=1, max_length=40)


class LadderSellRequest(BaseModel):
    """The operator's manual sale of one part of the ladder, named by when it was bought."""

    model_config = {"extra": "forbid"}

    part: datetime


class ApprovalRequest(BaseModel):
    subject_kind: str = Field(pattern="^(strategy|capital_tier|gate_change)$")
    subject_id: uuid.UUID
    decision: str = Field(pattern="^(approve|reject)$")
    gate_config_id: uuid.UUID | None = None
    notes: str | None = None


FORWARD = Path(__file__).resolve().parents[2] / "data/forward"
LADDER_DIRS = {"sim": "btc-ladder", "demo": "btc-ladder-demo", "live": "btc-ladder-live"}


PER_PAGE = 100


def _page(page: int) -> tuple[int, int]:
    """(limit, offset) for a 1-based page."""
    return PER_PAGE, (max(1, page) - 1) * PER_PAGE


def _plain(value: Any) -> str:
    """A limit as a person writes it: 0.25, 10000, 60. The database pads NUMERIC columns
    with zeros (0.25000000), which reads as noise."""
    number = Decimal(str(value))
    text = format(number.normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".")


def create_app(engine: sa.Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or sa.create_engine(settings.app_database_url)
    ladder_status = FORWARD / LADDER_DIRS[settings.app_book] / "status.json"
    make_session = sessionmaker(engine, expire_on_commit=False)

    secret = os.environ.get("TRADELAB_SESSION_SECRET", "")
    if len(secret) < 32:
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

    session_dep = Depends(db)

    def audit(session: Session, actor: str, action: str, kind: str,
              subject: str, payload: dict | None = None) -> None:
        session.execute(sa.text(
            "INSERT INTO audit_log (actor, action, subject_kind, subject_id, payload) "
            "VALUES (:a, :ac, :k, :s, CAST(:p AS jsonb))"
        ), {"a": actor, "ac": action, "k": kind, "s": subject,
            "p": None if payload is None else json.dumps(payload)})


    @app.post("/api/session")
    def sign_in(body: SignIn, request: Request, response: Response,
                session: Session = session_dep) -> dict:
        row = session.execute(
            sa.text("SELECT email, role FROM users WHERE email = :e"), {"e": body.email}
        ).first()
        expected = os.environ.get("TRADELAB_ADMIN_HASH", "")
        salt = os.environ.get("TRADELAB_ADMIN_SALT", "")
        if row is None or not expected or not verify_password(body.password, salt, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        response.set_cookie(
            SESSION_COOKIE, sessions.issue(row.email), httponly=True, samesite="strict",
            max_age=int(SESSION_MAX_AGE.total_seconds()),
            secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
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
    def audit_trail(response: Response, page: int = 1, who: Operator = operator,
                    session: Session = session_dep) -> list[dict]:
        limit, offset = _page(page)
        rows = session.execute(sa.text(
            "SELECT actor, action, subject_kind, subject_id, occurred_at "
            "FROM audit_log ORDER BY occurred_at DESC, id DESC LIMIT :l OFFSET :o"
        ), {"l": limit, "o": offset}).mappings().all()
        response.headers["X-Total-Count"] = str(session.execute(
            sa.text("SELECT count(*) FROM audit_log")).scalar())
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


    EDITABLE = {
        "btc_ladder": "The BTC ladder's parts, step, correction and pullback. Saving a change "
                      "ends the current run and starts a new one under the new numbers.",
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
        }

    @app.get("/api/settings/{key}/history")
    def setting_history(key: str, response: Response, page: int = 1,
                        who: Operator = operator,
                        session: Session = session_dep) -> list[dict]:
        limit, offset = _page(page)
        rows = session.execute(sa.text(
            "SELECT s.value, s.rationale, s.valid_from, u.email AS changed_by "
            "FROM app_settings s LEFT JOIN users u ON u.id = s.changed_by "
            "WHERE s.key = :k ORDER BY s.valid_from DESC LIMIT :l OFFSET :o"
        ), {"k": key, "l": limit, "o": offset}).mappings().all()
        response.headers["X-Total-Count"] = str(session.execute(sa.text(
            "SELECT count(*) FROM app_settings WHERE key = :k"), {"k": key}).scalar())
        return [dict(r) for r in rows]

    @app.post("/api/settings/{key}", status_code=status.HTTP_201_CREATED)
    def record_setting(key: str, body: SettingRequest, who: Operator = operator,
                       session: Session = session_dep) -> dict[str, Any]:
        """Append a new setting value (never update in place); non-editable keys get a 403."""
        if key not in EDITABLE:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{key!r} is not an operator setting (§12.4); only the BTC ladder's "
                f"numbers are edited here.",
            )
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


    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        """The app starts at /home; a bare domain lands there instead of on a 404."""
        return RedirectResponse("/home", status_code=302)

    @app.get("/trade_lab", include_in_schema=False)
    def old_home() -> RedirectResponse:
        """Legacy path; redirects to /home."""
        return RedirectResponse("/home", status_code=308)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
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

    @app.get("/audit", include_in_schema=False)
    def audit_screen() -> FileResponse:
        return FileResponse(STATIC / "audit.html")

    @app.get("/api/risk-limits")
    def risk_limits(history_page: int = 1, events_page: int = 1, who: Operator = operator,
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
        stored = session.execute(sa.text(
            "SELECT * FROM risk_limit_configs ORDER BY valid_from DESC LIMIT 1"
        )).mappings().first()

        halting = {b.value for b in HALTING}
        human = {b.value for b in REQUIRES_HUMAN}

        limits = []
        for field in dataclasses.fields(RiskLimits):
            name = field.name
            breach = name if name in halting or name in human else (
                "price_deviation" if name.startswith("max_price_deviation") else name)
            limits.append({
                "name": name,
                "value": _plain(stored[name]) if stored is not None and name in stored
                         else _plain(getattr(defaults, name)),
                "default": _plain(getattr(defaults, name)),
                "stops": STOPS.get(name, ""),
                "halts": breach in halting,
                "needs_a_person": breach in human,
                "low": str(BOUNDS[name].low), "high": str(BOUNDS[name].high),
                "whole": BOUNDS[name].whole, "bound": BOUNDS[name].describes,
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
            "FROM risk_limit_configs c ORDER BY c.valid_from DESC LIMIT :l OFFSET :o"
        ), dict(zip(("l", "o"), _page(history_page), strict=True))).mappings().all()

        events = session.execute(sa.text(
            "SELECT rule, observed, limit_value, action, occurred_at, run_id "
            "FROM risk_events ORDER BY occurred_at DESC LIMIT :l OFFSET :o"
        ), dict(zip(("l", "o"), _page(events_page), strict=True))).mappings().all()

        return {
            "source": "stored configuration" if stored is not None
                      else "engine defaults, no configuration stored",
            "config_name": None if stored is None else stored["name"],
            "valid_from": None if stored is None
                          else stored["valid_from"].isoformat(),
            "limits": limits,
            "halting": sorted(halting),
            "needs_a_person": sorted(human),
            "breach_kinds": sorted(b.value for b in Breach),
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
            "event_count": int(session.execute(
                sa.text("SELECT count(*) FROM risk_events")).scalar() or 0),
            "history_total": int(session.execute(
                sa.text("SELECT count(*) FROM risk_limit_configs")).scalar() or 0),
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
            "mode": r["mode"], "venue": r["venue"], "status": r["status"],
            "currency": r["base_currency"],
            "guide": r["guide"], "trend": r["trend"], "trend_source": r["trend_source"],
            "book": r["book"],
            "close_pending": f"{r['run_id']}:{r['asset']}" in pending,
            "can_close": r["status"] == "running",
        } for r in rows if (r["qty"] or 0) != 0]

        by_mode = session.execute(sa.text(
            "SELECT mode, coalesce(sum(unrealised_pnl), 0) AS unrealised, "
            "       coalesce(sum(realised_pnl), 0) AS realised, "
            "       coalesce(sum(funding_paid), 0) AS funding "
            "FROM (SELECT DISTINCT ON (p.run_id, p.asset) r.mode, p.unrealised_pnl, "
            "        p.realised_pnl, p.funding_paid FROM positions p "
            "        JOIN runs r ON r.id = p.run_id WHERE r.ended_at IS NULL "
            "      ORDER BY p.run_id, p.asset, p.as_of DESC) AS latest "
            "GROUP BY mode ORDER BY mode"
        )).mappings().all()

        return {
            "positions": open_rows,
            "open_count": len(open_rows),
            "totals": {r["mode"]: {"unrealised_pnl": float(r["unrealised"]),
                                   "realised_pnl": float(r["realised"]),
                                   "funding_paid": float(r["funding"])} for r in by_mode},
            "runs_total": int(session.execute(
                sa.text("SELECT count(*) FROM runs")).scalar() or 0),
            "runs_live": int(session.execute(sa.text(
                "SELECT count(*) FROM runs WHERE ended_at IS NULL")).scalar() or 0),
            "risk_events": int(session.execute(
                sa.text("SELECT count(*) FROM risk_events")).scalar() or 0),
            "fills": int(session.execute(
                sa.text("SELECT count(*) FROM fills")).scalar() or 0),
            "snapshots": int(session.execute(
                sa.text("SELECT count(*) FROM positions")).scalar() or 0),
        }

    def _pending_manual(session: Session, run_id: str) -> list[dict[str, Any]]:
        return [{"action": a, "part": p} for a, p in session.execute(sa.text(
            "SELECT q.action, q.payload->>'part' FROM audit_log q "
            "WHERE q.action IN ('ladder.buy_request', 'ladder.sell_request') "
            "AND q.payload->>'run_id' = :run "
            "AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'ladder.answered' "
            "AND a.payload->>'request' = q.id::text) ORDER BY q.id"), {"run": run_id}).all()]

    @app.get("/api/ladder")
    def ladder(who: Operator = operator, session: Session = session_dep) -> dict[str, Any]:
        """The BTC ladder as its book last published it: read, never computed (§20.1), with
        the manual buys and sells it has not answered yet."""
        if not ladder_status.exists():
            return {"available": False, "book": settings.app_book}
        status_ = json.loads(ladder_status.read_text())
        age = (datetime.now(UTC) - datetime.fromisoformat(status_["as_of"])).total_seconds()
        return {"available": True, "age_seconds": round(age), "book": settings.app_book,
                **status_, "pending": _pending_manual(session, status_["run_id"])}

    def _ladder_run(session: Session) -> dict[str, Any]:
        """The ladder's published status, if its run is the one running."""
        if not ladder_status.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "the ladder has not published")
        status_ = json.loads(ladder_status.read_text())
        state = session.execute(sa.text("SELECT status FROM runs WHERE id = :r"),
                                {"r": status_["run_id"]}).scalar()
        if state != "running":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"the ladder's run is {state}; nothing will act on it")
        return status_

    @app.post("/api/ladder/buy", status_code=status.HTTP_202_ACCEPTED)
    def ladder_buy(who: Operator = operator, session: Session = session_dep) -> dict[str, Any]:
        """Ask the ladder to buy one part at market now, marked manual. Accepted, not done:
        the book answers with a `ladder.answered` audit row within seconds."""
        status_ = _ladder_run(session)
        if status_["full"]:
            raise HTTPException(status.HTTP_409_CONFLICT, "all parts are held")
        pending = _pending_manual(session, status_["run_id"])
        if any(p["action"] == "ladder.buy_request" for p in pending):
            return {"pending": True, "duplicate": True}
        audit(session, who.email, "ladder.buy_request", "ladder", status_["run_id"],
              {"run_id": status_["run_id"], "manual": True})
        session.commit()
        return {"pending": True, "duplicate": False}

    @app.post("/api/ladder/sell", status_code=status.HTTP_202_ACCEPTED)
    def ladder_sell(body: LadderSellRequest, who: Operator = operator,
                    session: Session = session_dep) -> dict[str, Any]:
        """Ask the ladder to sell one part at market now, marked manual, even at a loss."""
        status_ = _ladder_run(session)
        part = next((p for p in status_["parts"]
                     if datetime.fromisoformat(p["opened"]) == body.part), None)
        if part is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such part is held")
        pending = _pending_manual(session, status_["run_id"])
        if any(p["part"] == part["opened"] for p in pending):
            return {"pending": True, "duplicate": True}
        audit(session, who.email, "ladder.sell_request", "ladder", status_["run_id"],
              {"run_id": status_["run_id"], "part": part["opened"], "manual": True,
               "gain_shown": part["gain"]})
        session.commit()
        return {"pending": True, "duplicate": False}

    @app.get("/risk-limits", include_in_schema=False)
    def risk_limits_screen() -> FileResponse:
        """Risk limits screen; limits are versioned configs, not editable settings (§12.4)."""
        return FileResponse(STATIC / "risk_limits.html")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "computes_metrics": False, "can_start_live_trading": False}

    return app
