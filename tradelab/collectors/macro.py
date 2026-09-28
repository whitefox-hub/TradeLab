"""Macro series and release calendar (Plan.md §4.7, §20.6). Keyless: FRED CSV, plus Yahoo
(FRED has no usable gold; stooq CSV returns a web page).

Timing fields: `scheduled_for` (expected), `actual_at` (publication instant, not date),
`available_at` (actual_at + feed delay). FRED serves revised values today; results on a
series with `is_revised` and no `has_vintages` must be flagged."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
AGENT = {"User-Agent": "Mozilla/5.0"}


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    code: str
    name: str
    kind: str
    source: str
    symbol: str
    has_vintages: bool
    is_revised: bool
    session_close: str | None = None
    #: Seconds from release to the feed carrying it; zero for market prices.
    publish_lag_s: int = 0


CATALOGUE: tuple[SeriesSpec, ...] = (
    SeriesSpec("FEDFUNDS", "Federal funds effective rate", "rate", "fred",
               "FEDFUNDS", has_vintages=True, is_revised=True,
               session_close="17:00 America/New_York", publish_lag_s=86_400),
    SeriesSpec("DGS10", "US 10-year treasury yield", "yield", "fred",
               "DGS10", has_vintages=False, is_revised=False,
               session_close="17:00 America/New_York", publish_lag_s=57_600),
    SeriesSpec("CPIAUCSL", "US CPI, all urban consumers", "activity", "fred",
               "CPIAUCSL", has_vintages=True, is_revised=True,
               session_close="08:30 America/New_York", publish_lag_s=0),
    SeriesSpec("DTWEXBGS", "Trade-weighted US dollar index", "index", "fred",
               "DTWEXBGS", has_vintages=False, is_revised=True,
               session_close="16:00 America/New_York", publish_lag_s=57_600),
    SeriesSpec("WTI", "WTI crude oil front month", "price", "yahoo",
               "CL=F", has_vintages=False, is_revised=False),
    SeriesSpec("GOLD", "Gold front month", "price", "yahoo",
               "GC=F", has_vintages=False, is_revised=False),
    SeriesSpec("SP500", "S&P 500", "index", "yahoo",
               "^GSPC", has_vintages=False, is_revised=False),
    SeriesSpec("VIX", "CBOE volatility index", "index", "yahoo",
               "^VIX", has_vintages=False, is_revised=False),
    SeriesSpec("DGS2", "US 2-year treasury yield", "yield", "fred",
               "DGS2", has_vintages=False, is_revised=False,
               session_close="17:00 America/New_York", publish_lag_s=57_600),
    SeriesSpec("HYSPREAD", "US high-yield option-adjusted spread", "rate", "fred",
               "BAMLH0A0HYM2", has_vintages=False, is_revised=True,
               session_close="17:00 America/New_York", publish_lag_s=86_400),
)

BY_CODE = {spec.code: spec for spec in CATALOGUE}


def _fred(symbol: str, client: httpx.Client) -> list[tuple[date, float]]:
    text = client.get(FRED_CSV, params={"id": symbol}, headers=AGENT,
                      follow_redirects=True, timeout=30).text
    rows: list[tuple[date, float]] = []
    for line in text.strip().splitlines()[1:]:
        day, _, value = line.partition(",")
        # FRED writes "." for missing; skip, never zero.
        if value.strip() in {".", ""}:
            continue
        try:
            rows.append((date.fromisoformat(day.strip()), float(value)))
        except ValueError:
            continue
    return rows


def _yahoo(symbol: str, client: httpx.Client) -> list[tuple[date, float]]:
    payload = client.get(YAHOO_CHART.format(symbol=symbol),
                         params={"range": "5y", "interval": "1d"},
                         headers=AGENT, timeout=30).json()
    result = payload["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [
        (datetime.fromtimestamp(ts, UTC).date(), float(close))
        for ts, close in zip(result["timestamp"], closes, strict=True)
        if close is not None
    ]


def observations(code: str, client: httpx.Client | None = None) -> list[tuple[date, float]]:
    """Every published observation for one series, oldest first."""
    spec = BY_CODE[code]
    owned = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        rows = (_fred if spec.source == "fred" else _yahoo)(spec.symbol, client)
    finally:
        if owned:
            client.close()
    return sorted(rows)


def surprise_sigma(actual: float, consensus: float, dispersion: float) -> float | None:
    """Surprise in sigma (comparable with §11.3 reactions and §20.5 ratings).

    `None` when dispersion is unknown or zero: unmeasured, not zero."""
    if dispersion is None or dispersion <= 0:
        return None
    return (actual - consensus) / dispersion


