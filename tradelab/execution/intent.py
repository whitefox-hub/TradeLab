"""The intent log. Plan.md §14.3.

Append-only; records what the strategy wanted (vetoes included), not only executions.
SENT is persisted before the order goes out, so a crash leaves something to resolve.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from tradelab.execution.types import Order


class IntentState(StrEnum):
    WANTED = "wanted"          # the strategy asked
    VETOED = "vetoed"          # the risk engine refused
    SENT = "sent"              # written before the venue was called
    ACKNOWLEDGED = "acknowledged"
    FILLED = "filled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"        # sent, never acknowledged: must be resolved
    ABSENT = "absent"          # resolved on restart: the venue never saw it


#: An order may exist at the venue; resolved on startup before anything is sent.
UNRESOLVED: frozenset[IntentState] = frozenset({IntentState.SENT, IntentState.UNKNOWN})


@dataclass(frozen=True, slots=True)
class Intent:
    at: datetime
    run_id: str
    sequence: int
    client_order_id: str
    asset: str
    side: str
    quantity: Decimal
    state: IntentState
    reason: str = ""
    venue_order_id: str = ""
    #: FILLED records only; needed to rebuild the average price (reconcile checks size only).
    price: Decimal | None = None
    fee: Decimal | None = None

    def as_json(self) -> str:
        record = asdict(self)
        record["at"] = self.at.isoformat()
        record["quantity"] = str(self.quantity)
        record["state"] = str(self.state)
        for name in ("price", "fee"):
            if record[name] is not None:
                record[name] = str(record[name])
        return json.dumps(record, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> Intent:
        record = json.loads(line)
        return cls(
            at=datetime.fromisoformat(record["at"]), run_id=record["run_id"],
            sequence=int(record["sequence"]), client_order_id=record["client_order_id"],
            asset=record["asset"], side=record["side"],
            quantity=Decimal(record["quantity"]), state=IntentState(record["state"]),
            reason=record.get("reason", ""),
            venue_order_id=record.get("venue_order_id", ""),
            price=Decimal(record["price"]) if record.get("price") is not None else None,
            fee=Decimal(record["fee"]) if record.get("fee") is not None else None,
        )


@dataclass
class IntentLog:
    """Append-only; a later record for a `client_order_id` supersedes, the earlier stays."""

    path: Path | None = None
    records: list[Intent] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> IntentLog:
        """Read a log back, as a restarted process must before it sends anything.

        A torn final line (crash mid-write) is truncated from the file so the next append
        does not join it. Damage anywhere else is refused.
        """
        if not path.exists():
            return cls(path=path)
        raw = path.read_bytes()
        lines = raw.split(b"\n")
        records: list[Intent] = []
        kept = 0
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                kept += len(line) + 1
                continue
            try:
                records.append(Intent.from_json(line.decode("utf-8")))
            except (ValueError, KeyError, UnicodeDecodeError) as damaged:
                if number == len(lines):
                    break
                raise ValueError(
                    f"{path}: line {number} is not an intent record ({damaged}); "
                    "only a torn final line is a crash, and this is not one"
                ) from damaged
            kept += len(line) + 1
        if kept < len(raw):
            with path.open("r+b") as handle:
                handle.truncate(kept)
        if records and not path.read_bytes().endswith(b"\n"):
            with path.open("ab") as handle:
                handle.write(b"\n")
        return cls(path=path, records=records)

    def append(self, intent: Intent) -> Intent:
        self.records.append(intent)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(intent.as_json() + "\n")
        return intent

    def record(
        self, order: Order, run_id: str, sequence: int, state: IntentState,
        at: datetime, reason: str = "", venue_order_id: str = "",
        price: Decimal | None = None, fee: Decimal | None = None,
    ) -> Intent:
        return self.append(Intent(
            at=at, run_id=run_id, sequence=sequence,
            client_order_id=order.client_order_id, asset=order.asset,
            side=str(order.side), quantity=order.quantity, state=state,
            reason=reason, venue_order_id=venue_order_id, price=price, fee=fee,
        ))

    def latest_state(self) -> dict[str, Intent]:
        """Most recent record per order, which is its current state."""
        out: dict[str, Intent] = {}
        for intent in self.records:
            out[intent.client_order_id] = intent
        return out

    def unresolved(self) -> list[Intent]:
        """Orders that may exist at the venue with unknown fate; settled before a restart sends."""
        return [i for i in self.latest_state().values() if i.state in UNRESOLVED]
