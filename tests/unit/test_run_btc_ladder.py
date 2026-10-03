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
    assert store.answers[1]["price"] == str(b.parts[0].price), "the fill price, not the ticker"
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
    assert store.answers[2]["price"] == str(b.sales[-1]["sold_at"])
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


@pytest.fixture
def venue_book(tmp_path, monkeypatch):
    """The book on the spot adapter, against the mocked venue of test_binance_spot."""
    from test_binance_spot import Venue, _adapter

    from tradelab.execution.types import ExecutionMode

    run = _runner()
    monkeypatch.setattr(run, "STATE_DIR", tmp_path)
    monkeypatch.setattr(run, "MODE", ExecutionMode.TESTNET)
    venue = Venue()
    adapter = _adapter(venue)
    store = FakeStore()
    store.setting = lambda key: {"budget": 2_000, "parts": 20, "step_pct": 1.5,
                                 "correction_pct": 5, "trail_pct": 2}
    b = run.start(store, adapter.instruments["BTCUSDT"], adapter)
    return run, b, store, venue, adapter


@pytest.mark.fast
def test_on_a_venue_a_part_is_the_whole_lots_that_arrived(venue_book) -> None:
    _, b, _, venue, _ = venue_book
    b.buy(Decimal(80_000), datetime.now(UTC), manual=False)
    part = b.parts[0]
    assert float(venue.btc) < 0.00124, "the commission was taken in BTC"
    assert part.quantity == pytest.approx(0.00123), "a part is only the lots a sale can deliver"
    assert part.spent == pytest.approx(0.00124 * 80_000, rel=1e-9)


@pytest.mark.fast
def test_a_sale_takes_the_rounding_left_over_by_earlier_ones(venue_book) -> None:
    _, b, _, venue, _ = venue_book
    for _ in range(2):
        b.buy(Decimal(80_000), datetime.now(UTC) - timedelta(days=1), manual=False)
        b.parts[-1].price -= 10_000
    b.sell(b.parts[0], Decimal(80_000), datetime.now(UTC), "manual")
    assert float(venue.btc) >= b.parts[0].quantity, "the part still held is still there"
    b.sell(b.parts[0], Decimal(80_000), datetime.now(UTC), "manual")
    assert float(venue.btc) < 0.00001, "what is left is under one lot"
    assert b.held() == pytest.approx(float(venue.btc))


@pytest.mark.fast
def test_on_a_venue_a_configuration_change_carries_the_parts(venue_book) -> None:
    run, b, store, _, adapter = venue_book
    b.buy(Decimal(80_000), datetime.now(UTC), manual=False)
    b.persist(datetime.now(UTC), Decimal(80_000))
    cash = b.cash
    ended = []
    store.end = lambda run_id, why: ended.append(why)
    store.setting = lambda key: {"budget": 2_500, "parts": 10, "step_pct": 1.5,
                                 "correction_pct": 5, "trail_pct": 2}
    again = run.start(store, adapter.instruments["BTCUSDT"], adapter)
    assert ended and "carried" in ended[0]
    assert len(again.parts) == 1 and again.parts[0].quantity == b.parts[0].quantity
    assert again.cash == pytest.approx(cash + 500)
    assert again.cfg.parts == 10


@pytest.mark.fast
def test_rounding_never_turns_a_decided_gain_into_a_loss(venue_book) -> None:
    """The sale delivers what the decision was made on: the realised gain is never under
    the one `exit_now` saw, apart from the swept remainders, which only add."""
    _, b, _, _, _ = venue_book
    b.buy(Decimal(80_000), datetime.now(UTC), manual=False)
    part = b.parts[0]
    decided = lad.net(part, 80_000, 0.001, 0)
    b.sell(part, Decimal(80_000), datetime.now(UTC), "pullback")
    assert b.sales[-1]["gain"] >= decided - 1e-9


@pytest.mark.fast
def test_real_money_has_a_database_of_its_own(monkeypatch) -> None:
    from tradelab.config import Settings
    from tradelab.execution.types import ExecutionMode

    run = _runner()
    settings = Settings()
    for venue, mode in (("sim", ExecutionMode.SIMULATOR), ("demo", ExecutionMode.TESTNET),
                        ("live", ExecutionMode.LIVE)):
        run.configure(venue)
        assert run.MODE is mode
        url = run.database_url(settings)
        expected = settings.live_db if mode is ExecutionMode.LIVE else settings.postgres_db
        assert url.endswith(f"/{expected}"), f"{venue} writes to {url.rsplit('/', 1)[-1]}"
    assert settings.live_database_url != settings.database_url


@pytest.mark.fast
def test_the_live_database_cannot_be_the_laboratory(monkeypatch) -> None:
    from tradelab.config import Settings

    monkeypatch.setenv("TRADELAB_LIVE_DB", Settings().postgres_db)
    with pytest.raises(ValueError, match="cannot be the laboratory"):
        _ = Settings().live_database_url


@pytest.mark.fast
def test_live_stays_refused_until_its_limits_are_decided(monkeypatch) -> None:
    run = _runner()
    monkeypatch.setattr("sys.argv", ["run_btc_ladder.py", "--venue", "live"])
    with pytest.raises(SystemExit, match="live is not enabled"):
        run.main()
