"""DuckDB access to the Parquet archive. Plan.md §3.2.

The archive is the primary store for backtesting, not a secondary dump. Hive
partitioning lets predicates prune files before any bytes are read.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from tradelab.storage.paths import DataKind, glob_for


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    return con


def scan(
    root: Path,
    kind: DataKind,
    asset: str | None = None,
    *,
    available_before: str | None = None,
    columns: str = "*",
) -> pl.DataFrame:
    """Read from the archive.

    `available_before` filters on `available_at`, which is the ONLY timestamp a
    backtest may filter on (§4.3). It is exposed here rather than left to callers
    so that the point-in-time rule is the path of least resistance.
    """
    pattern = glob_for(root, kind)
    clauses: list[str] = []
    if asset:
        clauses.append(f"asset = '{asset}'")
    if available_before:
        clauses.append(f"available_at < TIMESTAMPTZ '{available_before}'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as con:
        return con.execute(
            f"SELECT {columns} FROM read_parquet('{pattern}', hive_partitioning=true) "
            f"{where} ORDER BY event_time"
        ).pl()


def aggregate_bars(root: Path, asset: str, target: str, source: str = "1m") -> pl.DataFrame:
    """Derive coarser bars from 1m. Plan.md §5.7: derived, not stored as primary.

    The day boundary is UTC 00:00 and is not negotiable: funding settles on UTC
    marks, and any other cut silently misaligns funding features with bars.
    """
    pattern = glob_for(root, "ohlcv")
    with connect() as con:
        return con.execute(
            f"""
            SELECT
                time_bucket(INTERVAL '{_interval(target)}', event_time) AS bucket,
                any_value(venue) AS venue, any_value(asset) AS asset,
                first(open ORDER BY event_time) AS open,
                max(high) AS high,
                min(low)  AS low,
                last(close ORDER BY event_time) AS close,
                sum(volume) AS volume,
                sum(quote_volume) AS quote_volume,
                sum(trade_count) AS trade_count,
                sum(taker_buy_base) AS taker_buy_base,
                -- Participation proxy (§11.4): falling size with rising count is
                -- retail inflow. Volume alone cannot distinguish the two.
                sum(quote_volume) / nullif(sum(trade_count), 0) AS avg_trade_size,
                max(event_time) AS event_time,
                max(available_at) AS available_at
            FROM read_parquet('{pattern}', hive_partitioning=true)
            WHERE timeframe = '{source}' AND asset = '{asset}'
            GROUP BY bucket
            ORDER BY bucket
            """
        ).pl()


def _interval(timeframe: str) -> str:
    unit, amount = timeframe[-1], int(timeframe[:-1])
    return {"m": f"{amount} minutes", "h": f"{amount} hours", "d": f"{amount} days"}[unit]
