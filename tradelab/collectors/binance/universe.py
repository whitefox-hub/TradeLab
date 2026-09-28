"""Asset universe and exchange constraints from Binance USD-M futures (Plan.md §4.4).

`exchangeInfo` is a snapshot (current constraints, no past values), so each scheduled run
is a new `valid_from` row."""

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
                # onboardDate is the venue's listing timestamp.
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
    """Deterministic phase-1 selection, ordered by listing date, not current volume (§4.4)."""
    eligible = [
        r for r in rows
        if r["quote_asset"] == quote and r["status"] == "TRADING"
    ]
    eligible.sort(key=lambda r: (r["listed_at"], r["asset"]))
    selected = [r["asset"] for r in eligible]
    return selected[:limit] if limit else selected
