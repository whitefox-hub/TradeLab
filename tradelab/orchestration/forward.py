"""The registry side of a forward simulation. Plan.md §12.1 rung 2, §15, §20.

Writes a simulator-mode `runs` row, `positions` snapshots and `balances`.
A forward run is not a promotion: the strategy stays `validating`; §15's
`candidate → simulator` transition is untouched.
"""

from __future__ import annotations

import ast
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from tradelab.risk.limits import RiskLimits

LIMIT_FIELDS = tuple(RiskLimits.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class Snapshot:
    asset: str
    qty: Decimal
    avg_entry: Decimal
    unrealised: Decimal
    realised: Decimal
    funding_paid: Decimal
    guide: str | None
    trend: int | None
    trend_source: str | None


class Store:
    def __init__(self, url: str) -> None:
        self.engine = sa.create_engine(url, pool_pre_ping=True)


    def ensure_strategy(self, name: str, impl: Path, status: str = "validating") -> uuid.UUID:
        """One row per strategy name and code hash (§16.1)."""
        code_hash = code_identity(impl)
        with self.engine.begin() as db:
            found = db.execute(sa.text(
                "SELECT id FROM strategies WHERE name = :n AND code_hash = :h"),
                {"n": name, "h": code_hash}).scalar()
            if found:
                return uuid.UUID(str(found))
            sid = uuid.uuid4()
            db.execute(sa.text(
                "INSERT INTO strategies (id, name, kind, impl_ref, code_hash, param_space, "
                "constraints, status) VALUES (:id, :n, 'forward', :impl, :h, '{}', '{}', :s)"),
                {"id": sid, "n": name, "impl": str(impl), "h": code_hash, "s": status})
            return sid

    def active_limits(self) -> tuple[uuid.UUID, RiskLimits]:
        """The active configuration: newest by `valid_from`, as the API defines it."""
        with self.engine.begin() as db:
            row = db.execute(sa.text(
                "SELECT * FROM risk_limit_configs ORDER BY valid_from DESC LIMIT 1")
            ).mappings().one()
        values: dict[str, Any] = {}
        for name in LIMIT_FIELDS:
            default = getattr(RiskLimits(), name)
            values[name] = type(default)(row[name])
        return uuid.UUID(str(row["id"])), RiskLimits(**values)

    def cost_model_id(self) -> uuid.UUID:
        with self.engine.begin() as db:
            return uuid.UUID(str(db.execute(sa.text(
                "SELECT id FROM cost_models WHERE name = 'binance-retail'")).scalar_one()))

    def ensure_run(self, strategy_id: uuid.UUID, label: str, params: dict[str, Any],
                   capital: Decimal, limits_id: uuid.UUID) -> uuid.UUID:
        """The open run for this label, or a new one; a restart resumes, never duplicates."""
        with self.engine.begin() as db:
            found = db.execute(sa.text(
                "SELECT id FROM runs WHERE strategy_id = :s AND mode = 'simulator' "
                "AND status = 'running' AND params->>'label' = :l"),
                {"s": strategy_id, "l": label}).scalar()
            if found:
                return uuid.UUID(str(found))
            db.execute(sa.text(
                "UPDATE runs SET status = 'stopped', ended_at = now(), "
                "halt_reason = 'superseded by a new version of the strategy' "
                "WHERE mode = 'simulator' AND ended_at IS NULL AND params->>'label' = :l"),
                {"l": label})
            rid = uuid.uuid4()
            db.execute(sa.text(
                "INSERT INTO runs (id, strategy_id, mode, venue, params, initial_capital, "
                "base_currency, risk_limit_config_id, cost_model_id, code_version, "
                "image_digest, config_hash, status, started_at) VALUES "
                "(:id, :s, 'simulator', 'binance', CAST(:p AS jsonb), :c, 'USDT', :r, :cm, "
                ":v, 'unpinned', :h, 'running', :t)"),
                {"id": rid, "s": strategy_id, "p": json.dumps({**params, "label": label}),
                 "c": capital, "r": limits_id, "cm": self.cost_model_id(),
                 "v": _git_head(), "h": hashlib.sha256(json.dumps(params, sort_keys=True)
                                                       .encode()).hexdigest(),
                 "t": datetime.now(UTC)})
            return rid


    def write(self, run_id: uuid.UUID, at: datetime, rows: list[Snapshot],
              equity: Decimal, used: Decimal) -> None:
        with self.engine.begin() as db:
            for r in rows:
                db.execute(sa.text(
                    "INSERT INTO positions (run_id, asset, as_of, qty, avg_entry, "
                    "unrealised_pnl, realised_pnl, funding_paid, guide, trend, trend_source) "
                    "VALUES (:run, :a, :t, :q, :e, :u, :r, :f, :g, :tr, :ts) "
                    "ON CONFLICT DO NOTHING"),
                    {"run": run_id, "a": r.asset, "t": at, "q": r.qty, "e": r.avg_entry,
                     "u": r.unrealised, "r": r.realised, "f": r.funding_paid,
                     "g": r.guide, "tr": r.trend, "ts": r.trend_source})
            db.execute(sa.text(
                "INSERT INTO balances (run_id, as_of, currency, equity, free, used_margin) "
                "VALUES (:run, :t, 'USDT', :e, :f, :u) ON CONFLICT DO NOTHING"),
                {"run": run_id, "t": at, "e": equity, "f": equity - used, "u": used})


    def setting(self, key: str) -> dict | None:
        """The newest value of an operator setting (app_settings is append-only)."""
        with self.engine.begin() as db:
            value = db.execute(sa.text(
                "SELECT value FROM app_settings WHERE key = :k "
                "ORDER BY valid_from DESC LIMIT 1"), {"k": key}).scalar()
        if value is None:
            return None
        return value if isinstance(value, dict) else json.loads(value)


    def pending_closes(self, run_id: uuid.UUID) -> list[tuple[int, str]]:
        """Operator requests to close a position of this run, not yet answered.

        The request and its answer are both audit rows, so the queue is the audit trail.
        """
        with self.engine.begin() as db:
            rows = db.execute(sa.text(
                "SELECT q.id, q.payload->>'asset' FROM audit_log q "
                "WHERE q.action = 'position.close_request' AND q.payload->>'run_id' = :run "
                "AND NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.action = 'position.closed' "
                "AND a.payload->>'request' = q.id::text) ORDER BY q.id"),
                {"run": str(run_id)}).all()
        return [(int(i), str(a)) for i, a in rows]

    def answer_close(self, request: int, run_id: uuid.UUID, asset: str,
                     outcome: dict[str, Any]) -> None:
        with self.engine.begin() as db:
            db.execute(sa.text(
                "INSERT INTO audit_log (actor, action, subject_kind, subject_id, payload) "
                "VALUES ('forward-runner', 'position.closed', 'position', :s, CAST(:p AS jsonb))"),
                {"s": f"{run_id}:{asset}",
                 "p": json.dumps({"request": str(request), "run_id": str(run_id),
                                  "asset": asset, **outcome})})

    def end(self, run_id: uuid.UUID, reason: str) -> None:
        """Close a run for good: it stops showing positions, and its history stays."""
        with self.engine.begin() as db:
            db.execute(sa.text("UPDATE runs SET status = 'stopped', halt_reason = :r, "
                               "ended_at = now() WHERE id = :id"), {"r": reason, "id": run_id})

    def halt(self, run_id: uuid.UUID, reason: str) -> None:
        with self.engine.begin() as db:
            db.execute(sa.text("UPDATE runs SET status = 'halted', halt_reason = :r "
                               "WHERE id = :id"), {"r": reason, "id": run_id})


def code_identity(impl: Path) -> str:
    """Hash of the code itself: the AST with docstrings removed, so an edit to comments
    or docstrings is not a new strategy. A raw-bytes hash opened a second run of the
    trend book after a comment-only cleanup, which showed its positions twice."""
    tree = ast.parse(impl.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def _git_head() -> str:
    head = Path(".git/HEAD")
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref: "):
            return (Path(".git") / ref[5:]).read_text().strip()[:12]
        return ref[:12]
    except OSError:
        return "unknown"
