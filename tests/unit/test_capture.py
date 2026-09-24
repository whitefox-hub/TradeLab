"""Capture of non-backfillable data. Plan.md §4.1."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradelab.collectors.binance.capture import LiquidationCapture
from tradelab.storage.paths import NON_BACKFILLABLE

# A real !forceOrder@arr payload, recorded from the live stream.
SAMPLE = {
    "e": "forceOrder", "E": 1758573248123,
    "o": {
        "s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC", "q": "0.014",
        "p": "106900.00", "ap": "106880.50", "X": "FILLED", "l": "0.014",
        "z": "0.014", "T": 1758573248100,
    },
}


@pytest.mark.fast
def test_parses_a_real_liquidation_payload() -> None:
    row = LiquidationCapture.parse(SAMPLE)
    assert row["asset"] == "BTCUSDT"
    assert row["side"] == "SELL"
    # Average fill price is preferred over the order price: it is what actually
    # transacted, and liquidation prices routinely slip from the order price.
    assert row["price"] == pytest.approx(106880.50)
    assert row["qty"] == pytest.approx(0.014)


@pytest.mark.fast
def test_available_at_is_never_before_the_event() -> None:
    row = LiquidationCapture.parse(SAMPLE)
    assert row["available_at"] >= row["event_time"]
    assert row["event_time"].tzinfo is UTC
    assert row["event_time"] == datetime.fromtimestamp(SAMPLE["E"] / 1000, UTC)


@pytest.mark.fast
def test_these_kinds_are_marked_non_backfillable() -> None:
    """Liquidations have no historical REST endpoint; OI reaches ~30 days.

    The constant exists so the urgency is in the code rather than in a document
    someone has to remember to read.
    """
    assert "liquidations" in NON_BACKFILLABLE
    assert "open_interest" in NON_BACKFILLABLE


@pytest.mark.fast
def test_buffer_flushes_each_day_into_its_own_partition(tmp_path) -> None:
    """A flush must never straddle a day boundary.

    Parts are numbered within a day, so a crash costs at most one flush interval
    instead of corrupting a partition.
    """
    from datetime import timedelta

    from tradelab.collectors import schemas
    from tradelab.collectors.binance.capture import _Buffer

    buffer = _Buffer(tmp_path, "liquidations", schemas.LIQUIDATIONS, 1000, timedelta(hours=1))
    for day in (21, 22):
        for asset in ("BTCUSDT", "ETHUSDT"):
            buffer.add({
                "event_time": datetime(2026, 9, day, 12, 0, tzinfo=UTC),
                "ingest_time": datetime(2026, 9, day, 12, 0, tzinfo=UTC),
                "available_at": datetime(2026, 9, day, 12, 0, tzinfo=UTC),
                "venue": "binance", "asset": asset, "revision": 0,
                "side": "SELL", "price": 100.0, "qty": 1.0, "order_type": "LIMIT",
            })
    written = buffer.flush()
    assert len(written) == 4  # 2 assets x 2 days, never a file spanning both
    assert buffer.rows == []
    assert all("_p0000.parquet" in p.name for p in written)


@pytest.mark.fast
def test_successive_flushes_increment_the_part_number(tmp_path) -> None:
    from datetime import timedelta

    from tradelab.collectors import schemas
    from tradelab.collectors.binance.capture import _Buffer

    buffer = _Buffer(tmp_path, "liquidations", schemas.LIQUIDATIONS, 1000, timedelta(hours=1))
    row = {
        "event_time": datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        "ingest_time": datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        "available_at": datetime(2026, 9, 22, 12, 0, tzinfo=UTC),
        "venue": "binance", "asset": "BTCUSDT", "revision": 0,
        "side": "BUY", "price": 100.0, "qty": 1.0, "order_type": "LIMIT",
    }
    buffer.add(dict(row))
    first = buffer.flush()
    buffer.add(dict(row))
    second = buffer.flush()
    assert "_p0000" in first[0].name
    assert "_p0001" in second[0].name  # immutable: the first file is never touched


@pytest.mark.integration
def test_websocket_egress_works() -> None:
    """Proves frames actually arrive, using a stream that is always busy.

    `btcusdt@trade` is the control, NOT `@aggTrade`: on fstream the aggregate
    stream returns nothing even when egress is healthy, and using it as a control
    produced a false "websockets are blocked" diagnosis that stood for a while.
    A control stream has to be one that is known to deliver.
    """
    import asyncio
    import json

    import websockets

    async def probe() -> int:
        count = 0
        async with websockets.connect(
            "wss://fstream.binance.com/ws/btcusdt@trade", ping_interval=20
        ) as socket:
            deadline = asyncio.get_event_loop().time() + 20
            while asyncio.get_event_loop().time() < deadline:
                try:
                    json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                    count += 1
                except TimeoutError:
                    continue
        return count

    assert asyncio.run(probe()) > 0, "No websocket frames received: check egress"


@pytest.mark.integration
def test_liquidation_stream_delivers_messages() -> None:
    """Whether Binance's liquidation streams still deliver.

    Run this only when egress is known good (the test above) and the market is
    not flat. Observed on 2026-09-24 with 24h ranges of 2.5-5.7%: zero frames in
    90s across six major symbols AND the all-market stream, while `@trade`
    flooded on the same connection. That combination is not explainable by market
    conditions, so treat a failure here as "the feed changed or something in the
    path filters it", not as "the collector is broken".
    """
    import asyncio
    import json

    import websockets

    async def probe() -> int:
        count = 0
        streams = "/".join(f"{s}@forceOrder" for s in ("btcusdt", "ethusdt", "solusdt"))
        async with websockets.connect(
            f"wss://fstream.binance.com/stream?streams={streams}", ping_interval=20
        ) as socket:
            deadline = asyncio.get_event_loop().time() + 90
            while asyncio.get_event_loop().time() < deadline:
                try:
                    json.loads(await asyncio.wait_for(socket.recv(), timeout=3))
                    count += 1
                except TimeoutError:
                    continue
        return count

    assert asyncio.run(probe()) > 0, "No liquidations in 90s across three majors"
