"""One asset's 5-minute series for Flash Strike, assembled the same way twice.

The backtest builds it from the archive, and the forward simulation builds it
from the REST endpoints. If the two assemble differently, the model is traded
on inputs it was never trained on. So both call `assemble`.

**When a reading may be used** (§4.3), measured on 2026-09-27 as the REST
endpoints publish after a bar closes: the bar itself after ~1 s, its premium
index after ~14 s, open interest after ~19 s, the top-trader ratio after ~2
minutes. A decision taken at a bar's close, as the registered rules take them,
therefore cannot have any of the auxiliary readings of that bar. The rule here
is the one a live process can keep every time:

* price bars are usable at their close;
* premium and metrics are usable from the **next** bar's close, 5 minutes after
  the moment they describe (`AUX_USABLE_AFTER_MS`).

The inputs carry the moment each reading *exists*: `available_ms` is a kline's
close, a premium bar's close, and a metrics snapshot's own time (the archive's
`create_time` plus five minutes, `intraday.METRICS_STAMP_LAG_MS`). This module
adds the publication margin. RESULT-008 was void because the second of those
two corrections was missing, and a first repair that made only the first would
still have fed each decision a reading published after it.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from tradelab.strategy import flash_strike as fs

STEP_MS = 300_000
AUX_USABLE_AFTER_MS = 300_000


def assemble(klines: pl.DataFrame, premium: pl.DataFrame | None,
             metrics: pl.DataFrame | None) -> pl.DataFrame:
    """Bars on a complete 5-minute grid, with premium and metrics usable at each.

    A missing bar becomes a zero-volume bar at the previous close, so bar
    arithmetic stays true. Metrics are joined backward within ten minutes of
    usability, so a stale snapshot is absent rather than carried.
    """
    stamps = klines["available_ms"].to_numpy()
    grid = pl.DataFrame({"available_ms": np.arange(int(stamps.min()),
                                                   int(stamps.max()) + 1, STEP_MS)})
    bars = grid.join(klines, on="available_ms", how="left").with_columns(
        pl.col("close").forward_fill())
    bars = bars.with_columns(
        [pl.col(c).fill_null(pl.col("close")) for c in ("open", "high", "low")]
        + [pl.col(c).fill_null(0) for c in ("volume", "quote_volume", "trades",
                                            "taker_buy_volume", "taker_buy_quote")])
    if premium is not None and not premium.is_empty():
        bars = bars.join(
            premium.select((pl.col("available_ms") + AUX_USABLE_AFTER_MS).alias("available_ms"),
                           pl.col("close").alias("premium")),
            on="available_ms", how="left")
    else:
        bars = bars.with_columns(pl.lit(None, dtype=pl.Float64).alias("premium"))
    if metrics is not None and not metrics.is_empty():
        bars = bars.sort("available_ms").join_asof(
            metrics.select((pl.col("available_ms") + AUX_USABLE_AFTER_MS).alias("snap_ms"),
                           "open_interest",
                           pl.col("toptrader_position_ratio").alias("toptrader"))
            .sort("snap_ms"),
            left_on="available_ms", right_on="snap_ms", strategy="backward",
            tolerance=2 * STEP_MS)
    else:
        bars = bars.with_columns(pl.lit(None, dtype=pl.Float64).alias("open_interest"),
                                 pl.lit(None, dtype=pl.Float64).alias("toptrader"))
    months = bars.select((pl.col("available_ms") - 1).cast(pl.Datetime("ms", "UTC"))
                         .dt.strftime("%Y-%m").alias("month"))["month"]
    ticks = {m: fs.infer_tick(g["close"].to_numpy())
             for (m,), g in bars.with_columns(months.alias("m")).group_by("m")}
    return bars.with_columns(months.alias("month"),
                             months.replace_strict(ticks, return_dtype=pl.Float64).alias("tick"))


def series(bars: pl.DataFrame) -> fs.Series:
    def col(name: str) -> np.ndarray:
        return bars[name].cast(pl.Float64).to_numpy()
    return fs.Series(
        close_ms=bars["available_ms"].to_numpy(), open=col("open"), high=col("high"),
        low=col("low"), close=col("close"), volume=col("volume"),
        quote_volume=col("quote_volume"), trades=col("trades"),
        taker_buy_quote=col("taker_buy_quote"), premium=col("premium"),
        open_interest=col("open_interest"), toptrader_ratio=col("toptrader"),
        tick=col("tick"))
