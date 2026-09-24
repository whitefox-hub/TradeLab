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
