"""Hard limits and the kill switch. Plan.md §14.1.

Evaluated by a component **independent of the strategy** and able to veto any
order. Two consequences follow from that word and they are the reason this
module exists at all:

* Positions and orders are counted **here**, from fills the engine observed, not
  from anything the strategy reports. A strategy that miscounts its own state is
  exactly the case these limits exist for, so trusting its count would disable
  them precisely when they are needed.
* The limits are enforced in **every** mode, backtest and simulator included
  (§12.2). A limiter that only activates in production has its first real
  execution with real money.
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


#: Breaches that stop the whole run rather than one order. Halting on a loss
#: limit and then accepting the next order would make the limit decorative.
HALTING: frozenset[Breach] = frozenset({
    Breach.MAX_DAILY_LOSS,
    Breach.MAX_DRAWDOWN_FROM_PEAK,
    Breach.MAX_CONSECUTIVE_ERRORS,
})

#: A halt a human must clear. A drawdown limit that resumes by itself has not
#: been reviewed, and the review is the control (§14.1).
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
        """Allowed, but at a size news cut down.

        A breach on an allowed decision would read as a refusal anywhere that
        only checks `breach`, so the one case where both are set has a name.
        """
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
        # How news reaches a decision. Injected rather than looked up here, so
        # the engine stays independent of where events are stored and a
        # backtest can supply a point-in-time view of its own.
        #
        # It conditions size; it does not produce a signal. §11.7 is explicit
        # that sign is an input to an expected-reaction model and not a signal,
        # so what an event earns here is a worse prior, not a direction.
        self.event_context = event_context
        # Five strategies in one correlation cluster are one bet (§13.1), so a
        # per-strategy position cap does not cap the exposure that matters.
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

        # `reduce_only` first, ahead of the halt check and everything else.
        # Getting flat is the one action a halted run still needs to take, and
        # refusing it here left `kill()` as the only exit — which reaches the
        # adapter directly and so leaves exits entirely unchecked. An order that
        # can only reduce a position cannot breach a limit by being allowed.
        if order.reduce_only:
            return Decision(allowed=True)

        if account.halted:
            return _refuse(Breach.HALTED, account.halt_reason)

        loss_check = self.check_account(at)
        if not loss_check.allowed:
            return loss_check

        if mid_price is not None and mid_price > 0:
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

        # News, before size. An event that halves the intended position has to
        # do so before the minimum-viable check, or an order gets approved at a
        # size it will never be sent at.
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

        # Size, judged on the quantised quantity: the venue will round, so
        # approving an unrounded size approves something that will not trade.
        def with_event_reason(detail: str) -> str:
            """Both size refusals say when news caused them.

            "below min_viable" on an order the strategy sized correctly is
            otherwise unaccountable — the operator sees a rejection for a size
            they never chose.
            """
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

    def enforce(self, at: datetime) -> Decision:
        """Evaluate account conditions and halt if any has fired.

        Called on every tick, not only when an order is being placed: a daily
        loss limit that is checked only when the strategy wants to trade does
        not fire on a losing position the strategy is holding quietly.
        """
        decision = self.check_account(at)
        if not decision.allowed and decision.halts:
            self.halt(f"{decision.breach}: {decision.detail}")
        return decision
