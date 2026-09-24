"""Point-in-time asset universe from Binance USD-M futures. Plan.md §4.4.

The universe is the defence against survivorship bias, and it is also where the
exchange constraints live. A backtest that sizes orders with today's `min_notional`
executes trades that would have been rejected at the time.

Caveat, stated because it matters: `exchangeInfo` is a *snapshot*. It lists what
exists now, with current tick/lot sizes, and says nothing about delisted contracts
or past parameter values. So this collector seeds the universe and must be run on
a schedule from now on — each run is a new `valid_from` row. Listing dates are
recovered separately from the first day of available kline data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

REST = "https://fapi.binance.com/fapi/v1/exchangeInfo"
VENUE = "binance"


def _filter_value(filters: list[dict], filter_type: str, key: str) -> Decimal:
    for entry in filters:
        if entry.get("filterType") == filter_type:
            return Decimal(str(entry[key]))
    raise KeyError(f"{filter_type}.{key} absent from symbol filters")


def snapshot(client: httpx.Client | None = None) -> list[dict]:
    """Current perpetual universe with its exchange constraints."""
    owned = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        payload = client.get(REST).raise_for_status().json()
    finally:
        if owned:
            client.close()

    observed_at = datetime.now(UTC)
    rows: list[dict] = []
    for symbol in payload["symbols"]:
        if symbol.get("contractType") != "PERPETUAL":
            continue
        filters = symbol["filters"]
        rows.append(
            {
                "asset": symbol["symbol"],
                "venue": VENUE,
                "valid_from": observed_at,
                # onboardDate is the venue's own listing timestamp. Where it is
                # absent, the first kline day is the fallback (see module docstring).
                "listed_at": datetime.fromtimestamp(symbol["onboardDate"] / 1000, UTC)
                if symbol.get("onboardDate")
                else observed_at,
                "delisted_at": None,
                "tick_size": _filter_value(filters, "PRICE_FILTER", "tickSize"),
                "lot_size": _filter_value(filters, "LOT_SIZE", "stepSize"),
                "min_notional": _filter_value(filters, "MIN_NOTIONAL", "notional"),
                "fee_tier": "VIP0",
                "valid_to": None,
                "status": symbol["status"],
                "quote_asset": symbol["quoteAsset"],
            }
        )
    return rows


def liquid_subset(rows: list[dict], quote: str = "USDT", limit: int | None = None) -> list[str]:
    """Deterministic asset selection for phase 1.

    Ordered by listing date, not by current volume: ranking by today's volume
    would pick the winners of the last six years, which is survivorship bias
    introduced at the moment of universe construction (§4.4).
    """
    eligible = [
        r for r in rows
        if r["quote_asset"] == quote and r["status"] == "TRADING"
    ]
    eligible.sort(key=lambda r: (r["listed_at"], r["asset"]))
    selected = [r["asset"] for r in eligible]
    return selected[:limit] if limit else selected
