"""What a week would have done, across every week there is. §14.1, §14.5, §15.

One week is one draw. Reporting a single one as "the gain" is the shape of
answer that makes a bad week look like a bad system and a good week like an
edge, so this runs every week in the panel and reports the distribution.

**There is no strategy.** Nothing has cleared §15, so what is held is the
reference: an equal-weighted basket of whatever the capital can actually open,
bought at the start of the week and sold at the end. Every figure below is the
market's, sized by what the limits and the venue allow, less costs. It is a
measurement of *capital adequacy*, not of an edge.

Lot sizes and notional minimums come from Binance rather than from an
assumption. The first version of the capital simulation hard-coded BTC's
minimum at 100 USDT when it is 50, which is the kind of number worth reading
from the venue rather than remembering.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np
import polars as pl

from tradelab.engines.tier_a.costs import BINANCE_RETAIL
from tradelab.risk.limits import RiskLimits

EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
WEEK = 7


def venue_rules() -> dict[str, tuple[Decimal, Decimal]]:
    """(lot size, minimum notional) per asset, from the venue."""
    payload = httpx.get(EXCHANGE_INFO, timeout=30).json()
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for symbol in payload["symbols"]:
        if symbol.get("contractType") != "PERPETUAL" or symbol.get("quoteAsset") != "USDT":
            continue
        filters = {f["filterType"]: f for f in symbol["filters"]}
        try:
            out[symbol["symbol"].removesuffix("USDT")] = (
                Decimal(filters["LOT_SIZE"]["stepSize"]),
                Decimal(filters["MIN_NOTIONAL"]["notional"]))
        except KeyError:
            continue
    return out


def price_panel(root: Path) -> tuple[np.ndarray, list[str]]:
    rows = pl.concat([pl.read_parquet(f) for f in sorted(root.glob("*.parquet"))])
    rows = rows.filter(pl.col("venue") == "binance").drop_nulls("close")
    wide = rows.pivot(index="day", on="asset", values="close").sort("day")
    assets = [a for a in wide.columns[1:]
              if wide[a].null_count() == 0]
    return np.array([wide[a].to_numpy().astype(float) for a in assets]), assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=Decimal, default=Decimal(100))
    parser.add_argument("--root", type=Path,
                        default=Path("data/parquet/funding_multivenue"))
    args = parser.parse_args()

    limits = RiskLimits()
    rules = venue_rules()
    prices, assets = price_panel(args.root)
    print(f"capital {args.capital:,.2f} USDT, {len(assets)} assets with a full "
          f"history, {prices.shape[1]} days\n")

    cap_per_position = min(args.capital * limits.max_position_pct_equity,
                           limits.max_position_notional)
    print(f"cap per position    : {cap_per_position:,.2f} "
          f"({limits.max_position_pct_equity:.0%} of equity)")
    print(f"positions allowed   : {limits.max_open_positions}")

    fee = Decimal(str(BINANCE_RETAIL.per_side))
    results: list[float] = []
    openable_counts: list[int] = []

    for start in range(0, prices.shape[1] - WEEK, WEEK):
        entry, exit_ = prices[:, start], prices[:, start + WEEK]

        # Which assets the capital can actually open at this week's prices.
        # min_viable is the larger of the venue's notional floor and one lot,
        # per §14.1 — reading min_notional alone gives the wrong floor.
        openable = []
        for index, asset in enumerate(assets):
            rule = rules.get(asset)
            if rule is None or entry[index] <= 0:
                continue
            lot, floor = rule
            viable = max(floor, lot * Decimal(str(entry[index])))
            if viable <= cap_per_position:
                openable.append((index, lot, viable))
        openable_counts.append(len(openable))
        if not openable:
            results.append(0.0)
            continue

        # Equal weight across as many as the limits allow, cheapest first so
        # the quantisation wastes least.
        openable.sort(key=lambda row: row[2])
        chosen = openable[: limits.max_open_positions]
        each = min(cap_per_position, args.capital / len(chosen))

        deployed = Decimal(0)
        gain = Decimal(0)
        for index, lot, _ in chosen:
            price = Decimal(str(entry[index]))
            quantity = (each / price // lot) * lot
            if quantity <= 0:
                continue
            notional = quantity * price
            deployed += notional
            moved = Decimal(str(exit_[index] / entry[index])) - 1
            # Entry and exit, both charged.
            gain += notional * moved - notional * fee * 2
        results.append(float(gain / args.capital))

    outcomes = np.array(results) * 100
    print(f"assets openable     : median {int(np.median(openable_counts))} "
          f"of {len(assets)}\n")
    print(f"{len(outcomes)} weeks simulated\n")
    print(f"  median week       : {np.median(outcomes):+7.2f}%  "
          f"({args.capital * Decimal(str(np.median(outcomes))) / 100:+,.2f})")
    print(f"  mean week         : {outcomes.mean():+7.2f}%")
    print(f"  best / worst      : {outcomes.max():+7.2f}% / {outcomes.min():+7.2f}%")
    print(f"  weeks positive    : {np.mean(outcomes > 0):.0%}")
    for pct in (5, 25, 75, 95):
        print(f"  {pct:>2}th percentile  : {np.percentile(outcomes, pct):+7.2f}%")
    print(f"\n  a week's outcome is {outcomes.std(ddof=1):.2f}% wide (one standard "
          f"deviation), against a median of {np.median(outcomes):+.2f}%.")
    print("  Reporting one week as the answer would be reporting one draw.")
    print("\nThere is no strategy: this is the market, sized by what the limits "
          "and the venue allow, less costs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
