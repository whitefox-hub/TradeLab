"""Binance announcements. Plan.md §11.1.

The best-behaved event source available: structured, timestamped, free, and with
hundreds of historical occurrences — which is what §11.6 needs and what free-text
news cannot provide. Listings are also the core of the differentiation thesis,
since each one is a fresh event and a genuinely independent cross-sectional
observation, unlike six more years of BTC.

Unusually for an event source, this one **is** partially backfillable: the CMS
endpoint paginates through the full history.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import polars as pl
import pyarrow as pa

from tradelab.collectors import schemas
from tradelab.collectors.base import (
    Batch,
    Issue,
    IssueKind,
    Severity,
    ValidationReport,
    check_duplicates,
    check_schema,
    check_timestamps,
)

CMS = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
VENUE = "binance"

#: Catalogue 48 is "New Cryptocurrency Listing".
CATALOGS = {48: "listing"}

#: How late a real subscriber would have seen the announcement.
#:
#: This constant is a modelling decision, not a detail, and it is versioned with
#: the collector. Using our own ingest time would make every backfilled
#: announcement "available" in 2026, which destroys them. Using the publication
#: instant assumes a zero-latency subscriber, which is a lie in the other
#: direction and flatters every backtest that uses them. 60s is the conservative
#: bound for a one-minute poller, and §11.8's pre-event flatness test is what
#: will reveal if it is set too low.
SUBSCRIBER_DELAY = timedelta(seconds=60)

#: Closed taxonomy (§11.1). A title that matches nothing becomes `unclassified`;
#: it never invents a class, because emergent classes fragment the sample into
#: cells of three events and make the impulse response unestimable.
#: Positively irrelevant to a crypto perpetuals system. Matched FIRST, so that
#: `unclassified` keeps its meaning: "we could not tell", which is an alarm,
#: rather than "not our concern", which is half this feed.
_IRRELEVANT: tuple[re.Pattern[str], ...] = (
    re.compile(r"bStocks|Tokenized Securit|Stock Trading|TradFi", re.I),
    re.compile(r"on Earn|Buy Crypto|Convert|VIP Loan|Collateral Asset", re.I),
    re.compile(r"Trading Bots|Margin Will Add New Pairs", re.I),
    # Pre-2019 spot and margin housekeeping. USD-M perpetuals launched in
    # September 2019, so most of the early feed is about instruments this system
    # does not trade.
    re.compile(r"Adds? (BUSD )?Margin (Trading|Pairs)|Stablecoin Trading Pairs", re.I),
    re.compile(r"Lottery|Leveraged Token", re.I),
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("futures_listing", re.compile(r"Futures Will Launch.*?Perpetual Contract", re.I)),
    ("futures_delivery", re.compile(r"Futures Will List.*?(Delivery|Quarterly)", re.I)),
    ("futures_delisting", re.compile(r"Futures Will (Delist|Close|Remove|Settle)", re.I)),
    ("spot_delisting", re.compile(r"Will Delist|Notice of Removal", re.I)),
    ("seed_tag", re.compile(r"Seed Tag", re.I)),
    ("margin_change", re.compile(r"(Leverage|Margin) (Tier|Bracket|Adjust)", re.I)),
    ("trading_halt", re.compile(r"Temporary Trading Halt|Will Suspend Trading", re.I)),
    ("trading_resume", re.compile(r"Will Resume Trading|Resumption of", re.I)),
    # "Binance Lists X" is the pre-2020 phrasing of "Binance Will List X".
    ("spot_listing", re.compile(r"Will List\b|Binance Lists\b", re.I)),
)

#: Classes whose events must name at least one asset to be usable in §11.3.
ASSET_BEARING = frozenset({
    "futures_listing", "futures_delisting", "spot_listing", "spot_delisting", "seed_tag",
    "trading_halt", "trading_resume",
})

# Tickers may be non-ASCII: Binance lists tokens with CJK names.
_TICKER = re.compile(r"\(([^\s()]{2,14})\)")
_PAIR = re.compile(r"\b(\w{2,12}(?:USDT|USDC))\b", re.UNICODE)
# Titles end with a parenthesised release date, which the ticker pattern happily
# captures. A phantom asset is worse than none: it never matches market data, and
# it hides the fact that the announcement needs its body resolved.
_NOT_A_TICKER = re.compile(r"^[\d\W_]+$|\d{4}-\d{2}", re.UNICODE)


def classify(title: str) -> str:
    if any(pattern.search(title) for pattern in _IRRELEVANT):
        return "not_applicable"
    for name, pattern in _PATTERNS:
        if pattern.search(title):
            return name
    return "unclassified"


def extract_assets(title: str) -> list[str]:
    """Trading pairs first, then parenthesised tickers. Order-preserving, unique."""
    found = _PAIR.findall(title) or _TICKER.findall(title)
    seen: dict[str, None] = {}
    for symbol in found:
        if _NOT_A_TICKER.search(symbol):
            continue
        seen.setdefault(symbol, None)
    return list(seen)


class BinanceAnnouncements:
    kind = "announcements"
    venue = VENUE

    def __init__(self, catalog_id: int = 48, client: httpx.Client | None = None) -> None:
        self.catalog_id = catalog_id
        self._client = client or httpx.Client(
            timeout=30.0, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
        )

    @property
    def schema(self) -> pa.Schema:
        return schemas.ANNOUNCEMENTS

    def fetch_page(self, page: int, size: int = 50) -> list[dict]:
        payload = self._client.get(
            CMS,
            params={"type": 1, "catalogId": self.catalog_id, "pageNo": page, "pageSize": size},
        ).raise_for_status().json()
        catalogs = payload.get("data", {}).get("catalogs") or []
        return catalogs[0].get("articles", []) if catalogs else []

    def _normalise(self, articles: list[dict]) -> pl.DataFrame:
        now = datetime.now(UTC)
        rows = []
        for article in articles:
            published = datetime.fromtimestamp(article["releaseDate"] / 1000, UTC)
            title = article["title"]
            event_class = classify(title)
            assets = extract_assets(title)
            rows.append({
                "event_time": published,
                "ingest_time": now,
                "available_at": published + SUBSCRIBER_DELAY,
                "venue": VENUE,
                "revision": 0,
                "article_id": str(article["id"]),
                "code": article["code"],
                "title": title,
                "catalog_id": self.catalog_id,
                "event_class": event_class,
                "assets": assets,
                "needs_body": event_class in ASSET_BEARING and not assets,
                "subscriber_delay_s": int(SUBSCRIBER_DELAY.total_seconds()),
            })
        return pl.DataFrame(rows, schema_overrides={
            "revision": pl.Int16, "catalog_id": pl.Int32, "subscriber_delay_s": pl.Int32,
        }).sort("event_time")

    def backfill(self, asset: str, start: datetime, end: datetime) -> Iterator[Batch]:
        """Paginate backwards until older than `start`. `asset` is unused: the
        feed is venue-wide, so partitions use the `_all` key."""
        page = 1
        while True:
            articles = self.fetch_page(page)
            if not articles:
                return
            frame = self._normalise(articles)
            in_range = frame.filter(
                (pl.col("event_time") >= start) & (pl.col("event_time") <= end)
            )
            if in_range.height:
                yield Batch(self.kind, VENUE, "_all", in_range, self.schema)
            if frame["event_time"].min() < start:  # type: ignore[operator]
                return
            page += 1

    def validate(self, batch: Batch) -> ValidationReport:
        issues = [
            *check_schema(batch),
            *check_timestamps(batch),
            *check_duplicates(batch, ["article_id"]),
        ]
        if not batch.frame.height:
            return ValidationReport(issues)
        times = batch.frame["event_time"]

        # Titles we could not place at all. Distinct from `not_applicable`, which
        # we positively identified as irrelevant. A rise here means the venue
        # changed its wording and the taxonomy stopped matching — a failure that
        # is invisible in the data itself.
        unknown = batch.frame.filter(pl.col("event_class") == "unclassified").height
        if unknown / batch.frame.height > 0.15:
            issues.append(Issue(
                kind=IssueKind.CROSS_INCONSISTENT, severity=Severity.WARN,
                period_start=times.min(), period_end=times.max(),  # type: ignore[arg-type]
                detail={"unclassified_fraction": round(unknown / batch.frame.height, 3),
                        "hint": "wording likely changed; extend the closed taxonomy"},
            ))

        # A listing whose asset we never extracted cannot have its reaction
        # measured (§11.3), so it is a hole, not merely untidy.
        bearing = batch.frame.filter(pl.col("event_class").is_in(list(ASSET_BEARING)))
        if bearing.height:
            missing = bearing.filter(pl.col("needs_body")).height
            if missing / bearing.height > 0.2:
                issues.append(Issue(
                    kind=IssueKind.CROSS_INCONSISTENT, severity=Severity.WARN,
                    period_start=times.min(), period_end=times.max(),  # type: ignore[arg-type]
                    detail={"assets_missing_fraction": round(missing / bearing.height, 3),
                            "hint": "multi-asset titles: fetch article bodies to resolve"},
                ))
        return ValidationReport(issues)
