"""The market trend's effect on a position, and the limits on it. Plan.md §20, §11.3.

This is the first thing in the repository where an operator's judgement reaches
a position size, so most of what follows is about what it cannot do.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradelab.scenario.tilt import (
    SENTIMENT_BUCKETS,
    TILT_CEILING,
    apply,
    exposure_multipliers,
    sentiment_bucket,
)


@pytest.mark.fast
def test_a_normal_trend_is_the_identity() -> None:
    """The default has to change nothing, or every run ever made carries an
    adjustment nobody chose."""
    position = np.array([1.0, -0.5, 0.0, 0.25])
    assert exposure_multipliers("NORMAL", 0) == (1.0, 1.0)
    assert np.array_equal(apply(position, "NORMAL", 0), position)
    # Magnitude is stored as 0 under NORMAL, but a stray non-zero must not tilt.
    assert np.array_equal(apply(position, "NORMAL", 7), position)


@pytest.mark.fast
def test_the_trend_never_changes_a_sign() -> None:
    """It conditions, it does not signal. §11.3 puts direction as an input to an
    expected-reaction model and never a signal in itself, so a view about the
    environment may scale a position and must never decide one."""
    position = np.array([1.0, -1.0, 0.3, -0.2, 0.0])
    for direction in ("UP", "DOWN"):
        for magnitude in range(1, 11):
            out = apply(position, direction, magnitude)
            assert np.array_equal(np.sign(out), np.sign(position)), (
                f"{direction} {magnitude} changed a sign")


@pytest.mark.fast
def test_a_tilt_on_a_flat_book_is_still_flat() -> None:
    """It scales a position and never creates one. An operator's view alone
    cannot open a trade, however strongly it is held."""
    flat = np.zeros(20)
    for magnitude in range(0, 11):
        assert not apply(flat, "UP", magnitude).any()
        assert not apply(flat, "DOWN", magnitude).any()


@pytest.mark.fast
def test_the_tilt_is_bounded_at_half() -> None:
    """An operator who could turn a systematic book into a directional bet with
    one slider would have the slider as the strategy. The ceiling is what stops
    that, so it is asserted rather than left to the arithmetic."""
    assert TILT_CEILING == 0.5
    for direction in ("UP", "DOWN"):
        for magnitude in range(0, 11):
            for multiplier in exposure_multipliers(direction, magnitude):
                assert 0.5 <= multiplier <= 1.5, (
                    f"{direction} {magnitude} gives {multiplier}")


@pytest.mark.fast
def test_longs_and_shorts_move_in_opposite_directions() -> None:
    """Scaled separately, not by one factor. A single multiplier would push a
    short *further* into the market trend it disagrees with, which is the opposite of
    what was asked for and would be invisible in any aggregate."""
    for magnitude in (1, 5, 10):
        long_up, short_up = exposure_multipliers("UP", magnitude)
        assert long_up > 1.0 > short_up
        long_down, short_down = exposure_multipliers("DOWN", magnitude)
        assert long_down < 1.0 < short_down
        # And the two directions are mirrors, so neither is quietly favoured.
        assert long_up == pytest.approx(short_down)
        assert short_up == pytest.approx(long_down)


@pytest.mark.fast
def test_the_bands_are_the_ones_the_index_actually_uses() -> None:
    """Read off 3156 published days, not guessed. The guessed version put 50 in
    'Greed' and had no 'Neutral' band at all, although the series carries 401
    days of one — the kind of error that is invisible because every value still
    gets a plausible label."""
    assert [name for _, name in SENTIMENT_BUCKETS] == [
        "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"]
    assert sentiment_bucket(25) == "Extreme Fear"
    assert sentiment_bucket(26) == "Fear"
    assert sentiment_bucket(47) == "Neutral"
    assert sentiment_bucket(54) == "Neutral"
    assert sentiment_bucket(55) == "Greed"
    assert sentiment_bucket(76) == "Extreme Greed"
    # Every value on the scale gets one, including the ends.
    assert {sentiment_bucket(v) for v in range(1, 101)} == {
        name for _, name in SENTIMENT_BUCKETS}


@pytest.mark.fast
def test_the_screen_and_the_engine_agree_on_the_bands() -> None:
    """Two copies of the boundaries, one in Python and one in the page. The one
    nobody checks is the one that drifts, and a screen labelling 50 'Greed'
    while the engine files the run under 'Neutral' is a disagreement no result
    would reveal."""
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2]
            / "tradelab/api/static/market_sentiment.html").read_text()
    for ceiling, name in SENTIMENT_BUCKETS[:-1]:
        assert f'if (value <= {ceiling}) return "{name}";' in page, (
            f"the page does not put {name} at {ceiling}")


@pytest.mark.fast
def test_the_sentiment_does_not_reach_a_position() -> None:
    """§11.4 has market state conditioning a model that does not exist yet.
    Applying a number whose method is not auditable would be inventing a market
    theory and then trading it, so the bucket is recorded and nothing else."""
    import inspect

    from tradelab.scenario import tilt

    source = inspect.getsource(tilt.apply) + inspect.getsource(tilt.exposure_multipliers)
    assert "sentiment" not in source, (
        "the sentiment has started changing a position; that needs a "
        "pre-registration, not an edit")


@pytest.mark.fast
def test_a_scenario_run_has_no_mode_to_set() -> None:
    """§20.2: not a button, not a dropdown, not a config field. A scenario run
    is a simulation, and the way to keep it one is for the record it writes to
    have no column an execution mode could go in."""
    from tradelab.registry.models import Base

    table = Base.metadata.tables["scenario_runs"]
    columns = set(table.columns.keys())
    assert "mode" not in columns
    assert not table.foreign_keys, (
        "scenario_runs points at something; it must not reach the execution "
        "tables, where a run is a thing that trades")


@pytest.mark.fast
def test_a_run_reports_the_step_it_reached_rather_than_a_verdict() -> None:
    """Steps 8 to 13 are execution and need a strategy promoted through §15.
    Nothing has been. A run that could not express "it stopped here" would have
    to report a stop as a result."""
    import numpy as np_

    from tradelab.scenario.run import GATE_STEP, execute

    class _Archive:
        """Enough bars to run on, with no dependence on the real archive."""

        def rglob(self, _pattern: str) -> list:
            return []

    with pytest.raises(FileNotFoundError):
        execute("UP", 9, 74, archive=_Archive())  # type: ignore[arg-type]

    assert GATE_STEP == 7
    del np_


@pytest.mark.fast
def test_the_gate_reads_the_arm_and_not_the_asset() -> None:
    """A deflated Sharpe is computed from returns, and the returns have to be
    the ones the run earned.

    The first version gated on `diff(close)/close` — a statistic about BTCUSDT.
    With the buy-and-hold reference it agrees to within 0.04 of Sharpe, which is
    exactly why it survived; with a position that is not constantly long it is
    wrong by twelve or more, and points the wrong way. This is the shape of
    defect where one test case makes two different things coincide.
    """
    import numpy as np_

    from tradelab.engines.tier_a.engine import net_returns
    from tradelab.engines.tier_a.engine import run as backtest

    close = 100.0 * np_.cumprod(
        1.0 + np_.random.default_rng(11).normal(0.0, 0.002, 4000))

    # `run` and `net_returns` are two views of one computation, so a Sharpe
    # taken from the series has to equal the one the summary reports.
    for position in (
        np_.ones(close.size),
        (np_.arange(close.size) // 60 % 2).astype(float),
        np_.where((np_.arange(close.size) // 60) % 2 == 0, 1.0, -1.0),
    ):
        series = net_returns(close, position)
        summary = backtest(close, position, timeframe="1m")
        from_series = series.mean() / series.std(ddof=1) * np_.sqrt(525_600)
        assert from_series == pytest.approx(summary.sharpe, abs=1e-9)

    # And the asset's own returns are a different number, so gating on them
    # would not have been caught by the equality above.
    asset = np_.zeros_like(close)
    asset[1:] = np_.diff(close) / close[:-1]
    flipping = np_.where((np_.arange(close.size) // 60) % 2 == 0, 1.0, -1.0)
    assert asset.mean() / asset.std(ddof=1) != pytest.approx(
        backtest(close, flipping, timeframe="1m").sharpe, abs=0.5)


@pytest.mark.fast
def test_the_run_gates_on_the_conditioned_series() -> None:
    """Asserted against the source, because the two series are numerically
    close for the reference in use and a value test would pass either way."""
    import inspect

    from tradelab.scenario import run as runner

    source = inspect.getsource(runner.execute)
    assert "net_returns(close, conditioned_position)" in source
    assert "np.diff(close) / close[:-1]" not in source


@pytest.mark.fast
def test_step_ten_does_not_claim_a_path_it_has_not_reached() -> None:
    """The tilt runs in the scenario runner and nowhere else, and the event
    damping this step describes is constructed only in a test.

    A step described by how it ought to work reads as how it does, and this one
    is the entry point for operator judgement — the place where a false
    description costs the most. So the claim is checked against the imports
    rather than against the prose: the day the tilt reaches the execution path,
    this fails and the sentence gets rewritten.
    """
    import inspect
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "tradelab"
    importers = [
        f.relative_to(root).as_posix()
        for f in root.rglob("*.py")
        if "scenario.tilt" in f.read_text() and "scenario/" not in f.relative_to(root).as_posix()
    ]
    assert importers == [], (
        f"the tilt now reaches {importers}; step 10 says it does not, and one "
        "of the two has to change")

    from tradelab.api.app import create_app

    # A contiguous fragment: the sentence is split across two string literals
    # in the source, so the whole phrase never appears in it.
    source = inspect.getsource(create_app)
    assert "Not yet on the execution " in source
    assert "is constructed only in a test" in source


@pytest.mark.fast
def test_the_study_terms_match_the_registration() -> None:
    """PREREG-005's parameters live in two places, and the one nobody checks is
    the one that drifts. A study whose run and registration disagree cannot say
    which came first, which is the entire value of having registered it.
    """
    from pathlib import Path

    from tradelab.scenario import study

    text = (Path(__file__).resolve().parents[2]
            / "research/PREREG-005-operator-trend.md").read_text()

    assert study.CUTOFF.isoformat() in text, "the cutoff is not the registered one"
    assert f"**{study.HORIZON_DAYS} days**" in text
    assert f"**{study.FIRST_LOOK_CALLS} directional\ncalls**" in text or (
        f"**{study.FIRST_LOOK_CALLS} directional calls**" in text), (
        "the stopping point in code is not the registered one")
    assert study.MARKET in text
    assert study.MARKET_ROBUSTNESS in text, (
        "the robustness market in code is not named in the registration")
    assert study.DIRECTIONAL == ("UP", "DOWN")
    # The sensitivity horizons are declared, and declared as not deciding.
    for horizon in study.SENSITIVITY_HORIZONS:
        assert f"{horizon}-day" in text


@pytest.mark.fast
def test_the_study_counts_only_what_a_person_set() -> None:
    """The trend is becoming settable by something other than a person. A log
    that cannot say which is one PREREG-005 cannot read: a hit credited to the
    operator that was computed from a trade count would confirm the wrong
    hypothesis, and nothing afterwards could separate them."""
    from pathlib import Path

    from tradelab.api.app import ScenarioRequest
    from tradelab.registry.models import MarketConditions, ScenarioRun
    from tradelab.scenario import study

    assert study.SOURCE == "manual"
    # Recorded in every place a setting lands, so none of them can be the one
    # that forgets.
    assert "source" in ScenarioRequest.model_fields
    assert "source" in MarketConditions.__table__.columns
    assert "source" in ScenarioRun.__table__.columns

    # And defaulted to manual, which is true of every row that existed before
    # anything else could write one.
    assert ScenarioRequest(direction="UP", magnitude=5, sentiment=50).source == "manual"

    text = (Path(__file__).resolve().parents[2]
            / "research/PREREG-005-operator-trend.md").read_text()
    assert "`source = 'manual'`" in text


@pytest.mark.fast
def test_the_index_series_holds_one_definition() -> None:
    """Every stored point is a 00:00Z snapshot.

    CMC100's latest endpoint refreshes every five minutes, and the SDK types
    its timestamp as a *date*, dropping the time. A value published at 18:00
    would therefore be stored as knowable at midnight — a look-ahead introduced
    by a type, invisible afterwards because the column would hold a perfectly
    ordinary timestamp. So the series is built from the historical endpoint
    alone, which already includes the current day.
    """
    import inspect

    from tradelab.collectors import market_index

    source = inspect.getsource(market_index)
    assert "index_cmc100_historical" in source
    assert "index_cmc100_latest" not in source, (
        "the intraday endpoint is back in the series; its points mean "
        "something different from their neighbours while looking identical")
    assert market_index.LATEST_IS_INTRADAY is True


@pytest.mark.fast
def test_every_coinmarketcap_call_goes_through_one_door() -> None:
    """Two throttles arrive as a 200 with the message in the payload: a quota a
    key lifts, and a rate limit to wait out. The hand-rolled version matched
    their English text, and put the backoff inside the history loop — so the
    latest endpoint, one request and running first, failed outright on a limit
    the history fetch would have retried through.

    Throttling handled per caller is throttling handled by whichever caller was
    written second.
    """
    import inspect

    from tradelab.collectors import market_index, sentiment

    for module in (market_index, sentiment):
        source = inspect.getsource(module)
        assert "pro-api.coinmarketcap.com" not in source, (
            f"{module.__name__} calls CoinMarketCap directly")
        if "coinmarketcap" in source:
            assert "from tradelab.collectors.cmc import" in source


@pytest.mark.fast
def test_the_api_key_is_read_from_the_environment_and_never_stored() -> None:
    """§14.4 keeps credentials out of the tree. This one is loaded the way the
    operator password is — `set -a && . secrets/api.env`, which is git-ignored.

    The keyless path stays a working mode and not a degraded one: a collector
    that only runs with a credential is one that stops silently on a machine
    that does not have it.
    """
    import inspect
    import re
    import subprocess
    from pathlib import Path

    from tradelab.collectors import cmc

    source = inspect.getsource(cmc)
    assert 'os.environ.get(KEY_VAR' in source
    assert "environment=\"public\"" in source, "the keyless fallback is gone"

    # Nothing prints it. A collector that logs a key puts it in every log that
    # collector ever writes, and those outlive the key.
    #
    # Asked of the syntax tree rather than of the text: matching lines that
    # mention "log" and "key" hits the sentence above this one, which is the
    # over-blunt filter this codebase keeps having to narrow.
    import ast

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in {"print", "debug", "info", "warning", "error", "exception"}:
            continue
        for argument in ast.walk(node):
            assert not (isinstance(argument, ast.Name) and argument.id == "key"), (
                f"{name}() is handed the key itself")

    # And no tracked file carries a bare 32-hex credential.
    root = Path(__file__).resolve().parents[2]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    pattern = re.compile(r"\b[0-9a-f]{32}\b")
    for name in tracked:
        path = root / name
        if not path.is_file() or path.suffix in {".png", ".jpg", ".parquet"}:
            continue
        try:
            body = path.read_text(errors="ignore")
        except OSError:
            continue
        for hit in pattern.findall(body):
            # Migration ids and digests are hex too; a credential is one that
            # sits next to something naming it.
            window = body[max(0, body.index(hit) - 60):body.index(hit)]
            assert "api_key" not in window.lower() and "cmc_key" not in window.lower(), (
                f"{name} looks like it carries a credential")
