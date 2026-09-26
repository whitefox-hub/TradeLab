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
from datetime import UTC, datetime, timedelta
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
from tradelab.config import Settings
from tradelab.news.expiry import resolve as resolve_expiry
from tradelab.news.strength import SIGMA_BY_STRENGTH, describe, to_sigma
from tradelab.news.templates import CATALOGUE as TEMPLATES

#: Shared chrome and the screens live here. Mounted rather than served per file,
#: so adding a stylesheet does not mean adding a route.
STATIC = Path(__file__).parent / "static"

#: Routes that write. Everything else is a read, and the list is short on
#: purpose: §20.1 allows this surface to write approvals and lifecycle
#: transitions, and nothing else.
WRITABLE = frozenset({"approval", "transition_request"})


class SignIn(BaseModel):
    email: str
    password: str


class NewEventRequest(BaseModel):
    """An event the operator adds by hand. Plan.md §20.5.

    The extraction will misclassify, the taxonomy will miss things, and some
    events carry judgement no classifier can supply — a hack, a regulation, a
    war. So the screen can add one, and what it adds is marked as hand-entered
    rather than blended into the collected corpus, because a study that cannot
    separate them cannot tell a measurement from an opinion.
    """

    event_type: str = Field(min_length=3, max_length=48)
    shape: str = Field(default="shock",
                       pattern="^(shock|schedule|process|regime_shift)$")
    assets: list[str] = Field(default_factory=list, max_length=40)
    #: -1 bearish, 0 unclear, +1 bullish, for the named assets.
    #:
    #: Recorded because the expected-reaction model of §11.10 needs it as an
    #: input. It is **not** the signal: what is tradeable is the residual,
    #: observed minus expected. Positive news that should have moved two sigma
    #: and moved a fifth of one is bearish information, and lukewarm news that
    #: moved four is
    #: bullish — so a system trading sign directly trades the part of the
    #: reaction everyone else has already priced.
    direction: int = Field(default=0, ge=-1, le=1)
    #: When it became public, which is not when it is being typed. Backdating an
    #: *event* is legitimate — it happened when it happened. Backdating a
    #: *judgement* about it is not, and that is refused elsewhere (§20.5).
    happened_at: datetime | None = None
    scheduled_for: datetime | None = None
    title: str = Field(min_length=3, max_length=400)
    #: Which catalogue entry this came from, if any.
    #:
    #: Recorded so a later calibration can tell a rating the operator chose from
    #: one left at a suggestion. §20.5 stores ratings in sigma so the
    #: *annotator's* calibration becomes knowable, and a curve that cannot
    #: separate those is measuring the template file.
    template: str | None = None


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
    #: 1-10, with direction carrying the sign. The operator never types a
    #: negative number; what is stored is still signed sigma, because §11.3
    #: normalises measured reactions to sigma and a rating in any other unit
    #: cannot be scored against one.
    strength: int | None = Field(default=None, ge=1, le=10)
    direction: int = Field(default=0, ge=-1, le=1)
    #: True when the operator left the rating exactly where a template put it.
    #: Not a lesser judgement, but a distinguishable one — a calibration built
    #: from unmoved suggestions measures the catalogue.
    from_template: str | None = None
    #: Accepted for callers that already work in sigma. When `strength` is given
    #: it wins, so the two cannot disagree in a stored row.
    rating: float | None = Field(default=None, ge=-10.0, le=10.0)

    @property
    def signed_sigma(self) -> float | None:
        if self.strength is not None:
            return to_sigma(self.strength, self.direction)
        return self.rating
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


class SettingRequest(BaseModel):
    """A change to an operator setting. Plan.md §12.4, §20.4.

    `rationale` is required and not a formality. §20.3 makes the same demand of
    gate thresholds, for the same reason: a change with no stated reason is one
    nobody can review, and six months later the audit log can only say what
    happened if the reason is next to it.
    """

    value: dict[str, Any]
    rationale: str = Field(min_length=8)


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
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def no_stale_chrome(request, call_next):
        """Keep the browser from serving an old menu.

        The sidebar lives in one file, so a cached app.js means a screen that
        exists is missing from the navigation — which reads as the screen not
        having been built. Revalidation costs one conditional request and
        removes a class of confusion that is indistinguishable from a bug.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

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
        """One strategy with the studies and verdicts issued under it.

        Parameters are displayed and never editable: §12.4 puts them in the
        column that creates a new strategy, and `runs.params` is immutable in
        the schema so an edit would produce a different run rather than change
        this one.
        """
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
        # Reached through experiments, because a verdict belongs to a
        # configuration rather than to the strategy as a whole.
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
            # Every verdict carries the bar it cleared, because a threshold that
            # was lowered travels with the results issued under it (§20.3).
            "validation_reports": [dict(r) for r in reports],
        }

    @app.get("/api/experiments")
    def experiments(who: Operator = operator, session: Session = session_dep,
                    limit: int = 200) -> list[dict]:
        """Tier B and above only.

        Tier A is disposable mass screening written to Parquet, never here
        (§5.7): a campaign produces millions of rows and putting them in the
        registry would make it unusable to query.
        """
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
               event_type: str | None = None, status: str | None = None,
               limit: int = 300) -> list[dict]:
        """Every event with whether it is still information, and why.

        `status` filters to `active` or `expired`. The distinction is §11.8's and
        it is not "recent": a delisting scheduled three months out is old news
        and entirely live, while a listing from this morning can already be
        spent. Sorting by age would put those the wrong way round.
        """
        clause = "WHERE e.event_type = :t" if event_type else ""
        rows = session.execute(sa.text(
            f"SELECT e.id, e.event_type, e.shape, e.first_seen_at, e.resolved_at, "
            f"       e.headline, e.code_hash, e.cluster_method, "
            f"       COALESCE(array_agg(DISTINCT a.asset) FILTER "
            f"                (WHERE a.asset IS NOT NULL), '{{}}') AS assets, "
            f"       max(a.expected_direction) AS direction, "
            f"       count(DISTINCT n.id) FILTER (WHERE n.kind = 'judgement') "
            f"         AS annotations, "
            f"       min(sr.scheduled_for) FILTER (WHERE sr.scheduled_for > now()) "
            f"         AS scheduled_for, "
            f"       (SELECT pt.state FROM process_transitions pt "
            f"         WHERE pt.event_id = e.id "
            f"         ORDER BY pt.sequence DESC LIMIT 1) AS latest_state, "
            f"       (SELECT (an.payload->>'reactivated_until')::timestamptz "
            f"         FROM event_annotations an WHERE an.event_id = e.id "
            f"          AND an.payload ? 'reactivated_until' "
            f"         ORDER BY an.created_at DESC LIMIT 1) AS reactivated_until "
            f"FROM events e "
            f"LEFT JOIN event_assets a ON a.event_id = e.id "
            f"LEFT JOIN event_annotations n ON n.event_id = e.id "
            f"LEFT JOIN schedule_revisions sr ON sr.event_id = e.id "
            f"{clause} "
            f"GROUP BY e.id ORDER BY e.first_seen_at DESC LIMIT :l"
        ), {"t": event_type, "l": min(limit, 1000)}).mappings().all()

        out = []
        for row in rows:
            expiry = resolve_expiry(
                event_type=row["event_type"], shape=row["shape"],
                first_seen_at=row["first_seen_at"], resolved_at=row["resolved_at"],
                scheduled_for=row["scheduled_for"], latest_state=row["latest_state"],
                reactivated_until=row["reactivated_until"],
            )
            if status == "active" and not expiry.active:
                continue
            if status == "expired" and expiry.active:
                continue
            out.append(dict(row) | {
                "rated": row["annotations"] > 0,
                "active": expiry.active,
                "ended_by": str(expiry.ended_by),
                "expires_at": expiry.expires_at,
                "expiry_reason": expiry.reason,
                # Flagged, so the screen can say the horizon is assumed rather
                # than measured. A default presented as a finding is worse than
                # no default (§11.5).
                "expiry_assumed": expiry.assumed,
                "reopenable": expiry.reopenable,
                "not_reopenable_because": expiry.not_reopenable_because,
                "hand_entered": str(row["cluster_method"]).startswith("manual:"),
            })
        return out

    @app.post("/api/events", status_code=status.HTTP_201_CREATED)
    def add_event(body: NewEventRequest, who: Operator = operator,
                  session: Session = session_dep) -> dict[str, Any]:
        """Add an event by hand. Plan.md §20.5.

        Marked `manual:` in `cluster_method` and stamped with the operator rather
        than a taxonomy hash, so a later study can separate what was collected
        from what was asserted. Blending them would make a corpus in which a
        measurement and an opinion are indistinguishable.
        """
        user = session.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": who.email}).first()
        event_id = uuid.uuid4()
        happened = body.happened_at or datetime.now(UTC)

        session.execute(sa.text(
            "INSERT INTO events (id, event_type, shape, first_seen_at, headline, "
            "latest_revision, cluster_method, code_hash) "
            "VALUES (:i, :t, :s, :f, :hl, 1, :c, :h)"
        ), {"i": event_id, "t": body.event_type, "s": body.shape, "f": happened,
            "hl": body.title,
            "c": f"manual:{who.email}", "h": "hand-entered"})

        for asset in dict.fromkeys(a.strip().upper() for a in body.assets if a.strip()):
            session.execute(sa.text(
                "INSERT INTO event_assets (event_id, revision, asset, "
                "expected_direction) VALUES (:e, 1, :a, :d) ON CONFLICT DO NOTHING"
            ), {"e": event_id, "a": asset, "d": body.direction})

        if body.scheduled_for is not None:
            session.execute(sa.text(
                "INSERT INTO schedule_revisions (event_id, revision, available_at, "
                "scheduled_for, confidence, source) "
                "VALUES (:e, 1, now(), :s, 'estimated', :src)"
            ), {"e": event_id, "s": body.scheduled_for, "src": f"manual:{who.email}"})

        # The title is the payload of a correction-kind annotation rather than a
        # column: `events` carries what the system knows, and the prose came
        # from a person.
        session.execute(sa.text(
            "INSERT INTO event_annotations (id, event_id, kind, available_at, "
            "payload, author) VALUES (:i, :e, 'correction', :a, CAST(:p AS jsonb), :u)"
        ), {"i": uuid.uuid4(), "e": event_id, "a": happened,
            "p": json.dumps({"title": body.title, "entered_by_hand": True,
                             **({"template": body.template} if body.template else {})}),
            "u": user.id})

        audit(session, who.email, "event.add", "event", str(event_id),
              {"event_type": body.event_type, "assets": body.assets})
        session.commit()
        return {"id": str(event_id), "first_seen_at": happened.isoformat()}

    @app.post("/api/events/{event_id}/reopen")
    def reopen_event(event_id: uuid.UUID, hours: int = 72,
                     who: Operator = operator,
                     session: Session = session_dep) -> dict[str, Any]:
        """Put an item back among those that still influence. Plan.md §11.8.

        Two endings can be taken back and two cannot, and the difference is
        whether the system was asserting something or observing it.

        A **decayed** item can be reopened because its horizon is assumed —
        §11.5 has not measured one — so an operator saying it still matters is
        the better of two estimates rather than an override of a finding. A
        **manual resolution** can be reopened because a person made it.

        A **passed schedule** cannot: the date arrived, and that is not a model
        output to disagree with. Nor can a **terminal process state**, which
        ended at a transition and would need another transition to reverse.

        The reopening is stored as a correction annotation rather than by
        rewinding a column, so the sequence — ended, reopened, by whom, when —
        survives. Overwriting would leave a row that has never been anything
        but active.
        """
        row = session.execute(sa.text(
            "SELECT e.event_type, e.shape, e.first_seen_at, e.resolved_at, "
            "       (SELECT min(sr.scheduled_for) FROM schedule_revisions sr "
            "         WHERE sr.event_id = e.id) AS scheduled_for, "
            "       (SELECT pt.state FROM process_transitions pt "
            "         WHERE pt.event_id = e.id "
            "         ORDER BY pt.sequence DESC LIMIT 1) AS latest_state "
            "FROM events e WHERE e.id = :i"
        ), {"i": event_id}).mappings().first()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")

        expiry = resolve_expiry(
            event_type=row["event_type"], shape=row["shape"],
            first_seen_at=row["first_seen_at"], resolved_at=row["resolved_at"],
            scheduled_for=row["scheduled_for"], latest_state=row["latest_state"],
        )
        if not expiry.reopenable:
            raise HTTPException(status.HTTP_409_CONFLICT, expiry.not_reopenable_because)

        user = session.execute(
            sa.text("SELECT id FROM users WHERE email = :e"), {"e": who.email}).first()
        until = datetime.now(UTC) + timedelta(hours=max(1, min(hours, 720)))

        session.execute(sa.text("UPDATE events SET resolved_at = NULL WHERE id = :i"),
                        {"i": event_id})
        session.execute(sa.text(
            "INSERT INTO event_annotations (id, event_id, kind, available_at, "
            "payload, author) VALUES (:i, :e, 'correction', now(), "
            "CAST(:p AS jsonb), :u)"
        ), {"i": uuid.uuid4(), "e": event_id,
            "p": json.dumps({"reactivated_until": until.isoformat(),
                             "over": expiry.reason}),
            "u": user.id})

        audit(session, who.email, "event.reopen", "event", str(event_id),
              {"until": until.isoformat()})
        session.commit()
        return {"id": str(event_id), "active_until": until.isoformat()}

    @app.post("/api/events/{event_id}/resolve")
    def resolve_event(event_id: uuid.UUID, who: Operator = operator,
                      session: Session = session_dep) -> dict[str, Any]:
        """Mark an item as no longer information. Plan.md §11.8.

        Supersession, in the operator's hands: a rumour that is denied does not
        decay, it is cancelled, and nothing in a clock can know that. Recorded
        as of now rather than backdated, for the same reason a judgement cannot
        be backdated.
        """
        updated = session.execute(sa.text(
            "UPDATE events SET resolved_at = now() "
            "WHERE id = :i AND resolved_at IS NULL RETURNING event_type"
        ), {"i": event_id}).first()
        if updated is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "no such event, or it is already resolved")
        audit(session, who.email, "event.resolve", "event", str(event_id))
        session.commit()
        return {"id": str(event_id), "resolved": True}

    #: Types worth suggesting that the corpus may not contain yet. Merged with
    #: whatever is actually in the database rather than replacing it, because a
    #: hand-written list drifts from the data the moment something new is
    #: ingested or seeded — which is how `halving` came to exist in three rows
    #: and in none of the suggestions.
    SUGGESTED_TYPES = (
        "hack", "regulation", "war", "unlock", "halving", "macro_release",
        "partnership", "exchange_collapse", "ban", "fork", "mainnet",
        "listing_rumour", "treasury_purchase",
    )

    @app.get("/api/event-templates")
    def event_templates(who: Operator = operator) -> list[dict]:
        """High-impact events with their shape and a suggested rating."""
        return [
            {"key": t.key, "label": t.label, "event_type": t.event_type,
             "shape": t.shape, "strength": t.strength, "direction": t.direction,
             "signed": t.signed, "market_wide": t.market_wide, "note": t.note}
            for t in TEMPLATES
        ]

    @app.get("/api/rating-scale")
    def rating_scale(who: Operator = operator) -> list[dict]:
        """The 1-10 steps with the sigma each one means.

        Served rather than written into the page, so the scale cannot drift from
        what the endpoint stores — and so the dialog can show the unit instead of
        hiding it. The operator should learn that seven means three and a half
        sigma, not be shielded from the fact that sigma is what is kept.
        """
        return [
            {"strength": n, "sigma": SIGMA_BY_STRENGTH[n], "label": describe(n)}
            for n in sorted(SIGMA_BY_STRENGTH)
        ]

    @app.get("/api/events/types")
    def event_types(who: Operator = operator,
                    session: Session = session_dep) -> list[dict]:
        """Every type in use, with its count, plus the ones worth suggesting.

        Derived rather than declared. The screen offers what exists and what is
        plausible, and cannot fall behind the data.
        """
        rows = session.execute(sa.text(
            "SELECT event_type, count(*) AS n FROM events "
            "GROUP BY event_type ORDER BY n DESC"
        )).mappings().all()
        in_use = {r["event_type"]: r["n"] for r in rows}
        return [
            {"event_type": name, "count": in_use.get(name, 0),
             "in_use": name in in_use}
            for name in sorted(set(in_use) | set(SUGGESTED_TYPES),
                               key=lambda k: (-in_use.get(k, 0), k))
        ]

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
        if body.strength is not None:
            # Kept alongside the sigma so a later reader can see what was
            # actually chosen, not only what it was converted to.
            payload["strength"] = body.strength
        if body.from_template:
            payload["from_template"] = body.from_template
        if body.note:
            payload["note"] = body.note

        rating = body.signed_sigma

        try:
            session.execute(sa.text(
                "INSERT INTO event_annotations (id, event_id, kind, available_at, "
                "payload, rating, confidence, author) "
                "VALUES (:i, :e, :k, :a, CAST(:p AS jsonb), :r, :c, :u)"
            ), {"i": annotation_id, "e": event_id, "k": body.kind,
                "a": available_at, "p": json.dumps(payload), "r": rating,
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

        # The rating already carries direction: it is signed sigma. Rather than
        # ask for the same judgement twice through a second control that could
        # disagree with it, the sign of the rating sets `expected_direction` for
        # the event's assets. One input, one meaning.
        #
        # Only for judgements, and only when a rating was given: a correction
        # repairs a parse failure and says nothing about which way the price
        # should go.
        if body.kind == "judgement" and rating is not None:
            session.execute(sa.text(
                "UPDATE event_assets SET expected_direction = :d "
                "WHERE event_id = :e"
            ), {"d": (rating > 0) - (rating < 0), "e": event_id})

        audit(session, who.email, f"annotation.{body.kind}", "event", str(event_id),
              {"rating": rating, "strength": body.strength,
               "confidence": body.confidence})
        session.commit()
        return {"id": str(annotation_id), "available_at": available_at.isoformat(),
                "rating": rating}

    # -- parameters ----------------------------------------------------------

    #: Settings the operator may change, and what each one does NOT reach.
    #:
    #: §12.4 draws a hard line: signal parameters, entry and exit rules, feature
    #: selection, label definitions and a strategy's asset universe cannot be
    #: tuned, because touching any of them creates a new strategy that re-enters
    #: screening. This surface exposes only the other column, and the read-only
    #: half is shown beside it rather than hidden — knowing why you cannot change
    #: something is the useful part.
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
        """Current value per key, with its reason and when it was set.

        Read from the newest row rather than from a mutable column: settings are
        appended, so every result issued under an older value stays readable.
        """
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
        """Append a new value. Nothing is updated in place.

        A key outside the editable set is refused by name rather than ignored: a
        request to change a signal parameter is a request the system should
        answer, not swallow.
        """
        if key not in EDITABLE:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"{key!r} is not an operator setting. §12.4 puts it in the column "
                f"that creates a new strategy, which returns to screening and "
                f"adds its trials to the counter — it cannot be edited here.",
            )
        # Checked here, not in the form. A rule in the form is a suggestion,
        # because this endpoint is reachable without it.
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

    # -- macro --------------------------------------------------------------

    @app.get("/api/macro/series")
    def macro_series(who: Operator = operator,
                     session: Session = session_dep) -> list[dict]:
        """The catalogue with freshness, which is the column that matters.

        A stale macro feed fails silently — the number is simply old, and old
        numbers look exactly like current ones. So the age of the newest
        observation is returned on every row rather than left to be noticed.
        """
        # The latest value and the one before it, so the screen can show where a
        # series stands. Both are stored numbers read back, not a series drawn:
        # §20.1 keeps time series in Grafana, and a current reading is a fact
        # rather than a chart.
        rows = session.execute(sa.text(
            "WITH ranked AS ("
            "  SELECT series, actual, scheduled_for, "
            "         row_number() OVER (PARTITION BY series "
            "                            ORDER BY scheduled_for DESC) AS rn "
            "  FROM macro_releases WHERE actual IS NOT NULL) "
            "SELECT s.code, s.name, s.kind, s.source, s.has_vintages, s.is_revised, "
            "       s.session_close, s.publish_lag_s, "
            "       max(r.scheduled_for) AS latest, "
            "       count(r.id) AS observations, "
            "       EXTRACT(EPOCH FROM (now() - max(r.scheduled_for)))/86400 AS days_old, "
            "       max(CASE WHEN k.rn = 1 THEN k.actual END) AS value, "
            "       max(CASE WHEN k.rn = 2 THEN k.actual END) AS previous, "
            "       max(CASE WHEN k.rn = 1 THEN k.scheduled_for END) AS value_at "
            "FROM macro_series s "
            "LEFT JOIN macro_releases r ON r.series = s.code "
            "LEFT JOIN ranked k ON k.series = s.code AND k.rn <= 2 "
            "GROUP BY s.code ORDER BY s.kind, s.code"
        )).mappings().all()
        return [
            dict(r) | {
                "days_old": float(r["days_old"]) if r["days_old"] is not None else None,
                # The change between the last two prints, in the series' own
                # units. Not annualised, not normalised — that would be a
                # computed figure, and this surface does not compute.
                "change": (float(r["value"]) - float(r["previous"]))
                if r["value"] is not None and r["previous"] is not None else None,
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
        return FileResponse(STATIC / "macro.html")

    # -- the screen ---------------------------------------------------------

    @app.get("/trade_lab", include_in_schema=False)
    def old_home() -> RedirectResponse:
        """The entry point moved to /home. Kept so an existing bookmark lands
        somewhere rather than on a 404."""
        return RedirectResponse("/home", status_code=308)

    @app.get("/login", include_in_schema=False)
    def login_screen() -> FileResponse:
        """The sign-in page. Unauthenticated screens send visitors here.

        It carries a `next` parameter so a deep link survives the detour, and
        honours it only when it is a path on this origin — an open redirect on a
        login page is how a session gets handed to somewhere else.
        """
        return FileResponse(STATIC / "login.html")

    @app.get("/home", include_in_schema=False)
    def home() -> FileResponse:
        """The entry point. Screens hang off the sidebar rather than being
        reached by knowing their paths."""
        return FileResponse(STATIC / "index.html")

    @app.get("/news", include_in_schema=False)
    def news_screen() -> FileResponse:
        """Served by the API rather than built as a separate front end, for now.

        §20 describes fifteen screens and a React application; this is the one
        that writes data the research depends on, so it exists before the build
        tooling does. The markup calls the same endpoints a React client would,
        so replacing it later changes the view and not the contract.
        """
        return FileResponse(STATIC / "news.html")

    @app.get("/strategies", include_in_schema=False)
    def strategies_screen() -> FileResponse:
        return FileResponse(STATIC / "strategies.html")

    @app.get("/experiments", include_in_schema=False)
    def experiments_screen() -> FileResponse:
        return FileResponse(STATIC / "experiments.html")

    @app.get("/data-quality", include_in_schema=False)
    def data_quality_screen() -> FileResponse:
        return FileResponse(STATIC / "data_quality.html")

    @app.get("/audit", include_in_schema=False)
    def audit_screen() -> FileResponse:
        return FileResponse(STATIC / "audit.html")

    #: The decision sequence, in the order it runs. Plan.md §8, §9, §12, §14.
    #:
    #: Every step carries the invariant that has to hold when it runs and, where
    #: one exists, the defect that proved the invariant was needed. That pairing
    #: is the point: a rule with no story behind it reads as a preference, and
    #: someone reorders it. Four of the five below were real defects in this
    #: codebase, found by tests built so the wrong behaviour produces a visibly
    #: wrong answer.
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
                {"n": 10, "name": "News conditions the size",
                 "does": "Look up every event visible at this instant that "
                         "touches the asset, and damp the intended position: "
                         "half under a live regime shift, seven tenths inside "
                         "the three-day anticipation window of a scheduled one, "
                         "and both compounding.",
                 "rule": "News conditions, it does not signal. §11.7 puts sign "
                         "as an input to an expected-reaction model and never a "
                         "signal in itself — what is tradeable is the residual, "
                         "observed minus expected, and trading the sign directly "
                         "trades the half everyone has already priced. So an "
                         "event earns a worse prior, not a direction. Visible "
                         "means available_at has passed, not that it happened.",
                 "cost": "Not damping at all was the state until now: events "
                         "were recorded, rated and measured, and no decision "
                         "read them. The residual itself still waits on §11.5 "
                         "fitting an expected-reaction model, without which "
                         "there is nothing to subtract from."},
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

    @app.get("/api/algorithm")
    def algorithm(who: Operator = operator,
                  session: Session = session_dep) -> dict:
        """The decision sequence, with live counts where a step has them.

        Counts read from tables, never computed — §20.1. They are here so the
        sequence is not an illustration: the trial number is the one that
        deflates the next verdict.
        """
        def scalar(query: str) -> int:
            try:
                return int(session.execute(sa.text(query)).scalar() or 0)
            except Exception:
                session.rollback()
                return 0

        return {
            "phases": ALGORITHM,
            "observed": {
                "trials": scalar("SELECT coalesce(sum(executed_trials),0) FROM studies"),
                "experiments": scalar("SELECT count(*) FROM experiments"),
                "verdicts": scalar("SELECT count(*) FROM validation_reports"),
                "passed": scalar(
                    "SELECT count(*) FROM validation_reports WHERE verdict='pass'"),
                "runs": scalar("SELECT count(*) FROM runs"),
                "risk_events": scalar("SELECT count(*) FROM risk_events"),
            },
        }

    @app.get("/algorithm", include_in_schema=False)
    def algorithm_screen() -> FileResponse:
        return FileResponse(STATIC / "algorithm.html")

    @app.get("/pipeline", include_in_schema=False)
    def old_pipeline() -> RedirectResponse:
        """Renamed. It showed project status; the question it was asked was
        about the decision sequence."""
        return RedirectResponse("/algorithm", status_code=308)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "computes_metrics": False, "can_start_live_trading": False}

    return app
