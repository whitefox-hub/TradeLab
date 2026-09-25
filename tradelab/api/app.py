"""The operator interface. Plan.md §20.

Two rules shape every route here, and both are refusals.

**It computes nothing.** Every figure returned was computed by a worker and
persisted. A number that exists only on screen is a number nobody can reproduce,
so this application reads the registry and reports what is in it — it does not
calculate a Sharpe, a drawdown or a utilisation. Time series belong to Grafana;
this surface exists because a decision needs an actor and a timestamp.

**It cannot start live trading.** There is no route that switches a strategy's
mode, and there is no venue credential in this process. The interface *requests*
a transition; the gate evaluates it and the executor acts. Mode is displayed,
never edited, and displayed prominently, because mistaking simulator for live is
the error this design is most concerned with.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Response, status
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
from tradelab.config import Settings

#: Routes that write. Everything else is a read, and the list is short on
#: purpose: §20.1 allows this surface to write approvals and lifecycle
#: transitions, and nothing else.
WRITABLE = frozenset({"approval", "transition_request"})


class SignIn(BaseModel):
    email: str
    password: str


class ApprovalRequest(BaseModel):
    subject_kind: str = Field(pattern="^(strategy|capital_tier|gate_change)$")
    subject_id: uuid.UUID
    decision: str = Field(pattern="^(approve|reject)$")
    gate_config_id: uuid.UUID | None = None
    notes: str | None = None


def create_app(engine: sa.Engine | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = engine or sa.create_engine(settings.database_url)
    make_session = sessionmaker(engine, expire_on_commit=False)

    secret = os.environ.get("TRADELAB_SESSION_SECRET", "")
    if len(secret) < 32:
        # Generated rather than defaulted to a constant: a shared default secret
        # is worse than no session at all, because it looks like security.
        from tradelab.api.security import new_secret

        secret = new_secret()
    sessions = Sessions(secret)
    operator = operator_dependency(sessions)

    app = FastAPI(title="TradeLab", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def db():
        with make_session() as session:
            yield session

    # Written as a default rather than as an `Annotated` alias on purpose. This
    # module uses `from __future__ import annotations`, which turns every
    # annotation into a string, and a local alias is not resolvable from the
    # module namespace — FastAPI read `session` as a query parameter and every
    # route 422'd. The 401 tests still passed, because the operator dependency
    # raised first, so the suite was green against a broken API.
    session_dep = Depends(db)

    def audit(session: Session, actor: str, action: str, kind: str,
              subject: str, payload: dict | None = None) -> None:
        session.execute(sa.text(
            "INSERT INTO audit_log (actor, action, subject_kind, subject_id, payload) "
            "VALUES (:a, :ac, :k, :s, CAST(:p AS jsonb))"
        ), {"a": actor, "ac": action, "k": kind, "s": subject,
            "p": None if payload is None else __import__("json").dumps(payload)})

    # -- session ------------------------------------------------------------

    @app.post("/api/session")
    def sign_in(body: SignIn, response: Response, session: Session = session_dep) -> dict:
        row = session.execute(
            sa.text("SELECT email, role FROM users WHERE email = :e"), {"e": body.email}
        ).first()
        expected = os.environ.get("TRADELAB_ADMIN_HASH", "")
        salt = os.environ.get("TRADELAB_ADMIN_SALT", "")
        if row is None or not expected or not verify_password(body.password, salt, expected):
            # One message for both failures. Distinguishing them tells an
            # attacker which half they got right.
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
            "SELECT id, name, status, created_at FROM strategies ORDER BY created_at DESC"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/runs")
    def runs(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        """Mode is returned on every row and is never accepted on any.

        There is no PATCH for this resource and no field that would change it.
        §20.2: production is reached through the capital ramp, not entered.
        """
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
            "SELECT id, kind, severity, asset, period_start, period_end, detail "
            "FROM data_quality_issues WHERE resolved_at IS NULL "
            "ORDER BY severity DESC, period_start DESC LIMIT 500"
        )).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/gates")
    def gates(who: Operator = operator, session: Session = session_dep) -> list[dict]:
        """Every gate with its age, because the age is what decides whether an
        approval may cite it (§20.3)."""
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
        """Record a decision. The gate decides whether it can be acted on.

        The 24-hour cooling-off is enforced by a database trigger, not here.
        Validating it in this handler as well would be duplicate logic that can
        drift; the API's job is to report the refusal legibly, not to be the
        second place the rule lives.
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

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "computes_metrics": False, "can_start_live_trading": False}

    return app
