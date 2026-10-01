"""The BTC +1% rule: it sells only once the sale nets 1% after the fee and slippage."""

import pytest

from tradelab.strategy import btc_plus_one as rule

FEE, SLIP = 0.001, 0.00015


def test_it_does_not_sell_at_a_loss_or_below_the_target():
    spent, qty = 1_000.0, 1_000.0 / 80_000 * (1 - FEE)
    assert not rule.should_sell(spent, qty, 70_000, FEE, SLIP)      # a loss: wait
    assert not rule.should_sell(spent, qty, 80_800, FEE, SLIP)      # +1% gross is not enough


def test_it_sells_at_the_target_price_and_the_sale_nets_one_percent():
    spent, qty = 1_000.0, 1_000.0 / 80_000 * (1 - FEE)
    target = rule.target_price(spent, qty, FEE, SLIP)
    assert rule.should_sell(spent, qty, target, FEE, SLIP)
    assert not rule.should_sell(spent, qty, target * 0.9999, FEE, SLIP)
    assert rule.proceeds(qty, target, FEE, SLIP) == pytest.approx(1_010.0)
    assert target / 80_000 - 1 == pytest.approx(0.01216, abs=1e-4)  # +1.2% move needed


def test_nothing_held_never_sells():
    assert not rule.should_sell(1_000.0, 0.0, 1e9, FEE, SLIP)
