"""Binance announcements (Plan.md §11.1). Backfillable: the CMS endpoint paginates the
full history."""

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

CATALOGS = {48: "listing"}

SUBSCRIBER_DELAY = timedelta(seconds=60)

_IRRELEVANT: tuple[re.Pattern[str], ...] = (
    re.compile(r"bStocks|Tokenized Securit|Stock Trading|TradFi", re.I),
    re.compile(r"on Earn|Buy Crypto|Convert|VIP Loan|Collateral Asset", re.I),
    re.compile(r"Trading Bots|Margin Will Add New Pairs", re.I),
    re.compile(r"Adds? (BUSD )?Margin (Trading|Pairs)|Stablecoin Trading Pairs", re.I),
    re.compile(r"Lottery|Leveraged Token", re.I),
    re.compile(r"Copy Trading", re.I),
    re.compile(r"Listing (is )?(Cancell?ed|Postponed|Delayed|Suspended)", re.I),
    re.compile(r"Will Cancel.*Delisting", re.I),
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("futures_delivery", re.compile(
        r"(Quarterly|Bi-Quarterly|Delivery).{0,40}(Contract|Futures)"
        r"|Futures Will List.*?(Delivery|Quarterly)", re.I)),
    ("futures_listing", re.compile(
        r"Futures Launch(es)?.*?Perpetual Contract"
        r"|Futures Will Launch.*?Perpetual Contract"
        r"|Will (Launch|List).{0,40}Perpetual Contract"
        r"|(Will Launch|Adds?).{0,60}on Margin (&|and) Futures"
        r"|Will (Be Available|Open for Trading).{0,40}Binance Futures"
        r"|Futures: New Listings This Week", re.I)),
    ("futures_delisting", re.compile(r"Futures Will (Delist|Close|Remove|Settle)", re.I)),
    ("spot_delisting", re.compile(r"Will Delist|Notice of Removal", re.I)),
    ("seed_tag", re.compile(r"Seed Tag", re.I)),
    ("margin_change", re.compile(r"(Leverage|Margin) (Tier|Bracket|Adjust)", re.I)),
    ("trading_halt", re.compile(r"Temporary Trading Halt|Will Suspend Trading", re.I)),
    ("trading_resume", re.compile(r"Will Resume Trading|Resumption of", re.I)),
    ("spot_listing", re.compile(r"Will List\b|Binance Lists\b", re.I)),
)

ASSET_BEARING = frozenset({
    "futures_listing", "futures_delisting", "spot_listing", "spot_delisting", "seed_tag",
    "trading_halt", "trading_resume",
})

_TICKER = re.compile(r"\(([^\s()]{2,14})\)")
_PAIR = re.compile(r"\b(\w{2,12}(?:USDT|USDC))\b", re.UNICODE)

_QUOTE = r"USD[ⓈS]?|USDT|USDC|BUSD|COIN|USD1"
_NOT_TICKER_WORDS = frozenset({
    "PERPETUAL", "CONTRACT", "CONTRACTS", "FUTURES", "LEVERAGE", "MARGINED",
    "WITH", "UP", "TO", "AND", "THE", "NEW", "LISTINGS", "THIS", "WEEK",
    "BINANCE", "WILL", "LAUNCH", "MULTIPLE", "ALPHA", "AVAILABLE", "TRADING",
    "MARGIN", "QUARTERLY", "DELIVERY", "INDEX", "COMPOSITE",
})

_ASSET_FORMS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b([A-Z0-9]{{2,}})/(?:{_QUOTE})\b"),
    re.compile(
        rf"(?:{_QUOTE})[-\s](?:M|Margined)\s+"
        rf"([A-Z0-9]{{2,}}(?:\s+and\s+[A-Z0-9]{{2,}})*)"
    ),
    re.compile(rf"\b([A-Z0-9]{{2,}})\s+(?:{_QUOTE})[-\s](?:M|Margined)\b"),
)
_NOT_A_TICKER = re.compile(r"^[\d\W_]+$|\d{4}-\d{2}", re.UNICODE)


def classify(title: str) -> str:
    if any(pattern.search(title) for pattern in _IRRELEVANT):
        return "not_applicable"
    for name, pattern in _PATTERNS:
        if pattern.search(title):
            return name
    return "unclassified"


_QUOTE_SUFFIX = ("USDT", "USDC", "BUSD", "USD1", "USD")


def base_ticker(symbol: str) -> str:
    """`BLURUSDT` -> `BLUR`; unchanged if stripping leaves nothing.

    Not applied in `extract_assets`, which stays faithful to the title."""
    upper = symbol.upper()
    for suffix in _QUOTE_SUFFIX:
        if upper.endswith(suffix) and len(upper) > len(suffix) + 1:
            return upper[: -len(suffix)]
    return upper


def extract_assets(title: str) -> list[str]:
    """Every asset named in the title, order-preserving and unique.

    Accumulates across forms (a title can carry several assets)."""
    found: list[str] = []
    for pattern in _ASSET_FORMS:
        for match in pattern.findall(title):
            found.extend(re.split(r"\s+and\s+", match))
    found.extend(_PAIR.findall(title))
    if not found:
        found.extend(_TICKER.findall(title))

    seen: dict[str, None] = {}
    for symbol in found:
        symbol = symbol.strip()
        if not symbol or _NOT_A_TICKER.search(symbol) or symbol.upper() in _NOT_TICKER_WORDS:
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

        unknown = batch.frame.filter(pl.col("event_class") == "unclassified").height
        if unknown / batch.frame.height > 0.15:
            issues.append(Issue(
                kind=IssueKind.CROSS_INCONSISTENT, severity=Severity.WARN,
                period_start=times.min(), period_end=times.max(),  # type: ignore[arg-type]
                detail={"unclassified_fraction": round(unknown / batch.frame.height, 3),
                        "hint": "wording likely changed; extend the closed taxonomy"},
            ))

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
