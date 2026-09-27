"""What a strategy may and may not do. Plan.md step 9, §12.4.

Step 9's rule is that the decision code is byte-identical across backtest,
simulator, testnet and live, and only the data feed and the venue adapter
change. A strategy that behaves differently in simulation is one whose
simulation proved nothing, so the interface is shaped to make that hard rather
than to ask for it politely.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pytest

from tradelab.strategy.base import Context, Strategy, Target
from tradelab.strategy.reference import BuyAndHold

AT = datetime(2026, 1, 1, tzinfo=UTC)


def context(history: np.ndarray, **changes: object) -> Context:
    base = {
        "at": AT, "asset": "AAAUSDT", "history": history,
        "last_close": Decimal(100), "held": Decimal(0), "equity": Decimal(1000),
    }
    return Context(**{**base, **changes})


@pytest.mark.fast
def test_a_strategy_cannot_know_which_mode_it_is_in() -> None:
    """Byte-identical across modes means the mode is not reachable from here.

    Checked against the interface rather than against each strategy: a rule that
    each implementation has to remember is one an implementation will forget.
    """
    fields = set(Context.__annotations__)
    for forbidden in ("mode", "venue", "adapter", "is_live", "run_id"):
        assert forbidden not in fields, f"Context exposes {forbidden}"

    signature = inspect.signature(Strategy.decide)
    assert list(signature.parameters) == ["self", "context"], (
        "decide takes something besides the context, and whatever it is will "
        "eventually be the mode")


@pytest.mark.fast
def test_the_context_stops_before_the_bar_being_decided() -> None:
    """`history` ends at the bar being decided on and does not include it.

    §9 step 3 applies the one-bar shift centrally for exactly this reason: a
    rule each strategy has to remember is a lookahead waiting for the first
    strategy that forgets.
    """
    source = inspect.getsource(Context)
    assert "does not include it" in source

    # The simulation passes close[:i], never close[:i + 1].
    from pathlib import Path
    script = (Path(__file__).resolve().parents[2]
              / "scripts/simulate_capital.py").read_text()
    assert "history=close[:i]" in script
    assert "close[:i + 1]" not in script and "close[:i+1]" not in script


@pytest.mark.fast
def test_a_strategy_asks_for_a_fraction_and_cannot_assume_leverage() -> None:
    """Leverage is the risk engine's to grant through max_leverage, not the
    strategy's to assume. A strategy returning 3.0 would be sizing past a limit
    the engine has not been asked about."""
    Target(exposure=Decimal("0.25"))
    Target(exposure=Decimal("-1"))
    for beyond in (Decimal("1.01"), Decimal(3), Decimal(-2)):
        with pytest.raises(ValueError, match="outside -1 to 1"):
            Target(exposure=beyond)


@pytest.mark.fast
def test_the_reference_reads_nothing_and_says_so() -> None:
    """A benchmark that looked at the data would be a strategy, and a poor one
    nobody registered. §15 measures against buy-and-hold precisely because it
    has no condition."""
    reference = BuyAndHold()
    assert isinstance(reference, Strategy)

    rising = context(np.array([1.0, 2.0, 3.0]))
    falling = context(np.array([3.0, 2.0, 1.0]))
    assert reference.decide(rising).exposure == reference.decide(falling).exposure, (
        "the reference reacted to the data, which makes it a strategy")

    assert "reference" in reference.name


@pytest.mark.fast
def test_the_interface_has_one_method_so_state_cannot_hide_between_two() -> None:
    """An interface with `on_entry` and `on_exit` invites a strategy that holds
    state between them, and state held across bars is where a lookahead hides:
    the entry remembers something the exit then acts on before it was
    knowable."""
    methods = [name for name in dir(Strategy)
               if not name.startswith("_") and callable(getattr(Strategy, name, None))]
    assert methods == ["decide"], f"the interface offers {methods}"


@pytest.mark.fast
def test_entries_and_exits_both_reach_the_order_path() -> None:
    """The reference never exits, so it cannot show that the path carries one.

    This runs a strategy with a condition — long above a level, flat below —
    over a synthetic series built to cross it, and counts what the path
    produced. **Only the mechanism is asserted.** The return is not computed and
    would not mean anything if it were: a rule invented here and measured here
    is a trial, and one nobody registered.
    """
    from dataclasses import dataclass

    @dataclass(frozen=True, slots=True)
    class AboveLevel:
        name: str = "probe:above-level"
        level: float = 100.0

        def decide(self, context: Context) -> Target:
            if context.history.size == 0:
                return Target(Decimal(0), "no history yet")
            above = float(context.history[-1]) > self.level
            return Target(Decimal("0.1") if above else Decimal(0),
                          "above the level" if above else "below it")

    probe = AboveLevel()
    assert isinstance(probe, Strategy)

    # Up, down, up: two entries and two exits are reachable.
    series = np.concatenate([
        np.linspace(90, 110, 20), np.linspace(110, 90, 20),
        np.linspace(90, 110, 20), np.linspace(110, 90, 20),
    ])
    wanted = [float(probe.decide(context(series[:i])).exposure)
              for i in range(1, series.size)]
    changes = sum(1 for a, b in pairwise(wanted) if a != b)

    assert max(wanted) > 0, "the condition never opened a position"
    assert min(wanted) == 0, "the condition never closed one"
    assert changes >= 4, (
        f"only {changes} changes of state; the path has not shown it carries "
        "both entries and exits")
