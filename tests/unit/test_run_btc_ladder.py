"""The ladder book's manual buys and sells, without a database or the network."""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tradelab.execution.types import Instrument
from tradelab.risk.limits import RiskLimits
from tradelab.strategy import btc_ladder as lad

ROOT = Path(__file__).resolve().parents[2]


def _runner():
    spec = importlib.util.spec_from_file_location("run_btc_ladder",
                                                  ROOT / "scripts/run_btc_ladder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStore:
    def __init__(self) -> None:
        self.requests: list[tuple[int, str, dict]] = []
        self.answers: dict[int, dict] = {}

    def active_limits(self):
        return uuid.uuid4(), RiskLimits()

    def ensure_strategy(self, *args):
        return uuid.uuid4()

    def ensure_run(self, *args):
        return uuid.UUID(int=1)

    def pending_manual(self, run_id):
        return [r for r in self.requests if r[0] not in self.answers]

    def answer_manual(self, request, run_id, outcome):
        self.answers[request] = outcome

    def write(self, *args):
        pass


@pytest.fixture
def book(tmp_path, monkeypatch):
    run = _runner()
    monkeypatch.setattr(run, "STATE_DIR", tmp_path)
    price = {"now": Decimal(100_000)}
    monkeypatch.setattr(run, "ticker", lambda client: price["now"])
    inst = Instrument("BTCUSDT", Decimal("0.01"), Decimal("0.00001"), Decimal(5))
    store = FakeStore()
    b = run.Book(store, inst, lad.Config(budget=2_000, parts=20))
    return run, b, store, price


@pytest.mark.fast
def test_a_manual_buy_is_one_part_marked_manual(book) -> None:
    run, b, store, _ = book
    store.requests.append((1, "ladder.buy_request", {}))
    assert b.manual_requested(None)
    assert store.answers[1]["result"] == "bought"
    assert len(b.parts) == 1 and b.parts[0].spent == pytest.approx(100, abs=1)
    status = json.loads((run.STATE_DIR / "status.json").read_text())
    assert status["parts"][0]["manual"] is True
    wanted = [i for i in b.engine.intents.records if i.state == "wanted"]
    assert wanted[-1].reason == "manual"


@pytest.mark.fast
def test_a_manual_sale_may_realise_a_loss_and_says_so(book) -> None:
    _, b, store, price = book
    b.buy(price["now"], datetime.now(UTC) - timedelta(days=1), manual=False)
    price["now"] = Decimal(95_000)
    store.requests.append((2, "ladder.sell_request",
                           {"part": b.parts[0].opened.isoformat()}))
    b.manual_requested(None)
    assert store.answers[2]["result"] == "sold"
    assert not b.parts
    sale = b.sales[-1]
    assert sale["entry"] == "rule" and sale["exit"] == "manual"
    assert sale["gain"] < 0
    assert b.paused_until is None, "selling one part does not pause buying"


@pytest.mark.fast
def test_a_part_bought_by_hand_keeps_its_mark_through_a_rule_sale(book) -> None:
    _, b, store, price = book
    store.requests.append((3, "ladder.buy_request", {}))
    b.manual_requested(None)
    price["now"] = Decimal(115_000)
    b.step(price["now"], datetime.now(UTC))
    assert b.sales[-1]["entry"] == "manual" and b.sales[-1]["exit"] == "cap"
    assert not b.manual


@pytest.mark.fast
def test_a_manual_buy_is_refused_when_every_part_is_held(book) -> None:
    _, b, store, price = book
    b.cfg = lad.Config(budget=2_000, parts=1)
    b.buy(price["now"], datetime.now(UTC), manual=False)
    store.requests.append((4, "ladder.buy_request", {}))
    b.manual_requested(None)
    assert store.answers[4] == {"result": "refused", "why": "all parts are held"}


@pytest.mark.fast
def test_the_mark_and_the_sales_survive_a_restart(book) -> None:
    run, b, store, _ = book
    store.requests.append((5, "ladder.buy_request", {}))
    b.manual_requested(None)
    again = run.Book(store, b.adapter.instruments["BTCUSDT"], b.cfg)
    assert again.manual == b.manual and again.parts[0].opened == b.parts[0].opened
