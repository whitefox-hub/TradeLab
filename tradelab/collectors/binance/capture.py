"""Capture of data that cannot be backfilled. Plan.md §4.1, §4.5.

This module is the most time-sensitive code in the project, and the reason is
arithmetic rather than engineering: liquidations have **no historical REST
endpoint at all**, and open interest reaches back roughly 30 days. Every week
these collectors are not running is a week of history that never exists.

Both feeds are public. No account, no API key (§4.7).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import polars as pl
import structlog
import websockets

from tradelab.collectors import schemas
from tradelab.storage.writer import next_part, write_partition

log = structlog.get_logger(__name__)

WS = "wss://fstream.binance.com/ws"
REST = "https://fapi.binance.com/fapi/v1"
VENUE = "binance"


class _Buffer:
    """Accumulates rows and flushes whole days as numbered parts.

    Flushing on a day boundary as well as on size keeps every file inside exactly
    one partition, so a crash costs at most one flush interval rather than
    corrupting a day.
    """

    def __init__(self, root: Path, kind: str, schema, flush_rows: int, flush_after: timedelta):
        self.root, self.kind, self.schema = root, kind, schema
        self.flush_rows, self.flush_after = flush_rows, flush_after
        self.rows: list[dict] = []
        self.last_flush = datetime.now(UTC)
        # Counts rows that reached disk, not rows accepted. The watermark may
        # only ever advance on this number.
        self.flushed_rows = 0

    def add(self, row: dict) -> None:
        self.rows.append(row)

    def due(self) -> bool:
        return (
            len(self.rows) >= self.flush_rows
            or datetime.now(UTC) - self.last_flush >= self.flush_after
        )

    def flush(self) -> list[Path]:
        if not self.rows:
            return []
        frame = (
            pl.DataFrame(self.rows, schema_overrides={"revision": pl.Int16})
            .with_columns(pl.col("event_time").dt.date().alias("_day"))
            .sort("event_time")
        )
        written: list[Path] = []
        for (asset, day), chunk in frame.group_by(["asset", "_day"], maintain_order=True):
            part = next_part(self.root, self.kind, VENUE, asset, day)  # type: ignore[arg-type]
            written.append(
                write_partition(
                    chunk.drop("_day"), self.schema, self.root, self.kind,  # type: ignore[arg-type]
                    VENUE, asset, day, part=part,  # type: ignore[arg-type]
                )
            )
        self.flushed_rows += frame.height
        self.rows.clear()
        self.last_flush = datetime.now(UTC)
        return written


class LiquidationCapture:
    """`!forceOrder@arr` — every liquidation on USD-M futures, all symbols.

    Not backfillable under any circumstances. This is the differentiating dataset
    of §11 and the input to the liquidation-asymmetry component of the state
    index (§11.4).
    """

    kind = "liquidations"
    venue = VENUE

    def __init__(self, root: Path, flush_rows: int = 200,
                 flush_after: timedelta = timedelta(seconds=60)) -> None:
        # A short flush budget because this data is unrecoverable: an unclean kill
        # costs at most one interval, and the price is more small files, which is
        # a far cheaper problem than a permanent hole.
        self.buffer = _Buffer(root, self.kind, schemas.LIQUIDATIONS, flush_rows, flush_after)

    @staticmethod
    def parse(message: dict) -> dict:
        order = message["o"]
        event_time = datetime.fromtimestamp(message["E"] / 1000, UTC)
        return {
            "event_time": event_time,
            "ingest_time": datetime.now(UTC),
            # A public stream event is usable the moment it arrives. Unlike news,
            # there is no publication lag to model here.
            "available_at": datetime.now(UTC),
            "venue": VENUE,
            "asset": order["s"],
            "revision": 0,
            "side": order["S"],
            "price": float(order["ap"]) or float(order["p"]),
            "qty": float(order["q"]),
            "order_type": order.get("o"),
        }

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        backoff = 1.0
        while not stop.is_set():
            try:
                # Liveness is the protocol's job: ping/pong detects a dead peer.
                # Message silence must NOT: liquidations are bursty, and quiet
                # markets produce none for minutes at a time. Treating silence as
                # failure produces a reconnect loop that drops the bursts too.
                async with websockets.connect(
                    f"{WS}/!forceOrder@arr", ping_interval=20, ping_timeout=20
                ) as socket:
                    log.info("liquidation_stream_connected")
                    backoff = 1.0
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=5)
                        except TimeoutError:
                            # No liquidations right now. Flush if due, keep listening.
                            if self.buffer.due():
                                self.buffer.flush()
                            continue
                        self.buffer.add(self.parse(json.loads(raw)))
                        if self.buffer.due():
                            self.buffer.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A reconnect loses events. The gap is logged so it can be filed
                # as a data_quality_issue rather than silently disappearing.
                log.warning("liquidation_stream_reconnect", error=str(exc), backoff=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        self.buffer.flush()


class OpenInterestCapture:
    """Polled, because there is no websocket for open interest.

    REST history reaches about 30 days, so anything older than that simply does
    not exist unless it was captured. Polling cadence is a deliberate trade-off:
    5 minutes is fine for the 1-7 day horizon of §0.2 and keeps well inside the
    public rate limit.
    """

    kind = "open_interest"
    venue = VENUE

    def __init__(self, root: Path, assets: list[str], interval: timedelta = timedelta(minutes=5),
                 flush_after: timedelta = timedelta(minutes=10)) -> None:
        self.assets = assets
        self.interval = interval
        self.buffer = _Buffer(root, self.kind, schemas.OPEN_INTEREST, 10_000, flush_after)

    async def poll_once(self, client: httpx.AsyncClient) -> None:
        for asset in self.assets:
            try:
                payload = (
                    await client.get(f"{REST}/openInterest", params={"symbol": asset})
                ).raise_for_status().json()
            except Exception as exc:
                log.warning("open_interest_poll_failed", asset=asset, error=str(exc))
                continue
            event_time = datetime.fromtimestamp(payload["time"] / 1000, UTC)
            self.buffer.add({
                "event_time": event_time,
                "ingest_time": datetime.now(UTC),
                "available_at": datetime.now(UTC),
                "venue": VENUE,
                "asset": payload["symbol"],
                "revision": 0,
                "open_interest": float(payload["openInterest"]),
                "open_interest_value": None,
            })

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        async with httpx.AsyncClient(timeout=20.0) as client:
            while not stop.is_set():
                await self.poll_once(client)
                if self.buffer.due():
                    self.buffer.flush()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.interval.total_seconds())
        self.buffer.flush()
