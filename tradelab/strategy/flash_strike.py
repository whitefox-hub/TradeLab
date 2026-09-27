"""Flash Strike, as registered. research/PREREG-008-flash-strike.md.

Rules 1 to 3 and the exits, bar by bar, on one asset's 5-minute series. No model
and no book: this module proposes candidates, fills them or not, and records
what each would have earned under each of the 12 registered exit
configurations. Deciding which to take, and sizing them against one another,
belongs to the run script.

Everything is indexed by bar close (`available_ms`). A quantity used at bar
*t* is computed from bars closed at or before *t*, and the lookahead probe in
the run script perturbs a bar to prove it.

The trade outcome follows §7's discipline without using `labeling.barriers`
directly. That module labels every bar from its close, with widths of sigma
times the square root of the horizon. The registered trade enters on a limit
fill at the shock's VWAP, with stops in ATR from that fill and gaps executed at
the bar's worst price, and the barrier module cannot express that trade. The
two rules that matter are kept exactly: **the stop wins a bar that touches both barriers**, and each
outcome records **when it was determined**, which is what the purge must cover.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import polars as pl

BARS_PER_DAY = 288
ATR_BARS = BARS_PER_DAY               # trailing 24 h
QUANTILE_BARS = 30 * BARS_PER_DAY     # trailing 30 days for the anomaly threshold
QUANTILE = 0.99
COOLDOWN_BARS = 6                     # 30 minutes
ORDER_LIFE_BARS = 6                   # 30 minutes

SL_MULTS = (0.5, 1.0)
TP_MULTS = (1.0, 1.5, 2.0)            # multiples of the stop distance
HOLD_MINUTES = (30, 120)
CONFIGS: tuple[tuple[float, float, int], ...] = tuple(product(SL_MULTS, TP_MULTS, HOLD_MINUTES))

MAKER = 0.0002
TAKER = 0.0005 + 0.00005 + 0.0001     # fee + half spread + slippage


@dataclass(frozen=True, slots=True)
class Series:
    """One asset's aligned 5-minute arrays. Missing metrics are NaN."""

    close_ms: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    trades: np.ndarray
    taker_buy_quote: np.ndarray
    premium: np.ndarray
    open_interest: np.ndarray
    toptrader_ratio: np.ndarray
    tick: np.ndarray                  # per bar, inferred from the month's prices


# -- indicators, each known at the close of its bar ---------------------------


def trailing_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Mean of the `window` bars *before* t, so bar t is never inside its own
    reference."""
    out = np.full(values.size, np.nan)
    clean = np.nan_to_num(values)
    counts = np.concatenate([[0], np.cumsum(np.isfinite(values))])
    sums = np.concatenate([[0.0], np.cumsum(clean)])
    for_t = np.arange(window, values.size)
    n = counts[for_t] - counts[for_t - window]
    out[for_t] = np.where(n > window // 2, (sums[for_t] - sums[for_t - window])
                          / np.maximum(n, 1), np.nan)
    return out


def trailing_std(values: np.ndarray, window: int) -> np.ndarray:
    mean = trailing_mean(values, window)
    square = trailing_mean(values * values, window)
    out: np.ndarray = np.sqrt(np.maximum(square - mean * mean, 0.0))
    return out


def true_range(s: Series) -> np.ndarray:
    previous = np.concatenate([[s.close[0]], s.close[:-1]])
    out: np.ndarray = np.maximum(s.high, previous) - np.minimum(s.low, previous)
    return out


def trailing_quantile(values: np.ndarray, window: int, q: float) -> np.ndarray:
    """The q-quantile of the `window` bars before t, via polars' rolling kernel
    on a one-bar lag."""
    lagged = np.concatenate([[np.nan], values[:-1]])
    return (pl.Series(lagged).rolling_quantile(q, window_size=window,
                                               min_samples=window // 2)
            .to_numpy().astype(np.float64))


# -- rule 1: the anomaly -------------------------------------------------------


def shocks(s: Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bars whose move is in the asset's own top 1%, in ATR.

    Returns the event indices, each event's direction and the ATR at the shock.
    The threshold is the trailing 30-day 99th percentile of the same ratio, so
    it is the market's, not ours.
    """
    atr = trailing_mean(true_range(s), ATR_BARS)
    ratio = np.abs(s.close - s.open) / atr
    threshold = trailing_quantile(ratio, QUANTILE_BARS, QUANTILE)
    fired = np.isfinite(ratio) & np.isfinite(threshold) & (ratio >= threshold)
    events: list[int] = []
    last = -COOLDOWN_BARS - 1
    for t in (int(i) for i in np.flatnonzero(fired)):
        if t - last > COOLDOWN_BARS:
            events.append(t)
            last = t
    idx = np.asarray(events, dtype=np.int64)
    return idx, np.sign(s.close[idx] - s.open[idx]).astype(np.int8), atr[idx]


# -- rules 2 and 3: the pullback and persistence -------------------------------


@dataclass(frozen=True, slots=True)
class Fill:
    shock: int
    fill: int
    side: int
    price: float
    atr: float


def fill(s: Series, shock: int, side: int, atr: float) -> Fill | None:
    """A resting limit at the shock bar's VWAP, filled only on a trade-through.

    Persistence is checked at the fill, from bars closed before it. The taker
    buy share from the shock to the bar before the fill must be on the shock's
    side, and open interest must not have fallen since the shock closed.
    """
    if s.volume[shock] <= 0 or not np.isfinite(atr) or side == 0:
        return None
    vwap = s.quote_volume[shock] / s.volume[shock]
    last = min(shock + ORDER_LIFE_BARS, s.close.size - 1)
    for f in range(shock + 1, last + 1):
        tick = s.tick[f]
        through = (s.low[f] <= vwap - tick) if side > 0 else (s.high[f] >= vwap + tick)
        if not through:
            continue
        window = slice(shock, f)
        quote = s.quote_volume[window].sum()
        share = s.taker_buy_quote[window].sum() / quote if quote > 0 else 0.5
        on_side = share > 0.5 if side > 0 else share < 0.5
        oi_then, oi_now = s.open_interest[shock], s.open_interest[f - 1]
        oi_held = np.isfinite(oi_then) and np.isfinite(oi_now) and oi_now >= oi_then
        if on_side and oi_held:
            return Fill(shock, f, side, float(vwap), float(atr))
        return None
    return None


# -- the exits, under every registered configuration ---------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    net: float            # return on notional, after costs and funding
    reason: int           # 1 take profit, -1 stop, 0 time
    exit_bar: int         # when the outcome was determined


def exit_trade(s: Series, entry: Fill, sl_mult: float, tp_mult: float,
               hold_minutes: int, funding_per_bar: np.ndarray) -> Outcome | None:
    """Walk forward from the fill until a barrier or the clock ends it.

    In the fill bar only the stop can trigger, because a limit filled inside a
    bar may have filled at its end. When one bar touches both barriers the stop
    came first. A stop the bar opens through executes at the bar's worst price.
    A take profit, being a resting limit, needs a trade-through by one tick.
    """
    side, price = entry.side, entry.price
    distance = sl_mult * entry.atr
    stop = price - side * distance
    target = price + side * tp_mult * distance
    horizon = entry.fill + hold_minutes // 5
    if horizon >= s.close.size:
        return None

    for j in range(entry.fill, horizon + 1):
        worst = s.low[j] if side > 0 else s.high[j]
        best = s.high[j] if side > 0 else s.low[j]
        stopped = (worst <= stop) if side > 0 else (worst >= stop)
        if stopped:
            gapped = (s.open[j] <= stop) if side > 0 else (s.open[j] >= stop)
            out = worst if gapped and j > entry.fill else stop
            return _close(s, entry, out, j, -1, MAKER + TAKER, funding_per_bar)
        tick = s.tick[j]
        hit = (best >= target + tick) if side > 0 else (best <= target - tick)
        if j > entry.fill and hit:
            return _close(s, entry, target, j, 1, MAKER + MAKER, funding_per_bar)
    return _close(s, entry, float(s.close[horizon]), horizon, 0, MAKER + TAKER,
                  funding_per_bar)


def _close(s: Series, entry: Fill, out: float, bar: int, reason: int,
           costs: float, funding_per_bar: np.ndarray) -> Outcome:
    gross = entry.side * (out - entry.price) / entry.price
    # Funding accrues to the side paying it across the bars held. Positive
    # funding is paid by longs.
    funding = entry.side * float(np.nansum(funding_per_bar[entry.fill:bar + 1]))
    return Outcome(gross - costs - funding, reason, bar)


# -- the thirteen features, known at the fill ----------------------------------

FEATURES = (
    "shock_atr", "shock_volume_z", "shock_taker_share", "oi_change",
    "oi_change_signed", "forced_flow", "funding_z", "premium_change",
    "toptrader_change", "trade_size_z", "btc_return", "vol_ratio",
    "minutes_to_fill",
)


def features(s: Series, entry: Fill, funding_z: float, btc_return: float) -> dict[str, float]:
    t, f = entry.shock, entry.fill
    move = s.close[t] - s.open[t]

    def z(values: np.ndarray) -> float:
        mean = np.nanmean(values[max(t - ATR_BARS, 0):t])
        std = np.nanstd(values[max(t - ATR_BARS, 0):t])
        return float((values[t] - mean) / std) if std > 0 else 0.0

    oi_before = s.open_interest[t - 1] if t > 0 else np.nan
    oi_change = (s.open_interest[t] / oi_before - 1.0
                 if np.isfinite(oi_before) and oi_before > 0 else np.nan)
    size = np.divide(s.quote_volume, s.trades, out=np.full(s.trades.size, np.nan),
                     where=s.trades > 0)
    returns = np.diff(np.log(s.close), prepend=np.log(s.close[0]))
    vol_1h = np.std(returns[max(t - 11, 0):t + 1])
    vol_24h = np.std(returns[max(t - ATR_BARS + 1, 0):t + 1])
    top_then = s.toptrader_ratio[t - 12] if t >= 12 else np.nan
    return {
        "shock_atr": float(abs(move) / entry.atr),
        "shock_volume_z": z(s.quote_volume),
        "shock_taker_share": float(s.taker_buy_quote[t] / s.quote_volume[t])
        if s.quote_volume[t] > 0 else 0.5,
        "oi_change": float(oi_change),
        "oi_change_signed": float(oi_change * np.sign(move)),
        "forced_flow": float(abs(move) / entry.atr * max(0.0, -oi_change))
        if np.isfinite(oi_change) else np.nan,
        "funding_z": funding_z,
        "premium_change": float(s.premium[t] - s.premium[t - 1]) if t > 0 else np.nan,
        "toptrader_change": float(s.toptrader_ratio[t] - top_then),
        "trade_size_z": z(size),
        "btc_return": btc_return,
        "vol_ratio": float(vol_1h / vol_24h) if vol_24h > 0 else np.nan,
        "minutes_to_fill": float((f - t) * 5),
    }


def infer_tick(prices: np.ndarray) -> float:
    """The smallest positive gap between distinct prices printed in a month.

    Point in time, unlike `exchangeInfo`, which reports today's tick for every
    past month, and ticks change when a price moves by orders of magnitude.
    """
    distinct = np.unique(prices[np.isfinite(prices) & (prices > 0)])
    gaps = np.diff(distinct)
    gaps = gaps[gaps > 0]
    return float(gaps.min()) if gaps.size else float("nan")
