"""The BTC ladder's rules, one at a time."""

from datetime import UTC, datetime, timedelta

from tradelab.strategy import btc_ladder as lad

FEE, SLIP = 0.001, 0.00015
T0 = datetime(2026, 10, 1, tzinfo=UTC)


def part(price=100.0, spent=100.0, days=0.0):
    return lad.Part(price, spent * (1 - FEE) / price, spent, T0 - timedelta(days=days))


def at(gain, p):
    """The price at which `p` would net `gain`."""
    return p.spent * (1 + gain) / (p.quantity * (1 - SLIP) * (1 - FEE))


def test_the_minimum_grows_with_the_days_held_and_the_cap_is_twice_it():
    assert [lad.minimum(d) for d in (0, 14.9, 15, 30, 45, 60, 90, 400)] == \
        [0.01, 0.01, 0.03, 0.05, 0.08, 0.12, 0.20, 0.20]
    assert [lad.cap(d) for d in (0, 15, 30, 45, 60, 90)] == [0.10, 0.10, 0.10, 0.16, 0.24, 0.40]


def test_it_never_sells_below_the_minimum():
    p = part(days=20)
    assert not lad.exit_now(p, at(0.025, p), T0, FEE, SLIP)
    assert not lad.exit_now(p, at(-0.30, p), T0, FEE, SLIP)


def test_reaching_the_minimum_starts_following_it_does_not_sell():
    p = part()
    assert not lad.exit_now(p, at(0.0101, p), T0, FEE, SLIP)
    assert p.peak is not None


def test_it_sells_two_percent_under_the_peak_and_not_before():
    p = part()
    lad.exit_now(p, at(0.06, p), T0, FEE, SLIP)
    top = p.peak
    assert not lad.exit_now(p, top * 0.981, T0, FEE, SLIP)
    assert lad.exit_now(p, top * 0.98, T0, FEE, SLIP)


def test_falling_onto_the_minimum_sells_there():
    p = part()
    lad.exit_now(p, at(0.025, p), T0, FEE, SLIP)
    assert lad.exit_now(p, at(0.0105, p), T0, FEE, SLIP)


def test_the_cap_always_sells():
    p = part(days=50)
    assert lad.exit_now(p, at(0.161, p), T0, FEE, SLIP)


def test_entries_first_then_every_one_and_a_half_percent_lower_up_to_ten_parts():
    assert lad.buy_now([], 100.0, armed=True)
    assert not lad.buy_now([], 100.0, armed=False)
    held = [part(100.0), part(98.5)]
    assert not lad.buy_now(held, 97.03, armed=True)
    assert lad.buy_now(held, 97.0225, armed=True)
    assert not lad.buy_now([part(100.0 - i) for i in range(10)], 1.0, armed=True)


def test_a_part_is_a_tenth_of_the_budget_including_what_is_held():
    assert lad.part_size(1_000.0, []) == 100.0
    assert lad.part_size(800.0, [part(spent=110.0), part(spent=110.0)]) == 102.0
    assert lad.part_size(50.0, [part(spent=100.0)] * 9) == 50.0


def test_the_ladder_also_climbs_on_a_correction_clear_of_every_part():
    held = [part(100.0)]
    assert not lad.buy_now(held, 110.0, armed=False)
    assert lad.buy_now(held, 110.0, armed=True)
    assert not lad.buy_now([part(100.0), part(110.0)], 109.0, armed=True)
    assert lad.buy_now([part(100.0), part(110.0)], 105.0, armed=True)


def test_the_config_reads_the_parameters_screen_in_percent():
    assert lad.Config.from_setting(None) == lad.DEFAULT
    cfg = lad.Config.from_setting({"budget": 2000, "parts": 20, "step_pct": 3,
                                   "correction_pct": 4, "trail_pct": 1.5})
    assert (cfg.budget, cfg.parts, cfg.step, cfg.reentry, cfg.trail) == \
        (2000, 20, 0.03, 0.04, 0.015)
    assert lad.Config.from_setting({"parts": 10, "step_pct": 1.5, "correction_pct": 5,
                                    "trail_pct": 2}).budget == 1000
    assert lad.part_size(1_000.0, [], cfg) == 50.0
    assert lad.buy_now([part(100.0)], 97.0, armed=False, cfg=cfg)
    assert not lad.buy_now([part(100.0)], 97.5, armed=False, cfg=cfg)
