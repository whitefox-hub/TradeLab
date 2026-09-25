"""The intent log. Plan.md §14.3.

Append-only, and it records **what the strategy wanted**, not only what was
executed. Two things depend on that distinction:

* Reconstructing why a position was opened. An order book of executions says
  what happened; only the intent says what the decision was, and a refused order
  is a decision too.
* Recovery. State is persisted *before* the order is sent, never after, so a
  process that dies between send and acknowledgement knows on startup that the
  order might exist and goes looking for it. A log written after the fact cannot
  answer that question, which is exactly when it is asked.
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


#: States a process can die in while an order may exist at the venue. On
#: startup these are resolved against the venue before anything else is sent.
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

    def as_json(self) -> str:
        record = asdict(self)
        record["at"] = self.at.isoformat()
        record["quantity"] = str(self.quantity)
        record["state"] = str(self.state)
        return json.dumps(record, sort_keys=True)


@dataclass
class IntentLog:
    """Append-only. Nothing here is ever updated in place.

    A later record supersedes an earlier one for the same `client_order_id`; the
    earlier one stays, because the sequence of states is the audit trail and
    overwriting it would erase the moment the process died.
    """

    path: Path | None = None
    records: list[Intent] = field(default_factory=list)

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
    ) -> Intent:
        return self.append(Intent(
            at=at, run_id=run_id, sequence=sequence,
            client_order_id=order.client_order_id, asset=order.asset,
            side=str(order.side), quantity=order.quantity, state=state,
            reason=reason, venue_order_id=venue_order_id,
        ))

    def latest_state(self) -> dict[str, Intent]:
        """Most recent record per order, which is its current state."""
        out: dict[str, Intent] = {}
        for intent in self.records:
            out[intent.client_order_id] = intent
        return out

    def unresolved(self) -> list[Intent]:
        """Orders that may exist at the venue and whose fate is unknown.

        This is the list a restart has to settle before it sends anything, and
        it is why `SENT` is written before the call rather than after it.
        """
        return [i for i in self.latest_state().values() if i.state in UNRESOLVED]
