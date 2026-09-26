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
import time
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
from tradelab.collectors.sentiment import CMC_SOURCE
from tradelab.collectors.sentiment import SOURCE as FNG_SOURCE
from tradelab.config import Settings
from tradelab.scenario import run as scenario_run
from tradelab.scenario.tilt import direction as trend_direction

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


class ScenarioRequest(BaseModel):
    """The conditions a simulation is told to run under. Plan.md §20.

    Three fields and no fourth. No asset, because there is one set of conditions
    and they are the market's; no date, because none of this happened —
    everything that did is dated and point-in-time and lives in `events`.

    `extra="forbid"` so a client sending a field this does not have gets an
    error instead of having it dropped. A silently ignored field is how an
    operator comes to believe a setting took effect.
    """

    model_config = {"extra": "forbid"}

    #: The operator's reading of the news. 1 to 10, **5 neutral**: 5 is
    #: what is held when nothing has happened, and it leaves every
    #: position exactly as it was. Direction is which side of 5 it sits
    #: on, derived and never stored - two fields that can disagree is what
    #: this replaced, and the arithmetic reading magnitude as distance
    #: from zero tilted the book by a quarter at the value meaning "I
    #: have heard nothing".
    trend: int = Field(ge=1, le=10)
    #: Where on the 1-100 scale to assume the rest of the market sits. An
    #: assumption, and deliberately a different field from the measured reading
    #: in `sentiment_readings` — holding both in one number would make an
    #: opinion indistinguishable from a measurement.
    sentiment: int = Field(ge=1, le=100)
    #: Who decided this. 'manual' is a person's own reading, 'adopted' is a
    #: computed value a person accepted, 'derived' is written without a person.
    #:
    #: PREREG-005 measures whether the *operator's* reading of the news predicts
    #: anything, and filters on this. A hit credited to a person that was
    #: computed from a trade count would confirm the wrong hypothesis, and after
    #: the fact the log could not tell them apart.
    source: str = Field(default="manual", pattern="^(manual|derived|adopted)$")

class RiskConfigRequest(BaseModel):
    """A new risk limit configuration. Plan.md §14.1, §12.4, §20.

    §12.4 puts sizing and risk limits in the operator's hands — unlike signal
    parameters, which cannot be edited anywhere because changing one creates a
    new strategy that re-enters the funnel.

    A configuration is **created, never edited**. §14.1 versions them and has
    runs reference one rather than embed it, so a change is traceable and a
    limit is never altered beneath a position opened under it.

    `extra="forbid"` so an unknown field is refused rather than dropped: a
    silently ignored limit is one the operator believes is in force.
    """

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9 _.-]+$")
    #: Every limit, because one left out would take a default nobody chose.
    #: Bounds live in tradelab.risk.bounds and are checked on the way in.
    limits: dict[str, Any] = Field(min_length=1)
    #: Why. A limit change with no stated reason is one nobody can review, and
    #: §14.1's whole argument for versioning them is that a change is traceable.
    rationale: str = Field(min_length=10, max_length=1000)


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

    # -- the scenario, and the market's own reading of itself ---------------

    @app.get("/api/scenario")
    def read_scenario(who: Operator = operator,
                      session: Session = session_dep) -> dict[str, Any]:
        """The conditions a simulation runs under. Always present.

        Seeded by the migration rather than created on first write, so "no
        pressure, neutral sentiment" and "nobody has set this" cannot come back
        as the same empty result. They are different facts and only one of them
        is a decision.
        """
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
        """Set the conditions. An update, never an insert — there is one row.

        PUT and not POST for the same reason: this names something that already
        exists and replaces its contents, and sending it twice leaves the same
        state. A POST would imply a second set of conditions could be created.
        """
        # Read in the same transaction as the UPDATE below, so the "from" it
        # records is the value this write actually replaced.
        before = session.execute(sa.text(
            "SELECT trend, sentiment FROM market_conditions WHERE id = 1 FOR UPDATE"
        )).mappings().first()

        # The UPDATE carries its own no-op test, so a save that changes nothing
        # touches no row and writes no log line. Done in the WHERE rather than
        # by reading first and comparing in Python: between those two statements
        # another request can change the value, and the comparison would then be
        # against something that is no longer there.
        row = session.execute(sa.text(
            "UPDATE market_conditions SET trend = :t, sentiment = :s, "
            "source = :src WHERE id = 1 AND ("
            "  trend IS DISTINCT FROM :t OR sentiment IS DISTINCT FROM :s) "
            "RETURNING trend, sentiment, source"
        ), {"t": body.trend, "s": body.sentiment,
            "src": body.source}).mappings().first()

        if row is None:
            # Either nothing moved, or the row is missing. Told apart by asking,
            # because returning "unchanged" for a missing row would hide the one
            # state that is actually broken.
            current = session.execute(sa.text(
                "SELECT trend, sentiment, source FROM market_conditions WHERE id = 1"
            )).mappings().first()
            if current is None:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "the conditions row is missing; migration 0013 seeds it")
            # Idempotent, and honest about it: the same values sent twice leave
            # one entry in the log, not two. A change log that records
            # non-changes cannot answer when something was last changed, which
            # is the only question it exists for.
            return {"trend": current["trend"],
                    "direction": trend_direction(current["trend"]),
                    "sentiment": current["sentiment"],
                    "source": current["source"], "changed": False}

        # Audited because these are inputs someone chose. Which conditions a
        # simulation was run under last month is a question the log has to be
        # able to answer after the values have been changed — so the entry says
        # what they moved from as well as to, and which run it started.
        #
        # Save is what starts the algorithm. Only on a real change, for the
        # same reason the log only records one: re-running an identical scenario
        # produces an identical answer, and a second copy of it is noise in the
        # list the operator reads.
        #
        # Synchronous because it is milliseconds on the archive this holds, and
        # because a run the operator does not wait for is one they will not
        # connect to the button they pressed. If the archive grows this has to
        # move off the request, and the timing is reported so that is visible
        # before it becomes a problem rather than after.
        started = time.perf_counter()
        try:
            outcome = scenario_run.execute(
                body.trend, body.sentiment)
        except FileNotFoundError as missing:
            # The scenario is saved either way. A run that cannot start is not a
            # reason to discard a setting the operator made.
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
        """Every change to the conditions, newest first.

        Read out of `audit_log` rather than from a second table written
        alongside it. A history kept in its own table is a second writer, and
        two records of the same fact drift — the one nobody reads is the one
        that goes wrong. This is the same rows the Audit screen shows, asked a
        narrower question.

        Every entry here is a change, because `set_scenario` does not write when
        nothing moved. That is what makes the list readable: a log that records
        non-changes cannot answer when something was last changed, which is the
        only question it exists for.

        Entries carry what they changed *from*, so the screen can say which of
        the two dials moved. Rows written before that was recorded come back
        with `from` null and are shown as changes to both, which is the honest
        reading of an entry that does not say.
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
        """What the algorithm did under each set of conditions, newest first.

        The conditions are columns on the row rather than a join to
        `market_conditions`, which holds one mutable row — a join would report
        every past run as having used today's settings, which is the shape of
        error that makes a backtest look prescient.
        """
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
        """Both indices, and the comparisons from whichever has the history.

        They are different indices, not two opinions about one: different
        methods and different numbers — 72 against 74 on the day CMC was added.
        Reported side by side rather than averaged, because an average of two
        methods is a third method nobody can decompose.

        Read back, never computed. §20.1 puts the boundary at the application
        deriving figures, and picking stored rows is not deriving one — but the
        chart the vendors draw is a time series, and §20.1 puts those in Grafana.
        """
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
                # Said plainly, because a stale feed fails silently: the number
                # is simply old, and old numbers look exactly like current ones.
                "days_old": (datetime.now(UTC).date() - row["day"]).days,
            }

        primary = latest(CMC_SOURCE) or latest(FNG_SOURCE)
        if primary is None:
            return {"available": False}
        other = latest(FNG_SOURCE if primary["source"] == CMC_SOURCE else CMC_SOURCE)

        # The comparisons come from the series that has depth. CMC serves only
        # its latest value, so its history starts the day it was first collected
        # — naming the series the dates come from stops them reading as the
        # headline's own past.
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

        # "On or before" rather than exact, so a missing day gives the most
        # recent value before it instead of an empty cell. A gap in a daily
        # series is a collection failure, and a blank would read as calm.
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

    @app.get("/market-sentiment", include_in_schema=False)
    def market_sentiment_screen() -> FileResponse:
        """The market trend the operator sets, and the index the market prints. §20.

        Both on one page because they answer the same question from opposite
        sides — what the book is leaning into, and what everyone else is doing —
        and reading either one alone is how a lean gets mistaken for a consensus.

        Kept plain on purpose. The market trend has two fields and the index has one, so
        anything else this page offered would be an input nothing consumes, and
        an input nothing consumes is one an operator eventually believes in.
        """
        return FileResponse(STATIC / "market_sentiment.html")

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
        """Every limit the engine enforces, and every breach it has recorded.

        The list is **derived from the engine**, not written here: the fields of
        `RiskLimits`, the `HALTING` set and `REQUIRES_HUMAN`. A page with its
        own copy of the limits is a page that keeps showing one after it is
        removed, and misses the one that was added — and this is the screen
        where that would matter most.

        Values come from the newest stored configuration when there is one, and
        from the engine's defaults when there is not. Which of the two is in
        force is reported rather than left to be inferred, because a default
        displayed as a configuration is a limit nobody chose.
        """
        import dataclasses

        from tradelab.risk.bounds import BOUNDS
        from tradelab.risk.limits import (
            HALTING,
            REQUIRES_HUMAN,
            STOPS,
            Breach,
            RiskLimits,
        )

        defaults = RiskLimits()
        # The one in force. The same row the limits below are read from, so
        # the badge and the values on the page cannot disagree about which
        # configuration is active.
        stored = session.execute(sa.text(
            "SELECT * FROM risk_limit_configs ORDER BY valid_from DESC LIMIT 1"
        )).mappings().first()

        halting = {b.value for b in HALTING}
        human = {b.value for b in REQUIRES_HUMAN}

        limits = []
        for field in dataclasses.fields(RiskLimits):
            name = field.name
            # The breach a limit raises is named after it, except the price one:
            # `max_price_deviation_bps` raises `price_deviation`. Matched by
            # membership rather than assumed equal, so a rename shows up as a
            # limit that halts nothing instead of a crash.
            breach = name if name in halting or name in human else (
                "price_deviation" if name.startswith("max_price_deviation") else name)
            limits.append({
                "name": name,
                "value": str(stored[name]) if stored is not None and name in stored
                         else str(getattr(defaults, name)),
                "default": str(getattr(defaults, name)),
                "stops": STOPS.get(name, ""),
                # A breach that halts the whole run, not just this order.
                "halts": breach in halting,
                # And one a person has to clear before it resumes.
                "needs_a_person": breach in human,
                # What it may be set to, from the same place the endpoint
                # checks, so the form cannot offer a value the server refuses.
                "low": str(BOUNDS[name].low), "high": str(BOUNDS[name].high),
                "whole": BOUNDS[name].whole, "bound": BOUNDS[name].describes,
            })

        # Every configuration ever created, so a change is visible as a change
        # rather than as a value that is simply different from last time.
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
            # Every breach the engine can raise, including the ones that are not
            # a configured number: a halt already in force, a position below the
            # minimum viable size, and news cutting a size down.
            "breach_kinds": sorted(b.value for b in Breach),
            # ACTIVE is the newest by valid_from, so exactly one row carries
            # it — by construction and not by a flag somebody has to keep
            # correct. A flag can be set on two rows; a maximum cannot be
            # reached twice, and a tie is refused by a unique constraint.
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
        """Create a risk limit configuration. Never edit one.

        §14.1 versions these and has runs reference one rather than embed it,
        precisely so a limit cannot change beneath a position opened under it.
        An UPDATE here would undo that: every past run would silently report
        limits it never ran under.

        The bounds are checked server side because the endpoint is reachable
        without the form, and they are bounds *on the bounds* — a risk limit is
        what stops a bad decision, so one set loosely enough has been removed
        while still appearing on the screen.
        """
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

        # The rationale lives in the audit entry rather than in a column: the
        # configuration is the numbers, and why they were chosen is a thing a
        # person said, at a time, which is what the log is for.
        audit(session, who.email, "risk_config.create", "risk_limit_config",
              str(config_id), {"name": body.name, "rationale": body.rationale,
                               "limits": {k: str(v) for k, v in parsed.items()}})
        session.commit()
        return {"id": str(config_id), "name": body.name,
                "limits": {k: str(v) for k, v in parsed.items()}}

    @app.get("/api/positions")
    def open_positions(who: Operator = operator,
                       session: Session = session_dep) -> dict[str, Any]:
        """Every position currently held, with its own profit and loss.

        **Read, never computed.** `positions` stores `unrealised_pnl`,
        `realised_pnl` and `funding_paid` as the execution engine folded them
        from fills, and §20.1 puts the boundary exactly here: a figure this
        screen derived would be one nobody else can reproduce, and it would
        disagree with the engine's the moment either changed.

        `positions` is a snapshot series keyed on (run, asset, as_of), so the
        current position is the newest row per run and asset. Taking the newest
        row overall would report one asset's position as the whole book, and
        taking every row would count a position once per snapshot ever written.

        A flat position is not an open one, so rows at zero quantity are left
        out — they are the record of a position that closed, and their realised
        P&L is still counted in the totals below.
        """
        rows = session.execute(sa.text(
            "SELECT DISTINCT ON (p.run_id, p.asset) "
            "  p.run_id, p.asset, p.as_of, p.qty, p.avg_entry, "
            "  p.unrealised_pnl, p.realised_pnl, p.funding_paid, "
            "  r.mode, r.venue, r.status, r.base_currency "
            "FROM positions p JOIN runs r ON r.id = p.run_id "
            "ORDER BY p.run_id, p.asset, p.as_of DESC"
        )).mappings().all()

        def number(value: Any) -> float | None:
            return None if value is None else float(value)

        open_rows = [{
            "run_id": str(r["run_id"]), "asset": r["asset"],
            "as_of": r["as_of"].isoformat(), "qty": number(r["qty"]),
            "side": "LONG" if (r["qty"] or 0) > 0 else "SHORT",
            "avg_entry": number(r["avg_entry"]),
            "unrealised_pnl": number(r["unrealised_pnl"]),
            "realised_pnl": number(r["realised_pnl"]),
            "funding_paid": number(r["funding_paid"]),
            # Shown, never editable. §20.2 is the most important thing in that
            # section, and a book whose mode is not on screen is one where
            # nobody can see which money it is.
            "mode": r["mode"], "venue": r["venue"], "status": r["status"],
            "currency": r["base_currency"],
        } for r in rows if (r["qty"] or 0) != 0]

        totals = session.execute(sa.text(
            "SELECT coalesce(sum(unrealised_pnl), 0) AS unrealised, "
            "       coalesce(sum(realised_pnl), 0) AS realised, "
            "       coalesce(sum(funding_paid), 0) AS funding "
            "FROM (SELECT DISTINCT ON (run_id, asset) unrealised_pnl, "
            "        realised_pnl, funding_paid FROM positions "
            "      ORDER BY run_id, asset, as_of DESC) AS latest"
        )).mappings().first()

        return {
            "positions": open_rows,
            "open_count": len(open_rows),
            # Across the latest snapshot of every position, closed ones
            # included: realised P&L does not stop counting when a position does.
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
        }

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

        # The last scenario run, so the sequence shows where it got to rather
        # than describing a path nobody has walked. Read back, never recomputed.
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
                # Execution runs, which is a different thing from a scenario
                # run and is the count that says whether steps 8 to 13 have
                # ever executed. Zero is the honest answer and the screen says
                # so rather than leaving them looking merely unreached.
                "runs": scalar("SELECT count(*) FROM runs"),
                "risk_events": scalar("SELECT count(*) FROM risk_events"),
            },
            # Which steps a scenario run executes and which it substitutes a
            # reference for. Declared by the runner rather than assumed by the
            # screen, so the two cannot disagree about what ran.
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
        """What the engine refuses, and what it has refused.

        Read only. A risk limit defines behaviour, so §12.4 keeps it out of the
        operator's editable settings: changing one creates a new configuration
        and a run records which it ran under, so a limit is never edited beneath
        a position opened under it.
        """
        return FileResponse(STATIC / "risk_limits.html")

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
