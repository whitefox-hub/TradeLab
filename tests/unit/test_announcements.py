"""Announcement classification and asset extraction. Plan.md §11.1.

Every title here was taken from the live feed. They are kept as regression cases
because each one previously produced a wrong answer.
"""

from __future__ import annotations

import pytest

from tradelab.collectors.binance.announcements import (
    ASSET_BEARING,
    SUBSCRIBER_DELAY,
    classify,
    extract_assets,
)


@pytest.mark.fast
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Binance Futures Will Launch USDⓈ-Margined GRVTUSDT Perpetual Contract (2026-07-31)",
         "futures_listing"),
        ("Binance Will List Hyperliquid (HYPE) with Seed Tag Applied", "seed_tag"),
        ("Binance Futures Will List USDⓈ-M & COIN-M Quarterly 0326 Delivery Contracts",
         "futures_delivery"),
        # Positively irrelevant, not merely unrecognised: tokenised equities and
        # Earn/Convert additions are half this feed and are not events here.
        ("Binance Will Add 3 bStocks Tokenized Securities as Collateral Asset", "not_applicable"),
        ("Binance Will Add Hyperliquid (HYPE) on Earn, Buy Crypto, Convert, VIP Loan & Margin",
         "not_applicable"),
        ("Binance Futures Will Launch Multiple USDⓈ-Margined TradFi Perpetual Contracts",
         "not_applicable"),
    ],
)
def test_classification(title: str, expected: str) -> None:
    assert classify(title) == expected


@pytest.mark.fast
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Binance Futures Will Launch USDⓈ-Margined GRVTUSDT Perpetual Contract", ["GRVTUSDT"]),
        # Non-ASCII tickers: Binance lists tokens with CJK names.
        ("Binance Futures Will Launch USDⓈ-Margined 牛来USDT Perpetual Contract", ["牛来USDT"]),
        ("Binance Futures Will Launch USDⓈ-Margined PONSUSDT and 哈基米USDT Perpetual Contracts",
         ["PONSUSDT", "哈基米USDT"]),
        ("Binance Will List MarsCoin (MARSCOIN) with Seed Tag Applied", ["MARSCOIN"]),
    ],
)
def test_asset_extraction(title: str, expected: list[str]) -> None:
    assert extract_assets(title) == expected


@pytest.mark.fast
def test_trailing_release_date_is_not_an_asset() -> None:
    """The regression that motivated `needs_body`.

    The parenthesised release date was being captured as a ticker. A phantom
    asset is worse than an empty list: it never matches market data, and it hides
    the fact that the announcement has to be resolved from its body.
    """
    title = "Binance Futures Will Launch Multiple USDⓈ-Margined Perpetual Contracts (2026-09-18)"
    assert classify(title) == "futures_listing"
    assert extract_assets(title) == []
    assert classify(title) in ASSET_BEARING  # so `needs_body` will be set


@pytest.mark.fast
def test_subscriber_delay_is_explicit_and_positive() -> None:
    """`available_at` is publication plus a modelled delay (§11.2).

    Using ingest time would make every backfilled announcement available in the
    present, which destroys them; using publication exactly assumes a zero-latency
    subscriber, which flatters every backtest that touches them.
    """
    assert SUBSCRIBER_DELAY.total_seconds() > 0


@pytest.mark.fast
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Pre-2020 phrasing: "Binance Lists X" rather than "Binance Will List X".
        ("Binance Lists Origin Protocol (OGN)", "spot_listing"),
        ("Temporary Trading Halt for ETHBULL and ETHBEAR Trading Pairs", "trading_halt"),
        ("Binance Will Resume Trading for ETHBULL/USDT", "trading_resume"),
        # Early-feed housekeeping about instruments this system does not trade.
        ("Binance Adds BUSD Margin Pairs for BNB and ETH", "not_applicable"),
        ("Binance Adds ICX, BTS, LSK and BNT Stablecoin Trading Pairs", "not_applicable"),
    ],
)
def test_historical_phrasing(title: str, expected: str) -> None:
    """Binance's wording changed over the years.

    The closed taxonomy is extended deliberately rather than allowed to grow on
    its own, and the validator's unclassified-rate alarm is what surfaced these.
    """
    assert classify(title) == expected


@pytest.mark.fast
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # 2019-2020, present tense. The original pattern required "Will Launch"
        # and so recorded zero futures listings for the whole of 2019.
        ("Binance Futures Launches ETH/USDT Perpetual Contract - Up to 50x Leverage",
         "futures_listing"),
        # Coin-margined, where "Futures" does not precede the verb.
        ("Binance Will Launch a FIL/USD Coin-Margined Perpetual Contract With Up to 50x Leverage",
         "futures_listing"),
        # 2024 wording.
        ("Binance Will Launch Sleepless AI (AI) on Margin & Futures", "futures_listing"),
        ("Binance Adds Dymension (DYM) on Margin & Futures", "futures_listing"),
        # 2025, now the dominant form and previously missed entirely: 59 of that
        # year's listings were unclassified.
        ("Obol (OBOL) Will Be Available on Binance Alpha and Binance Futures (2025-04-17)",
         "futures_listing"),
        ("Doodles (DOOD) Will Open for Trading on Binance Alpha and Binance Futures",
         "futures_listing"),
        # Batch announcements name no asset in the title, so they must classify
        # as listings and then be caught by `needs_body`.
        ("Binance Futures: New Listings This Week (2021-03-22)", "futures_listing"),
    ],
)
def test_listing_wordings_across_seven_years(title: str, expected: str) -> None:
    """Capture was strongly time-varying before these were added.

    A listings study reads a miss rate that changes by year as a property of the
    market rather than of the parser, which is the most expensive kind of data
    defect: it survives every statistical control because it looks like signal.
    """
    assert classify(title) == expected


@pytest.mark.fast
@pytest.mark.parametrize(
    "title",
    [
        # Negations. A pattern matching "Perpetual Contract" without excluding
        # these reads every one of them as a listing on a date when nothing was
        # listed.
        "BTT USDT-Margined Perpetual Contract Listing is Cancelled",
        "FIL Coin-Margined Perpetual Contract Listing Postponed",
        "DOT USDT-Margined Perpetual Contract Listing Delayed to 2020/08/22",
        "Binance Futures Will Cancel USDⓈ-M FTTBUSD Delisting and Launch Multiple Contracts",
        # Availability of an existing contract inside a product, not a listing.
        # The largest single source of false positives: 20 titles in 2024-2025.
        "Binance Futures Copy Trading Adds New USDⓈ-M Perpetual Contracts (2025-01-14)",
    ],
)
def test_these_are_never_listings(title: str) -> None:
    assert classify(title) != "futures_listing"


@pytest.mark.fast
def test_dated_contracts_are_not_perpetual_listings() -> None:
    """Quarterly contracts are a different instrument and several say "Will Launch".

    `futures_delivery` is matched before `futures_listing` for this reason; the
    reverse order puts dated contracts into the perpetual cross-section.
    """
    quarterly = (
        "Binance Futures Will Launch SOLUSD COIN-M Quarterly and Bi-Quarterly "
        "Contracts with up to 20x Leverage"
    )
    assert classify(quarterly) == "futures_delivery"


@pytest.mark.fast
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # `_PAIR` wanted `BTCUSDT` with no separator while the feed writes
        # `RLC/USDT`. This single gap accounted for most of the 338 listings out
        # of 594 that were recorded as naming no asset at all.
        ("Binance Futures Will Launch RLC/USDT Perpetual Contract With Up to 50x Leverage",
         ["RLC"]),
        # Ticker after the margin type.
        ("Binance Futures Will Launch USDⓈ-M BLUR Perpetual Contract with Up to 20X Leverage",
         ["BLUR"]),
        # Ticker before it.
        ("Binance Futures Will Launch YFI USDT-Margined Perpetual Contract", ["YFI"]),
        # Two assets in one phrase.
        ("Binance Futures Will Launch BUSD-Margined SAND and LTC Perpetual Contracts",
         ["SAND", "LTC"]),
        # Numeric prefixes are part of the ticker.
        ("Binance Futures Will Launch USDC-Margined 1000SHIB Perpetual Contract", ["1000SHIB"]),
    ],
)
def test_asset_forms_the_pair_pattern_cannot_see(title: str, expected: list[str]) -> None:
    assert extract_assets(title) == expected


@pytest.mark.fast
def test_long_tickers_are_not_truncated_into_a_second_asset() -> None:
    """A capped capture length invents assets rather than missing them.

    `1000000BOBUSDT` is fourteen characters; a twelve-character capture yielded
    `1000000BOBUS`, which appeared in the corpus alongside the real symbol as a
    separate listing.
    """
    title = "Binance Futures Will Launch USDⓈ-M 1000000BOBUSDT Perpetual Contract"
    assert extract_assets(title) == ["1000000BOBUSDT"]


@pytest.mark.fast
def test_base_ticker_gives_one_key_per_asset() -> None:
    """The same listing is written three ways across seven years.

    Without a single key each spelling counts as its own asset, which inflates
    the cross-section that the entire power argument for this hypothesis rests
    on (§0.4).
    """
    from tradelab.collectors.binance.announcements import base_ticker

    spellings = ["BLUR", "BLURUSDT", "BLURUSDC", "BLURBUSD"]
    assert {base_ticker(s) for s in spellings} == {"BLUR"}
    # Stripping must never empty the symbol: USDC is itself listed.
    assert base_ticker("USDC") == "USDC"
