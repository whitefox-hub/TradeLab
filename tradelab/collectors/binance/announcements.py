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

#: Catalogue 48 is "New Cryptocurrency Listing".
CATALOGS = {48: "listing"}

#: How late a real subscriber sees an announcement: not ingest time (breaks backfill), not
#: publication (zero latency flatters backtests). 60s = one-minute poller; §11.8 tests it.
SUBSCRIBER_DELAY = timedelta(seconds=60)

#: Closed taxonomy (§11.1): unmatched titles become `unclassified`, never a new class.
#: Irrelevant patterns match FIRST so `unclassified` means "could not tell" (an alarm).
_IRRELEVANT: tuple[re.Pattern[str], ...] = (
    re.compile(r"bStocks|Tokenized Securit|Stock Trading|TradFi", re.I),
    re.compile(r"on Earn|Buy Crypto|Convert|VIP Loan|Collateral Asset", re.I),
    re.compile(r"Trading Bots|Margin Will Add New Pairs", re.I),
    # Pre-2019 spot/margin housekeeping (USD-M perpetuals launched Sept 2019).
    re.compile(r"Adds? (BUSD )?Margin (Trading|Pairs)|Stablecoin Trading Pairs", re.I),
    re.compile(r"Lottery|Leveraged Token", re.I),
    # Copy Trading notices concern existing contracts, not listings; the largest
    # false-positive source in 2024-2025.
    re.compile(r"Copy Trading", re.I),
    # Negations ("... Listing is Cancelled/Postponed") must precede listing patterns.
    # A postponed listing gets its own later announcement.
    re.compile(r"Listing (is )?(Cancell?ed|Postponed|Delayed|Suspended)", re.I),
    re.compile(r"Will Cancel.*Delisting", re.I),
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Delivery first: quarterly titles also say "Will Launch" and would match perpetuals.
    ("futures_delivery", re.compile(
        r"(Quarterly|Bi-Quarterly|Delivery).{0,40}(Contract|Futures)"
        r"|Futures Will List.*?(Delivery|Quarterly)", re.I)),
    # Perpetual listings: the venue changes wording without notice; each alternative
    # covers an era. Missing one gives a time-varying miss rate.
    ("futures_listing", re.compile(
        # 2019-2020: "Binance Futures Launches ETH/USDT Perpetual Contract".
        r"Futures Launch(es)?.*?Perpetual Contract"
        # 2020-2023.
        r"|Futures Will Launch.*?Perpetual Contract"
        # Coin-margined: "Binance Will Launch a FIL/USD Coin-Margined Perpetual Contract".
        r"|Will (Launch|List).{0,40}Perpetual Contract"
        # 2024: "Binance Will Launch Sleepless AI (AI) on Margin & Futures".
        r"|(Will Launch|Adds?).{0,60}on Margin (&|and) Futures"
        # 2025: "Obol (OBOL) Will Be Available on Binance Alpha and Binance Futures".
        r"|Will (Be Available|Open for Trading).{0,40}Binance Futures"
        # Weekly batches name no asset; they end up `needs_body`.
        r"|Futures: New Listings This Week", re.I)),
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

#: Quote and margin vocabulary, which is never the listed asset.
_QUOTE = r"USD[ⓈS]?|USDT|USDC|BUSD|COIN|USD1"
#: Words that sit in ticker position but are prose.
_NOT_TICKER_WORDS = frozenset({
    "PERPETUAL", "CONTRACT", "CONTRACTS", "FUTURES", "LEVERAGE", "MARGINED",
    "WITH", "UP", "TO", "AND", "THE", "NEW", "LISTINGS", "THIS", "WEEK",
    "BINANCE", "WILL", "LAUNCH", "MULTIPLE", "ALPHA", "AVAILABLE", "TRADING",
    "MARGIN", "QUARTERLY", "DELIVERY", "INDEX", "COMPOSITE",
})

#: Separator/margin-prefixed forms `_PAIR` misses; over half of listing titles use them.
_ASSET_FORMS: tuple[re.Pattern[str], ...] = (
    # "RLC/USDT Perpetual Contract", "FIL/USD Coin-Margined"
    re.compile(rf"\b([A-Z0-9]{{2,}})/(?:{_QUOTE})\b"),
    # "USDⓈ-M BLUR", "USDT-Margined OP", "BUSD-Margined SAND and LTC",
    # "USDC-Margined 1000SHIB", "USDⓈ-Margined VIDT"
    re.compile(
        rf"(?:{_QUOTE})[-\s](?:M|Margined)\s+"
        rf"([A-Z0-9]{{2,}}(?:\s+and\s+[A-Z0-9]{{2,}})*)"
    ),
    # "YFI USDT-Margined Perpetual Contract": ticker ahead of the margin type.
    re.compile(rf"\b([A-Z0-9]{{2,}})\s+(?:{_QUOTE})[-\s](?:M|Margined)\b"),
)
# Rejects parenthesised release dates the ticker pattern captures; a phantom asset
# would hide that the announcement needs its body resolved.
_NOT_A_TICKER = re.compile(r"^[\d\W_]+$|\d{4}-\d{2}", re.UNICODE)


def classify(title: str) -> str:
    if any(pattern.search(title) for pattern in _IRRELEVANT):
        return "not_applicable"
    for name, pattern in _PATTERNS:
        if pattern.search(title):
            return name
    return "unclassified"


#: Quote suffixes stripped so `BLUR`, `BLUR/USDT`, `USDⓈ-M BLUR` share one key.
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

        # A rise in `unclassified` means the venue changed wording.
        unknown = batch.frame.filter(pl.col("event_class") == "unclassified").height
        if unknown / batch.frame.height > 0.15:
            issues.append(Issue(
                kind=IssueKind.CROSS_INCONSISTENT, severity=Severity.WARN,
                period_start=times.min(), period_end=times.max(),  # type: ignore[arg-type]
                detail={"unclassified_fraction": round(unknown / batch.frame.height, 3),
                        "hint": "wording likely changed; extend the closed taxonomy"},
            ))

        # An asset-bearing event without assets cannot be measured (§11.3).
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
