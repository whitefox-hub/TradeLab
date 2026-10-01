"""Hard limits and the kill switch. Plan.md §14.1.

Independent of the strategy: state is counted here from observed fills, never from what the
strategy reports, and limits apply in every mode (§12.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tradelab.events.context import EventContext
from tradelab.execution.types import AccountState, Instrument, Order


class Breach(StrEnum):
    """Why an order was refused. Named, because "rejected" is not actionable."""

    HALTED = "halted"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_OPEN_ORDERS = "max_open_orders"
    MAX_POSITIONS_PER_CLUSTER = "max_positions_per_cluster"
    MIN_POSITION_NOTIONAL = "min_position_notional"
    MAX_POSITION_NOTIONAL = "max_position_notional"
    MIN_POSITION_PCT_EQUITY = "min_position_pct_equity"
    MAX_POSITION_PCT_EQUITY = "max_position_pct_equity"
    MIN_SECONDS_BETWEEN_ENTRIES = "min_seconds_between_entries"
    MAX_GROSS_EXPOSURE = "max_gross_exposure"
    MAX_NET_EXPOSURE = "max_net_exposure"
    MAX_LEVERAGE = "max_leverage"
    MAX_DAILY_LOSS = "max_daily_loss"
    MAX_DRAWDOWN_FROM_PEAK = "max_drawdown_from_peak"
    MAX_ORDERS_PER_MINUTE = "max_orders_per_minute"
    MAX_CONSECUTIVE_ERRORS = "max_consecutive_errors"
    PRICE_DEVIATION = "price_deviation"
    BELOW_MIN_VIABLE = "below_min_viable"
    EVENT_CONTEXT = "event_context"


#: Breaches that stop the whole run rather than one order.
HALTING: frozenset[Breach] = frozenset({
    Breach.MAX_DAILY_LOSS,
    Breach.MAX_DRAWDOWN_FROM_PEAK,
    Breach.MAX_CONSECUTIVE_ERRORS,
})

#: A halt a human must clear; the review is the control (§14.1).
REQUIRES_HUMAN: frozenset[Breach] = frozenset({Breach.MAX_DRAWDOWN_FROM_PEAK})


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_open_positions: int = 5
    max_open_orders: int = 10
    max_positions_per_cluster: int = 2
    min_position_notional: Decimal = Decimal(10)
    max_position_notional: Decimal = Decimal(10_000)
    min_position_pct_equity: Decimal = Decimal("0.005")
    max_position_pct_equity: Decimal = Decimal("0.25")
    min_seconds_between_entries: int = 60
    max_gross_exposure: Decimal = Decimal("2.0")   # multiples of equity
    max_net_exposure: Decimal = Decimal("1.0")
    max_leverage: Decimal = Decimal("3.0")
    max_daily_loss: Decimal = Decimal("0.05")      # fraction of day-start equity
    max_drawdown_from_peak: Decimal = Decimal("0.20")
    max_orders_per_minute: int = 20
    max_consecutive_errors: int = 3
    max_price_deviation_bps: Decimal = Decimal(50)


#: What each limit stops, one line per `RiskLimits` field (a test enforces full coverage).
STOPS: dict[str, str] = {
    "max_open_positions":
        "A book wide enough that no single position matters is also one nobody "
        "is watching.",
    "max_open_orders":
        "Unfilled orders are exposure the position count does not show.",
    "max_positions_per_cluster":
        "Five strategies in one correlation cluster are one bet (§13.1), so a "
        "per-position cap does not cap the exposure that matters.",
    "min_position_notional":
        "Below a size, fees and slippage are the whole trade.",
    "max_position_notional":
        "An absolute ceiling, independent of equity, so a mis-priced equity "
        "figure cannot authorise an unbounded order.",
    "min_position_pct_equity":
        "A position too small to move the book is a cost with no thesis.",
    "max_position_pct_equity":
        "One name cannot become the portfolio — at entry, and afterwards: a "
        "position that grows past it by price is trimmed back (RESULT-007).",
    "min_seconds_between_entries":
        "A strategy re-entering within seconds is usually a loop, not a view.",
    "max_gross_exposure":
        "Longs plus shorts, so a market-neutral book cannot be leveraged "
        "arbitrarily on the grounds of being neutral.",
    "max_net_exposure":
        "Directional exposure, which is the part a market move reaches.",
    "max_leverage":
        "Borrowed size, capped independently of gross, because the two fail "
        "differently.",
    "max_daily_loss":
        "A fraction of the day's opening equity. Halts the run: accepting the "
        "next order after a loss limit would make the limit decorative.",
    "max_drawdown_from_peak":
        "Halts, and a person has to clear it. A drawdown limit that resumes by "
        "itself has not been reviewed, and the review is the control (§14.1).",
    "max_orders_per_minute":
        "A rate no human decision produces, so exceeding it is a malfunction "
        "whatever the strategy believes.",
    "max_consecutive_errors":
        "Halts. A system failing repeatedly does not know what it holds, and "
        "the next order is a guess.",
    "max_price_deviation_bps":
        "An order priced far from the market is a fat finger or a stale feed; "
        "neither should reach a venue.",
}

#: How the risk screen's form groups and names each limit: (group, plain name, unit).
#: Unit "pct" is a fraction shown as a percentage, "x" a multiple of equity, "usd" a USDT
#: amount. Kept beside STOPS so a new limit is described and placed in one edit.
PRESENTATION: dict[str, tuple[str, str, str]] = {
    "max_position_pct_equity": ("Position size", "Largest position", "pct"),
    "min_position_pct_equity": ("Position size", "Smallest position", "pct"),
    "max_position_notional": ("Position size", "Largest position, USDT", "usd"),
    "min_position_notional": ("Position size", "Smallest position, USDT", "usd"),
    "max_gross_exposure": ("Exposure", "Gross exposure", "x"),
    "max_net_exposure": ("Exposure", "Net exposure", "x"),
    "max_leverage": ("Exposure", "Leverage", "x"),
    "max_open_positions": ("Exposure", "Open positions", ""),
    "max_positions_per_cluster": ("Exposure", "Positions per correlated cluster", ""),
    "max_daily_loss": ("Losses", "Daily loss, from the day's opening equity", "pct"),
    "max_drawdown_from_peak": ("Losses", "Drawdown from the peak", "pct"),
    "min_seconds_between_entries": ("Orders", "Seconds between entries", "s"),
    "max_open_orders": ("Orders", "Open orders", ""),
    "max_orders_per_minute": ("Orders", "Orders per minute", ""),
    "max_consecutive_errors": ("Orders", "Consecutive errors", ""),
    "max_price_deviation_bps": ("Orders", "Price deviation from mid, bp", "bp"),
}


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    breach: Breach | None = None
    detail: str = ""

    @property
    def halts(self) -> bool:
        return self.breach in HALTING

    @property
    def damped(self) -> bool:
        """Allowed, but at a size news cut down (the one case with both allowed and breach)."""
        return self.allowed and self.breach is Breach.EVENT_CONTEXT


def _refuse(breach: Breach, detail: str) -> Decision:
    return Decision(allowed=False, breach=breach, detail=detail)


class RiskEngine:
    """Vetoes orders and halts runs. Holds its own view of account state."""

    def __init__(
        self, limits: RiskLimits, account: AccountState,
        clusters: dict[str, str] | None = None,
        event_context: Callable[[str, datetime], EventContext] | None = None,
    ) -> None:
        self.limits = limits
        self.account = account
        # Injected so a backtest can supply a point-in-time view. Conditions size only,
        # never direction (§11.7).
        self.event_context = event_context
        # One correlation cluster is one bet (§13.1).
        self.clusters = clusters or {}
        self._order_times: list[datetime] = []

    # -- state the engine owns, rather than asking the strategy for ----------

    def record_order(self, at: datetime) -> None:
        self._order_times.append(at)
        cutoff = at - timedelta(minutes=1)
        self._order_times = [t for t in self._order_times if t > cutoff]

    def record_error(self) -> None:
        self.account.consecutive_errors += 1

    def record_success(self) -> None:
        self.account.consecutive_errors = 0

    def mark_equity(self, equity: Decimal) -> None:
        self.account.equity = equity
        self.account.peak_equity = max(self.account.peak_equity, equity)

    def halt(self, reason: str) -> None:
        self.account.halted = True
        self.account.halt_reason = reason

    # -- the veto ------------------------------------------------------------

    def check(
        self, order: Order, price: Decimal, instrument: Instrument,
        prices: dict[str, Decimal], at: datetime, mid_price: Decimal | None = None,
    ) -> Decision:
        """Judge one order against every limit. Order of checks is deliberate."""
        account = self.account
        limits = self.limits

        # reduce_only first, ahead of the halt check: a halted run must still get flat.
        if order.reduce_only:
            return Decision(allowed=True)

        if account.halted:
            return _refuse(Breach.HALTED, account.halt_reason)

        loss_check = self.check_account(at)
        if not loss_check.allowed:
            return loss_check

        # §14.1: reject orders far from mid. A mid <= 0 is corrupt data and refuses (never
        # skip). None skips: no reference (backtest); venue paths must supply one.
        if mid_price is not None:
            if mid_price <= 0:
                return _refuse(
                    Breach.PRICE_DEVIATION,
                    f"mid of {mid_price} is not a price; the feed is broken and "
                    "deviation cannot be checked against it",
                )
            deviation = abs(price - mid_price) / mid_price * Decimal(10_000)
            if deviation > limits.max_price_deviation_bps:
                return _refuse(
                    Breach.PRICE_DEVIATION,
                    f"{deviation:.1f}bps from mid, limit {limits.max_price_deviation_bps}",
                )

        if len(self._order_times) >= limits.max_orders_per_minute:
            return _refuse(
                Breach.MAX_ORDERS_PER_MINUTE,
                f"{len(self._order_times)} orders in the last minute",
            )

        if account.open_order_count >= limits.max_open_orders:
            return _refuse(Breach.MAX_OPEN_ORDERS, f"{account.open_order_count} open")

        position = account.positions.get(order.asset)
        opening = position is None or not position.is_open

        if opening and account.open_positions >= limits.max_open_positions:
            return _refuse(
                Breach.MAX_OPEN_POSITIONS,
                f"{account.open_positions} open, limit {limits.max_open_positions}",
            )

        if opening and (cluster := self.clusters.get(order.asset)):
            in_cluster = sum(
                1 for p in account.positions.values()
                if p.is_open and self.clusters.get(p.asset) == cluster
            )
            if in_cluster >= limits.max_positions_per_cluster:
                return _refuse(
                    Breach.MAX_POSITIONS_PER_CLUSTER,
                    f"{in_cluster} already open in cluster {cluster!r}",
                )

        if (
            position is not None
            and position.last_entry_at is not None
            and (at - position.last_entry_at).total_seconds()
            < limits.min_seconds_between_entries
        ):
            return _refuse(
                Breach.MIN_SECONDS_BETWEEN_ENTRIES,
                f"last entry {(at - position.last_entry_at).total_seconds():.0f}s ago",
            )

        # News before size, so min-viable is judged on the damped size.
        context = self.event_context(order.asset, at) if self.event_context else None
        if context is not None and context.size_multiplier < 1.0:
            order = Order(
                asset=order.asset, side=order.side,
                quantity=order.quantity * Decimal(str(context.size_multiplier)),
                order_type=order.order_type, limit_price=order.limit_price,
                reduce_only=order.reduce_only,
                client_order_id=order.client_order_id,
                strategy_id=order.strategy_id,
            )

        # Size is judged on the quantised quantity, as the venue will round.
        def with_event_reason(detail: str) -> str:
            """Append the event damping to a size refusal that news caused."""
            if context is None or context.size_multiplier >= 1.0:
                return detail
            return (f"{detail} after event damping to "
                    f"{context.size_multiplier:.0%}: " + "; ".join(context.reasons))

        quantity = instrument.quantise(order.quantity)
        if quantity <= 0:
            return _refuse(Breach.BELOW_MIN_VIABLE, with_event_reason(
                f"{order.quantity} quantises to zero at lot size "
                f"{instrument.lot_size}"))
        notional = quantity * price
        if notional < instrument.min_viable(price):
            return _refuse(Breach.BELOW_MIN_VIABLE, with_event_reason(
                f"{notional} below min_viable {instrument.min_viable(price)}"))
        if notional < limits.min_position_notional:
            return _refuse(Breach.MIN_POSITION_NOTIONAL, f"{notional}")
        if notional > limits.max_position_notional:
            return _refuse(Breach.MAX_POSITION_NOTIONAL, f"{notional}")

        if account.equity > 0:
            fraction = notional / account.equity
            if fraction < limits.min_position_pct_equity:
                return _refuse(Breach.MIN_POSITION_PCT_EQUITY, f"{fraction:.4f}")
            if fraction > limits.max_position_pct_equity:
                return _refuse(Breach.MAX_POSITION_PCT_EQUITY, f"{fraction:.4f}")

        # Portfolio exposure, computed with this order included.
        projected = dict(prices) | {order.asset: price}
        gross = account.gross_exposure(projected) + notional
        signed = quantity * price * order.side.sign
        net = account.net_exposure(projected) + signed
        if account.equity > 0:
            if gross / account.equity > limits.max_gross_exposure:
                return _refuse(
                    Breach.MAX_GROSS_EXPOSURE,
                    f"{gross / account.equity:.2f}x, limit {limits.max_gross_exposure}",
                )
            if abs(net) / account.equity > limits.max_net_exposure:
                return _refuse(
                    Breach.MAX_NET_EXPOSURE,
                    f"{abs(net) / account.equity:.2f}x, limit {limits.max_net_exposure}",
                )
            if gross / account.equity > limits.max_leverage:
                return _refuse(Breach.MAX_LEVERAGE, f"{gross / account.equity:.2f}x")

        if context is not None and context.size_multiplier < 1.0:
            return Decision(
                allowed=True, breach=Breach.EVENT_CONTEXT,
                detail=f"sized to {context.size_multiplier:.0%}: "
                       + "; ".join(context.reasons),
            )
        return Decision(allowed=True)

    def check_account(self, at: datetime) -> Decision:
        """Loss and error conditions, which halt rather than refuse one order."""
        account, limits = self.account, self.limits

        if account.consecutive_errors >= limits.max_consecutive_errors:
            return _refuse(
                Breach.MAX_CONSECUTIVE_ERRORS, f"{account.consecutive_errors} in a row"
            )

        if account.day_start_equity > 0:
            loss = (account.day_start_equity - account.equity) / account.day_start_equity
            if loss > limits.max_daily_loss:
                return _refuse(Breach.MAX_DAILY_LOSS, f"{loss:.2%} today")

        if account.peak_equity > 0:
            drawdown = (account.peak_equity - account.equity) / account.peak_equity
            if drawdown > limits.max_drawdown_from_peak:
                return _refuse(Breach.MAX_DRAWDOWN_FROM_PEAK, f"{drawdown:.2%} from peak")

        return Decision(allowed=True)

    def held_excess(self, prices: dict[str, Decimal]) -> dict[str, Decimal]:
        """How far each held position is over its size cap, in units of the asset.

        `check` only sees a position at entry; growth by price is caught here (RESULT-007).
        Cap = min(max_position_pct_equity x equity, max_position_notional); equity <= 0 means
        the whole position is excess.
        """
        limits, equity = self.limits, self.account.equity
        excess: dict[str, Decimal] = {}
        for position in self.account.positions.values():
            price = prices.get(position.asset)
            if not position.is_open or price is None or price <= 0:
                continue
            size = abs(position.quantity)
            if equity <= 0:
                excess[position.asset] = size
                continue
            cap = min(equity * limits.max_position_pct_equity,
                      limits.max_position_notional)
            if size * price > cap:
                excess[position.asset] = size - cap / price
        return excess

    def enforce(self, at: datetime) -> Decision:
        """Evaluate account conditions and halt if any has fired. Called every tick."""
        decision = self.check_account(at)
        if not decision.allowed and decision.halts:
            self.halt(f"{decision.breach}: {decision.detail}")
        return decision
