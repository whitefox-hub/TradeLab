"""Macro series and their release calendar. Plan.md §4.7, §20.6.

Two sources, chosen by what is reachable without a key rather than by
preference. FRED's public CSV endpoint serves rates, yields, WTI, the dollar
index and CPI; it has no usable gold series and stooq's CSV endpoint now returns
a web page, so gold comes from Yahoo.

The distinction this module exists to preserve is **when a number became
knowable**, not what it is. Three fields carry it and they are not
interchangeable:

* `scheduled_for` — when the market expected the print.
* `actual_at` — the publication instant. Not the date: a daily timestamp on an
  intraday release puts the event at midnight, and every reaction measured from
  it is measured from the wrong moment.
* `available_at` — `actual_at` plus the feed's own delay, which is when a
  subscriber could have acted.

A series that is revised (`is_revised`) carries a further trap. The value FRED
serves today for a month two years ago is not the value that was published then,
and using it as if it were real-time answers a different question — what we know
now rather than what was known then. `has_vintages` marks the series where that
distinction is recoverable; results depending on one that is revised but has no
vintages are flagged rather than quietly reported.
"""

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
    #: Seconds between the release instant and the moment the feed carries it.
    #: Zero where the series is a market price rather than a scheduled print.
    publish_lag_s: int = 0


#: The catalogue. Each entry states whether it is revised and whether the
#: revisions are recoverable, because those two facts decide whether a study
#: using the series is answering the question it thinks it is.
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
)

BY_CODE = {spec.code: spec for spec in CATALOGUE}


def _fred(symbol: str, client: httpx.Client) -> list[tuple[date, float]]:
    text = client.get(FRED_CSV, params={"id": symbol}, headers=AGENT,
                      follow_redirects=True, timeout=30).text
    rows: list[tuple[date, float]] = []
    for line in text.strip().splitlines()[1:]:
        day, _, value = line.partition(",")
        # FRED writes "." for a missing observation. Skipped rather than zeroed:
        # a holiday is an absence, and a zero rate is a different claim.
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
    """Surprise on the same scale as everything else the system measures.

    In sigma rather than in the series' own units, so a CPI surprise and a rate
    surprise are comparable, and so that a macro reaction sits on the same scale
    as §11.3's event reactions and §20.5's human ratings.

    `None` when dispersion is unknown or zero: a surprise with no scale is not a
    small surprise, it is an unmeasured one, and reporting it as zero would put
    every such release at the centre of the distribution.
    """
    if dispersion is None or dispersion <= 0:
        return None
    return (actual - consensus) / dispersion


def rolling_dispersion(values: list[float], window: int = 24) -> float | None:
    """Standard deviation of recent changes, as a stand-in for forecast dispersion.

    A proper dispersion is the spread of economists' forecasts, which no free
    feed carries. This substitutes the series' own recent variability, which is
    a different quantity — it says how unusual the *move* was, not how unusual it
    was *against expectations*. Named plainly so nobody later reads it as the
    real thing.
    """
    if len(values) < window + 1:
        return None
    changes = [b - a for a, b in zip(values[-window - 1:-1], values[-window:], strict=True)]
    mean = sum(changes) / len(changes)
    variance = sum((c - mean) ** 2 for c in changes) / (len(changes) - 1)
    return variance**0.5 or None
