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

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
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


class AnnotationRequest(BaseModel):
    """A human's reading of an event. Plan.md §20.5.

    `rating` is in **sigma of the asset's pre-event volatility**, signed. Not
    percent, not a 1-10 scale. §11.3 normalises every measured reaction to
    sigma, so a rating in sigma can be scored against what actually happened —
    which is what makes the annotator's calibration knowable, and an input
    nobody can score is the one nobody ever corrects.

    `available_at` is accepted only for corrections. A judgement is available
    when it is made, and the database refuses a backdated one regardless of what
    is sent here.
    """

    kind: str = Field(pattern="^(correction|judgement)$")
    rating: float | None = Field(default=None, ge=-10.0, le=10.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)
    available_at: datetime | None = None
    note: str | None = None


class ConsensusRequest(BaseModel):
    """What the market expected, entered where no feed supplies it. §20.6.

    No `consensus_at` field, deliberately. It is set to now by the handler and
    the database refuses any row where it does not precede `scheduled_for`, so a
    consensus can only ever be recorded for a release that has not happened.
    """

    consensus: float
    note: str | None = None


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
            "p": None if payload is None else json.dumps(payload)})

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

    # -- events -------------------------------------------------------------

    @app.get("/api/events")
    def events(who: Operator = operator, session: Session = session_dep,
               event_type: str | None = None, limit: int = 100) -> list[dict]:
        """Newest first, with the assets and the annotation count.

        `rated` is what the screen sorts by in practice: the useful work is the
        events nobody has read yet, and an interface that hides which those are
        makes the operator find them by memory.
        """
        clause = "WHERE e.event_type = :t" if event_type else ""
        rows = session.execute(sa.text(
            f"SELECT e.id, e.event_type, e.shape, e.first_seen_at, e.resolved_at, "
            f"       e.code_hash, "
            f"       COALESCE(array_agg(DISTINCT a.asset) FILTER "
            f"                (WHERE a.asset IS NOT NULL), '{{}}') AS assets, "
            f"       count(DISTINCT n.id) AS annotations "
            f"FROM events e "
            f"LEFT JOIN event_assets a ON a.event_id = e.id "
            f"LEFT JOIN event_annotations n ON n.event_id = e.id "
            f"{clause} "
            f"GROUP BY e.id ORDER BY e.first_seen_at DESC LIMIT :l"
        ), {"t": event_type, "l": min(limit, 500)}).mappings().all()
        return [dict(r) | {"rated": r["annotations"] > 0} for r in rows]

    @app.get("/api/events/{event_id}")
    def event_detail(event_id: uuid.UUID, who: Operator = operator,
                     session: Session = session_dep) -> dict:
        row = session.execute(sa.text(
            "SELECT id, event_type, shape, first_seen_at, resolved_at, code_hash "
            "FROM events WHERE id = :i"), {"i": event_id}).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")
        assets = session.execute(sa.text(
            "SELECT asset, expected_direction, expected_magnitude, confidence "
            "FROM event_assets WHERE event_id = :i ORDER BY asset"),
            {"i": event_id}).mappings().all()
        annotations = session.execute(sa.text(
            "SELECT n.id, n.kind, n.rating, n.confidence, n.payload, n.available_at, "
            "       n.created_at, u.email AS author "
            "FROM event_annotations n JOIN users u ON u.id = n.author "
            "WHERE n.event_id = :i ORDER BY n.created_at DESC"),
            {"i": event_id}).mappings().all()
        schedule = session.execute(sa.text(
            "SELECT revision, available_at, scheduled_for, confidence, source "
            "FROM schedule_revisions WHERE event_id = :i ORDER BY revision"),
            {"i": event_id}).mappings().all()
        return {
            **dict(row),
            "assets": [dict(a) for a in assets],
            "annotations": [dict(a) for a in annotations],
            # Every date the event was ever believed to have, not just the last.
            # Showing only the current one is how a reader reconstructs a
            # timeline the market never had (§11.6).
            "schedule_revisions": [dict(s) for s in schedule],
        }

    @app.post("/api/events/{event_id}/annotations",
              status_code=status.HTTP_201_CREATED)
    def annotate(event_id: uuid.UUID, body: AnnotationRequest,
                 who: Operator = operator,
                 session: Session = session_dep) -> dict[str, Any]:
        """Record a reading. Plan.md §20.5.

        This writes to the registry and therefore belongs to the application
        rather than to Grafana — a rating needs an actor and a time, which is
        the whole reason this surface requires a session.

        It does not break §20.1's rule that the application computes nothing: a
        rating is not a computed metric, it is an **observation recorded with
        the time it was made**. The system still calculates nothing here. It
        records what someone asserted and when.
        """
        user = session.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": who.email}
        ).first()
        if user is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no such operator")

        exists = session.execute(
            sa.text("SELECT 1 FROM events WHERE id = :i"), {"i": event_id}).first()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")

        # A judgement is available when it is made. Accepting a client-supplied
        # time here would let the caller choose; the database refuses a
        # backdated judgement anyway, so this only decides whether the refusal
        # is a 400 or a 500.
        available_at = (
            datetime.now(UTC) if body.kind == "judgement"
            else (body.available_at or datetime.now(UTC))
        )
        annotation_id = uuid.uuid4()
        payload = dict(body.payload)
        if body.note:
            payload["note"] = body.note

        try:
            session.execute(sa.text(
                "INSERT INTO event_annotations (id, event_id, kind, available_at, "
                "payload, rating, confidence, author) "
                "VALUES (:i, :e, :k, :a, CAST(:p AS jsonb), :r, :c, :u)"
            ), {"i": annotation_id, "e": event_id, "k": body.kind,
                "a": available_at, "p": json.dumps(payload), "r": body.rating,
                "c": body.confidence, "u": user.id})
        except sa.exc.DBAPIError as error:
            session.rollback()
            message = str(getattr(error, "orig", error))
            if "ck_judgement_is_not_backdated" in message:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "a judgement cannot be dated before it was written (§20.5)",
                ) from error
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                message.split("\n")[0]) from error

        audit(session, who.email, f"annotation.{body.kind}", "event", str(event_id),
              {"rating": body.rating, "confidence": body.confidence})
        session.commit()
        return {"id": str(annotation_id), "available_at": available_at.isoformat()}

    # -- macro --------------------------------------------------------------

    @app.get("/api/macro/series")
    def macro_series(who: Operator = operator,
                     session: Session = session_dep) -> list[dict]:
        """The catalogue with freshness, which is the column that matters.

        A stale macro feed fails silently — the number is simply old, and old
        numbers look exactly like current ones. So the age of the newest
        observation is returned on every row rather than left to be noticed.
        """
        rows = session.execute(sa.text(
            "SELECT s.code, s.name, s.kind, s.source, s.has_vintages, s.is_revised, "
            "       s.session_close, s.publish_lag_s, "
            "       max(r.scheduled_for) AS latest, "
            "       count(r.id) AS observations, "
            "       EXTRACT(EPOCH FROM (now() - max(r.scheduled_for)))/86400 AS days_old "
            "FROM macro_series s LEFT JOIN macro_releases r ON r.series = s.code "
            "GROUP BY s.code ORDER BY s.kind, s.code"
        )).mappings().all()
        return [
            dict(r) | {
                "days_old": float(r["days_old"]) if r["days_old"] is not None else None,
                # A revised series with no vintages cannot be read as it was
                # known at the time, and a study using it is answering a
                # different question (§4.7). Surfaced, not buried in a column.
                "point_in_time_safe": bool(r["has_vintages"]) or not r["is_revised"],
            }
            for r in rows
        ]

    @app.get("/api/macro/releases")
    def macro_releases(who: Operator = operator, session: Session = session_dep,
                       series: str | None = None, limit: int = 100) -> list[dict]:
        clause = "WHERE series = :s" if series else ""
        rows = session.execute(sa.text(
            f"SELECT id, series, scheduled_for, consensus, consensus_at, actual, "
            f"       actual_at, available_at, surprise_sigma "
            f"FROM macro_releases {clause} "
            f"ORDER BY scheduled_for DESC LIMIT :l"
        ), {"s": series, "l": min(limit, 500)}).mappings().all()
        return [dict(r) for r in rows]

    @app.get("/api/macro/upcoming")
    def macro_upcoming(who: Operator = operator,
                       session: Session = session_dep) -> list[dict]:
        """Releases still ahead, which are the only ones a consensus can attach to.

        The anticipation window of §11.6 made visible: for a scheduled event the
        tradeable object is the approach, not the arrival.
        """
        rows = session.execute(sa.text(
            "SELECT id, series, scheduled_for, consensus, consensus_at, "
            "       EXTRACT(EPOCH FROM (scheduled_for - now()))/86400 AS days_ahead "
            "FROM macro_releases WHERE scheduled_for > now() "
            "ORDER BY scheduled_for LIMIT 200"
        )).mappings().all()
        return [dict(r) | {"days_ahead": float(r["days_ahead"])} for r in rows]

    @app.post("/api/macro/releases/{release_id}/consensus")
    def record_consensus(release_id: uuid.UUID, body: ConsensusRequest,
                         who: Operator = operator,
                         session: Session = session_dep) -> dict[str, Any]:
        """Record what the market expected, before the print. §20.6.

        The whole value of macro as an event class is that expectations existed
        *before* the release, so surprise is a number rather than a story told
        afterwards. A consensus entered once the actual is known is that story,
        and it would make every macro surprise look predictive.

        Refused by the database, not here — the third place this system declines
        to trust a control that can be clicked past.
        """
        try:
            updated = session.execute(sa.text(
                "UPDATE macro_releases SET consensus = :c, consensus_at = now() "
                "WHERE id = :i RETURNING series, scheduled_for"
            ), {"c": body.consensus, "i": release_id}).mappings().first()
        except sa.exc.DBAPIError as error:
            session.rollback()
            message = str(getattr(error, "orig", error))
            if "ck_consensus_precedes_the_release" in message:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "this release has already happened. A consensus recorded "
                    "afterwards is not a consensus, it is a memory of one (§20.6).",
                ) from error
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                message.split("\n")[0]) from error

        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such release")

        audit(session, who.email, "macro.consensus", "macro_release", str(release_id),
              {"consensus": body.consensus, "note": body.note})
        session.commit()
        return {"id": str(release_id), "series": updated["series"]}

    @app.get("/macro", include_in_schema=False)
    def macro_screen() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "macro.html")

    # -- the screen ---------------------------------------------------------

    @app.get("/login", include_in_schema=False)
    def login_screen() -> FileResponse:
        """The sign-in page. Unauthenticated screens send visitors here.

        It carries a `next` parameter so a deep link survives the detour, and
        honours it only when it is a path on this origin — an open redirect on a
        login page is how a session gets handed to somewhere else.
        """
        return FileResponse(Path(__file__).parent / "static" / "login.html")

    @app.get("/trade_lab", include_in_schema=False)
    def home() -> FileResponse:
        """The entry point. Screens hang off it rather than being reached by
        knowing their paths."""
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/news", include_in_schema=False)
    def news_screen() -> FileResponse:
        """Served by the API rather than built as a separate front end, for now.

        §20 describes fifteen screens and a React application; this is the one
        that writes data the research depends on, so it exists before the build
        tooling does. The markup calls the same endpoints a React client would,
        so replacing it later changes the view and not the contract.
        """
        return FileResponse(Path(__file__).parent / "static" / "news.html")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "computes_metrics": False, "can_start_live_trading": False}

    return app
