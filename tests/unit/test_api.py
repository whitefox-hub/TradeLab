"""The operator interface. Plan.md §20.

Most of these assert something the API does *not* do. That is the shape of §20:
the interface is defined more by its refusals than by its features, and a
refusal that is not tested is a refusal that will quietly stop holding.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

pytest.importorskip("fastapi")

from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from tradelab.api.app import create_app
from tradelab.api.security import (
    Sessions,
    hash_password,
    new_secret,
    verify_password,
)

SALT = "probe-salt"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """No database. Every test using this fixture asserts something structural —
    a route that does not exist, a credential that is not reachable, a figure
    that is not computed — and none of those needs a row to be true.

    Tests that do need rows are integration tests against real Postgres, below.
    Building a third copy of the schema in SQLite to keep them fast would give
    them something to drift from.
    """
    monkeypatch.setenv("TRADELAB_ADMIN_SALT", SALT)
    monkeypatch.setenv("TRADELAB_ADMIN_HASH", hash_password(PASSWORD, SALT))
    monkeypatch.setenv("TRADELAB_SESSION_SECRET", new_secret())
    return TestClient(create_app(engine=sa.create_engine("sqlite://")))


@pytest.fixture
def live_client(monkeypatch) -> TestClient:
    from tradelab.config import Settings

    monkeypatch.setenv("TRADELAB_ADMIN_SALT", SALT)
    monkeypatch.setenv("TRADELAB_ADMIN_HASH", hash_password(PASSWORD, SALT))
    monkeypatch.setenv("TRADELAB_SESSION_SECRET", new_secret())
    return TestClient(create_app(engine=sa.create_engine(Settings().database_url)))


@pytest.mark.fast
def test_the_password_hash_is_salted_and_verified_in_constant_time() -> None:
    digest = hash_password(PASSWORD, SALT)
    assert digest != hash_password(PASSWORD, "different-salt")
    assert verify_password(PASSWORD, SALT, digest)
    assert not verify_password("wrong", SALT, digest)


@pytest.mark.fast
def test_a_weak_session_secret_is_refused_rather_than_padded() -> None:
    """A shared or short secret is worse than no session, because it looks like
    security."""
    with pytest.raises(ValueError, match="at least 32"):
        Sessions("short")


@pytest.mark.fast
def test_a_session_survives_a_round_trip_and_a_tampered_one_does_not() -> None:
    sessions = Sessions(new_secret())
    token = sessions.issue("admin@example.com")
    assert sessions.read(token).email == "admin@example.com"
    assert sessions.read(token[:-3] + "xyz") is None
    assert sessions.read("not-a-token") is None


@pytest.mark.fast
def test_every_read_requires_a_session(client: TestClient) -> None:
    """One identity does not mean no identity: signing in is the deliberate act
    before a decision can be recorded (§20.3)."""
    for path in ("/api/me", "/api/strategies", "/api/runs", "/api/gates",
                 "/api/approvals", "/api/audit", "/api/universe"):
        response = client.get(path)
        assert response.status_code == 401, f"{path} answered without a session"


@pytest.mark.fast
def test_recording_an_approval_requires_a_session(client: TestClient) -> None:
    response = client.post("/api/approvals", json={
        "subject_kind": "strategy", "subject_id": str(uuid.uuid4()),
        "decision": "approve", "gate_config_id": str(uuid.uuid4()),
    })
    assert response.status_code == 401


@pytest.mark.fast
def test_no_route_mentions_mode_as_something_to_set(client: TestClient) -> None:
    paths = {route.path for route in client.app.routes}
    assert not any("mode" in path for path in paths)
    assert not any("promote" in path or "go-live" in path for path in paths)


@pytest.mark.fast
def test_the_approval_body_cannot_smuggle_a_mode_change() -> None:
    """Pydantic rejects unknown fields on this model rather than ignoring them,
    so a client that sends `mode` gets an error and not a silent no-op."""
    from tradelab.api.app import ApprovalRequest

    ok = ApprovalRequest(subject_kind="strategy", subject_id=uuid.uuid4(),
                         decision="approve", gate_config_id=uuid.uuid4())
    assert ok.decision == "approve"
    with pytest.raises(ValueError):
        ApprovalRequest(subject_kind="live", subject_id=uuid.uuid4(), decision="approve")
    with pytest.raises(ValueError):
        ApprovalRequest(subject_kind="strategy", subject_id=uuid.uuid4(), decision="yes")


@pytest.mark.fast
def test_health_states_the_two_things_this_surface_cannot_do(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["computes_metrics"] is False
    assert body["can_start_live_trading"] is False


@pytest.mark.fast
def test_the_process_holds_no_venue_credential() -> None:
    """§14.4: the api image does not carry LIVE keys, so the interface is
    incapable of starting live trading even if its code asked to."""
    import tradelab.api.app as module

    source = Path(module.__file__).read_text()
    for forbidden in ("api_secret", "BinanceVenue", "Credentials", "api_key"):
        assert forbidden not in source, f"{forbidden} reachable from the API process"


@pytest.mark.fast
def test_the_api_computes_no_figure() -> None:
    """§20.1: every number shown was computed by a worker and persisted. A
    figure that appears only on screen is one nobody can reproduce."""
    import ast

    import tradelab.api.app as module

    source = Path(module.__file__).read_text()

    # Checked on the imports rather than on the text. The first version searched
    # for names like `deflated_sharpe` anywhere in the file and started failing
    # the moment a query selected the stored column of that name — which is
    # precisely the behaviour §20.1 asks for: a figure computed by a worker and
    # read back. Reading one is the rule; calculating one is the violation.
    tree = ast.parse(source)
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    }
    for forbidden in ("numpy", "scipy", "polars", "tradelab.validation",
                      "tradelab.engines"):
        assert not any(name.startswith(forbidden) for name in imported), (
            f"the API imports {forbidden}, which computes"
        )


@pytest.mark.integration
def test_signing_in_with_the_wrong_password_says_nothing_useful(
    live_client: TestClient,
) -> None:
    """One message for an unknown user and for a bad password alike.
    Distinguishing them tells an attacker which half they got right."""
    response = live_client.post(
        "/api/session", json={"email": "nobody@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid credentials"


@pytest.mark.integration
def test_the_cooling_off_is_enforced_by_the_database(monkeypatch) -> None:
    """§20.3's separation is temporal, not social, and it lives in a trigger.

    Tested here rather than through the API on purpose: the API's own validation
    would be a second copy of the rule that can drift, and the guarantee that
    matters is that it cannot be clicked past — which is only true if the
    database is the one refusing.
    """
    import sqlalchemy as sa_

    from tradelab.config import Settings

    engine = sa_.create_engine(Settings().database_url)
    operator_id = uuid.uuid4()
    fresh_gate, aged_gate = uuid.uuid4(), uuid.uuid4()

    with engine.begin() as connection:
        connection.execute(sa_.text(
            "INSERT INTO users (id, email, role) VALUES (:i, :e, 'admin')"),
            {"i": operator_id, "e": f"probe-{operator_id.hex[:8]}@example.com"})
        for gate_id, valid_from in (
            (fresh_gate, "now()"), (aged_gate, "now() - interval '30 hours'")
        ):
            connection.execute(sa_.text(
                f"INSERT INTO gate_configs (id, transition, thresholds, rationale, "
                f"valid_from) VALUES (:i, 'validating->candidate', '{{}}', 'probe', "
                f"{valid_from})"), {"i": gate_id})

    def approve(gate_id: uuid.UUID) -> bool:
        try:
            with engine.begin() as connection:
                connection.execute(sa_.text(
                    "INSERT INTO approvals (id, subject_kind, subject_id, approved_by, "
                    "decision, gate_config_id) "
                    "VALUES (:i, 'strategy', :s, :u, 'approve', :g)"),
                    {"i": uuid.uuid4(), "s": uuid.uuid4(), "u": operator_id, "g": gate_id})
            return True
        except sa_.exc.DBAPIError as error:
            assert "24 hours" in str(error.orig)
            return False

    try:
        # Each in its own transaction: a shared one would leave the second
        # insert failing because the first aborted it, and the test would pass
        # for the wrong reason.
        assert not approve(fresh_gate), "a gate changed minutes ago was citable"
        assert approve(aged_gate), "a gate older than 24 hours was refused"
    finally:
        with engine.begin() as connection:
            connection.execute(sa_.text("DELETE FROM approvals WHERE approved_by = :i"),
                               {"i": operator_id})
            connection.execute(sa_.text("DELETE FROM gate_configs WHERE id IN (:a, :b)"),
                               {"a": fresh_gate, "b": aged_gate})
            connection.execute(sa_.text("DELETE FROM users WHERE id = :i"),
                               {"i": operator_id})


@pytest.mark.fast
def test_the_sidebar_only_links_screens_that_exist(client: TestClient) -> None:
    """A menu offering a screen that 404s is worse than not offering it.

    The sidebar is built from one list in app.js, so this checks the list rather
    than each page — which is the reason it is one list.
    """
    import re

    page = client.get("/static/app.js").text
    linked = set(re.findall(r'href: "(/[a-z_]+)"', page))
    served = {route.path for route in client.app.routes}
    assert linked, "the sidebar links nothing"
    for path in linked:
        assert path in served, f"{path} is in the menu but not served"


@pytest.mark.fast
def test_the_old_entry_point_still_lands_somewhere(client: TestClient) -> None:
    """/trade_lab moved to /home. A bookmark should not meet a 404."""
    response = client.get("/trade_lab", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/home"


@pytest.mark.fast
def test_the_login_page_is_served_without_a_session(client: TestClient) -> None:
    """Gating the sign-in page behind a session is the classic loop."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.fast
def test_the_login_redirect_cannot_leave_this_origin(client: TestClient) -> None:
    """`next` survives a deep link through sign-in, and an open redirect on a
    login page is how a session gets handed to somewhere else."""
    page = client.get("/login").text
    assert 'next.startsWith("/") && !next.startsWith("//")' in page, (
        "the next parameter is not constrained to a same-origin path"
    )


@pytest.mark.fast
def test_the_screens_send_an_unauthenticated_visitor_to_login(client: TestClient) -> None:
    """Not to the dashboard, which would 401 every card, and not to an empty
    table with no explanation of why it is empty."""
    shared = client.get("/static/app.js").text
    assert "/login?next=" in shared, "the shared mount does not point at sign-in"
    # The shared mount() sends anyone without a session to sign in, with the
    # path they wanted, rather than rendering a page whose every call will 401.
    assert 'location.replace("/login?next=" + encodeURIComponent(active))' in (
        client.get("/static/app.js").text
    )


@pytest.mark.fast
def test_the_dashboard_no_longer_carries_a_second_sign_in_form(client: TestClient) -> None:
    """One place to sign in. Two forms drift, and the one nobody uses is the one
    that keeps working against a stale endpoint."""
    page = client.get("/home").text
    assert 'type="password"' not in page


@pytest.mark.fast
def test_the_parameters_screen_is_served(client: TestClient) -> None:
    response = client.get("/parameters")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.fast
def test_a_setting_change_requires_a_stated_reason() -> None:
    """§20.3 demands one of gate thresholds for the same reason: a change nobody
    gave a reason for is one nobody can review six months later."""
    from tradelab.api.app import SettingRequest

    assert SettingRequest(value={"years": 3}, rationale="narrowing the window")
    with pytest.raises(ValueError):
        SettingRequest(value={"years": 3}, rationale="x")
    with pytest.raises(ValueError):
        SettingRequest(value={"years": 3})


@pytest.mark.integration
def test_a_locked_parameter_is_refused_by_name() -> None:
    """§12.4's right-hand column. Refused rather than ignored: a request to
    change a signal parameter is one the system should answer, because silently
    accepting it would let a validated strategy drift without a new id."""
    import sqlalchemy as sa_

    from tradelab.api.security import Sessions, new_secret
    from tradelab.config import Settings

    engine = sa_.create_engine(Settings().database_url)
    email = engine.connect().execute(
        sa_.text("SELECT email FROM users LIMIT 1")).scalar()
    if email is None:
        pytest.skip("no operator row")

    secret = new_secret()
    with pytest.MonkeyPatch().context() as patch:
        patch.setenv("TRADELAB_SESSION_SECRET", secret)
        client = TestClient(create_app(engine=engine))
        client.cookies.set("tradelab_session", Sessions(secret).issue(email))

        refused = client.post("/api/settings/rsi_period", json={
            "value": {"period": 14}, "rationale": "trying another period"})
        assert refused.status_code == 403
        assert "12.4" in refused.json()["detail"]

        allowed = client.get("/api/settings").json()
        keys = {entry["key"] for entry in allowed["editable"]}
        assert "research_universe" in keys
        assert "rsi_period" not in keys
        # And the locked half is shown, not hidden. Knowing why you cannot
        # change something is the useful part.
        assert any("asset universe" in item["name"].lower()
                   for item in allowed["locked"])


@pytest.mark.fast
def test_the_content_area_is_not_capped(client: TestClient) -> None:
    """A table that stops short of the window wastes the column the headline
    needs. Prose keeps its own limit through `.hint`, which is where a reading
    width belongs — on the text, not on the layout."""
    css = client.get("/static/app.css").text
    assert ".content { min-width:0; padding:28px 28px 64px; width:100%; }" in css
    assert "max-width:1180px" not in css
    assert ".hint { font-size:14px; color:var(--muted); margin:10px 0 0; max-width:74ch;" in css


@pytest.mark.fast
def test_the_algorithm_screen_is_served_and_in_the_menu(client: TestClient) -> None:
    assert client.get("/algorithm").status_code == 200
    assert 'href: "/algorithm"' in client.get("/static/app.js").text


@pytest.mark.fast
def test_the_old_pipeline_path_redirects(client: TestClient) -> None:
    """It showed project status; the question it was asked was about the
    decision sequence. A bookmark should not meet a 404."""
    response = client.get("/pipeline", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/algorithm"


def _algorithm_source() -> str:
    """The ALGORITHM literal alone.

    Ended at the list's own closing bracket rather than at whatever route
    follows it. The first version sliced up to `@app.get("/api/algorithm")` and
    broke the moment another endpoint was written above that one — counting
    `"name":` out of a SQL row as though it were a step.
    """
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    start = source.index("ALGORITHM = [")
    end = source.index("\n    ]\n", start) + len("\n    ]\n")
    return source[start:end]


@pytest.mark.fast
def test_every_step_carries_the_rule_that_must_hold() -> None:
    """A sequence without its invariants is a list of function names. What makes
    it the algorithm is what has to be true when each step runs."""
    block = _algorithm_source()
    assert block.count('"rule":') == block.count('"name":')
    assert block.count('"does":') == block.count('"name":')


@pytest.mark.fast
def test_the_steps_are_numbered_in_the_order_they_run() -> None:
    """The screen's whole claim is the ordering, so a gap or a repeat in the
    numbering is the one defect it cannot be allowed to have."""
    import re

    block = _algorithm_source()
    numbers = [int(n) for n in re.findall(r'"n": (\d+)', block)]
    assert numbers == list(range(1, len(numbers) + 1))


@pytest.mark.fast
def test_the_steps_that_were_defects_say_what_they_cost() -> None:
    """A rule with no story behind it reads as a preference, and the next person
    reorders it. These four were real defects in this codebase."""
    block = _algorithm_source()
    assert block.count('"cost":') >= 4
    # And the numbers in those stories are the measured ones, not round.
    assert "2.68" in block and "0.03" in block
    assert "1.000 to 0.024" in block


@pytest.mark.fast
def test_the_algorithm_screen_computes_nothing() -> None:
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    block = source[source.index('def algorithm('):source.index('def algorithm_screen(')]
    assert "SELECT count(*)" in block or "sum(executed_trials)" in block
    assert "session.rollback()" in block, "a missing table would take the view down"


@pytest.mark.fast
def test_the_chrome_is_revalidated_rather_than_cached(client: TestClient) -> None:
    """The sidebar lives in one file, so a cached app.js means a screen that
    exists is missing from the navigation — which reads as the screen not having
    been built rather than as a stale asset."""
    response = client.get("/static/app.js")
    assert "no-cache" in response.headers.get("cache-control", "")
    # HTML too: the pages carry the script tag that would otherwise be stale.
    assert client.get("/algorithm").status_code == 200


# -- trends -------------------------------------------------------------------
#
# A market trend is three fields: asset, direction, magnitude. The tests below are
# mostly about what it is *not* allowed to become, because the pressure on a
# screen like this is always to add one more field, and the fourth field is the
# one nothing downstream reads.


# -- what this surface is allowed to do --------------------------------------


@pytest.mark.fast
def test_there_is_no_route_that_changes_a_run_mode(client: TestClient) -> None:
    """§20.2, the most important thing in that section. Not a button, not a
    dropdown, not a config field. Asserted against the route table so that
    adding one later fails here rather than in production."""
    app = client.app
    writable = [
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    # The complete set of writes this surface allows, asserted only here: approvals
    # and lifecycle transitions (§20.1), operator settings (§12.4), the scenario (§20).
    # Written as equality so a new route fails here.
    assert sorted(writable) == [
        ("/api/approvals", "POST"),
        # §12.4 puts sizing and risk limits in the operator's hands, and §20
        # gives this screen "limit changes (admin)". It creates a configuration
        # and never edits one: §14.1 versions them and has runs reference one,
        # so a limit cannot change beneath a position opened under it. There is
        # no PUT or PATCH here for that reason.
        ("/api/risk-limits", "POST"),
        ("/api/scenario", "PUT"),
        ("/api/session", "DELETE"),
        ("/api/session", "POST"),
        ("/api/settings/{key}", "POST"),
    ], f"a new write route appeared: {writable}"


def _screens(client: TestClient) -> set[str]:
    """Every route whose handler returns a page.

    Found by return annotation rather than by a hand-written list: a list needs
    editing whenever a screen is added, and the edit that gets forgotten is the
    one that lets an unchecked page through.
    """
    served = set()
    for route in client.app.routes:
        annotation = getattr(getattr(route, "endpoint", None), "__annotations__", {})
        # The string, not the class: app.py uses `from __future__ import
        # annotations`, so every annotation there is already a string.
        if annotation.get("return") in (FileResponse, "FileResponse"):
            served.add(route.path)
    assert served, "no screens found; the check is not looking at anything"
    return served


@pytest.mark.fast
def test_every_screen_calls_only_endpoints_that_exist(client: TestClient) -> None:
    """A screen referring to a route nobody implemented fails in the browser,
    where nothing watches. Checked here instead.

    Over every screen rather than one named page. The previous version checked
    /news alone, so when /news was deleted the check went with it and the other
    eight screens were never covered at all.
    """
    import re

    for path in _screens(client):
        page = client.get(path).text
        called = set(re.findall(r'["`](/api/[a-z/-]+)', page))
        known = {route.path for route in client.app.routes}
        for endpoint in called:
            assert any(endpoint == p or p.startswith(endpoint) for p in known), (
                f"{path} calls {endpoint}, which has no route")


@pytest.mark.fast
def test_every_screen_mounts_the_shared_sidebar(client: TestClient) -> None:
    """Three copies of the chrome drift, and the copy nobody looks at is the one
    still styling a class that was renamed.

    Over every screen, for the same reason as above: the hand-written list this
    replaced named three pages out of nine.
    """
    for path in _screens(client) - {"/login"}:
        page = client.get(path).text
        assert '<link rel="stylesheet" href="/static/app.css">' in page, path
        assert '<script src="/static/app.js"></script>' in page, path
        assert f'mount("{path}"' in page, f"{path} does not mark itself active"


# -- the market trend ----------------------------------------------------------------


@pytest.mark.fast
def test_reading_or_setting_the_scenario_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/scenario").status_code == 401
    assert client.put("/api/scenario", json={
        "trend": 2, "sentiment": 20}).status_code == 401
    assert client.get("/api/fear-greed").status_code == 401


@pytest.mark.fast
def test_there_is_no_route_that_creates_a_second_scenario(client: TestClient) -> None:
    """One set of conditions. A POST or a DELETE would imply otherwise, and an
    API that implies a second scenario is one a client will eventually try to
    make."""
    paths = {route.path for route in client.app.routes}
    assert "/api/scenario" in paths
    assert not any(p.startswith("/api/trends") for p in paths)

    methods = {
        method
        for route in client.app.routes
        if getattr(route, "path", None) == "/api/scenario"
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET", "PUT"}, f"the scenario answers to {methods}"


@pytest.mark.fast
def test_the_assumed_sentiment_is_not_the_measured_one(client: TestClient) -> None:
    """The index printed today is a measurement: dated, published once, never
    revised. The scenario's sentiment is an assumption with no date. Separate
    fields, separate tables, and separate routes — holding both in one number
    would make an opinion indistinguishable from a measurement, which is the
    failure the hand-entered event corpus was kept apart from the collected one
    to avoid.
    """
    from tradelab.registry.models import MarketConditions, SentimentReading

    assert MarketConditions.__tablename__ != SentimentReading.__tablename__
    # The assumption carries no date. Every column on it, checked rather than
    # asserted in prose, because the way a date arrives is one migration at a
    # time.
    dated = [c.name for c in MarketConditions.__table__.columns
             if str(c.type).upper().startswith(("DATE", "TIMESTAMP"))]
    assert not dated, f"the scenario grew a date column: {dated}"

    paths = {route.path for route in client.app.routes}
    assert "/api/fear-greed" in paths
    # Named for the index, not for "sentiment", so the two cannot be confused at
    # the call site either.
    assert "/api/sentiment" not in paths


@pytest.mark.fast
def test_a_sentiment_outside_one_to_a_hundred_is_refused() -> None:
    """The scale is 1 to 100. A number outside it would be read downstream as a
    position on a scale that does not exist, and nothing further along could
    tell it from a real one."""
    import pydantic

    from tradelab.api.app import ScenarioRequest

    for bad in (0, -1, 101, 1000):
        with pytest.raises(pydantic.ValidationError):
            ScenarioRequest(trend=5, sentiment=bad)
    for good in (1, 50, 100):
        assert ScenarioRequest(trend=5, sentiment=good).sentiment == good


@pytest.mark.fast
def test_a_magnitude_outside_the_scale_is_refused() -> None:
    """The scale is defined as 1 to 10. A 70 is not a stronger market trend, it is a
    number the engine would read as seven times the defined extreme — and
    nothing downstream could tell it from a real one."""
    import pydantic

    from tradelab.api.app import ScenarioRequest

    for bad in (-1, 11, 70, 1000):
        with pytest.raises(pydantic.ValidationError):
            ScenarioRequest(direction="UP", magnitude=bad, sentiment=50)


@pytest.mark.fast
def test_the_scenario_carries_no_field_beyond_what_it_needs() -> None:
    """No asset, because there is one set of conditions and they are the
    market's; no date, because none of this happened.

    `source` is the fourth, and it earns its place: it says who decided, which
    PREREG-005 filters on. Without it a value computed from a trade count and a
    value a person read in the news are the same row, and a study measuring the
    person would count both.

    `extra="forbid"` matters as much as the field list. Without it a client
    sending `asset` gets a 200 and the value is dropped, which is how an
    operator comes to believe a setting took effect.
    """
    import pydantic

    from tradelab.api.app import ScenarioRequest

    assert set(ScenarioRequest.model_fields) == {
        "trend", "sentiment", "source"}
    for smuggled in ("asset", "happened_at", "duration_hours", "note", "mode"):
        with pytest.raises(pydantic.ValidationError):
            ScenarioRequest(trend=6, sentiment=50, **{smuggled: 1})


# -- the index ---------------------------------------------------------------


@pytest.mark.fast
def test_sentiment_is_kept_out_of_the_point_in_time_corpus() -> None:
    """§11.4 argues against a bought index and is right about why: the method is
    not auditable and it is composed partly of data already collected here. It
    is stored so it can be looked at, in its own table — `macro_series` is what a
    study reaches for, and this deliberately does not join it."""
    from tradelab.registry.models import MacroSeries, SentimentReading

    assert SentimentReading.__tablename__ == "sentiment_readings"
    assert SentimentReading.__tablename__ != MacroSeries.__tablename__
    # Every foreign key on the sentiment table, of which there are none: a join
    # into the corpus is how a vendor number ends up inside a backtest.
    assert not list(SentimentReading.__table__.foreign_keys)


@pytest.mark.fast
def test_a_reading_is_available_on_its_own_day_not_when_it_was_downloaded() -> None:
    """The index publishes one value per UTC day at that day's midnight. Stamping
    a backfilled 2018 reading with the moment it was downloaded would make eight
    years of history look as though it arrived today, and every study filtering
    on availability would then see none of it."""
    from datetime import UTC, datetime

    from tradelab.collectors.sentiment import Reading

    day = datetime(2018, 2, 1, tzinfo=UTC)
    assert Reading(day=day, value=30, classification="Fear").available_at == day


@pytest.mark.fast
def test_an_unknown_classification_or_value_is_refused_at_the_boundary() -> None:
    """Held in the collector as well as in a database CHECK, so a sixth band
    appearing upstream fails while the payload is still there to look at rather
    than as a constraint violation somewhere later."""
    import httpx

    from tradelab.collectors.sentiment import CLASSIFICATIONS, fetch

    assert len(CLASSIFICATIONS) == 5

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"value": "50", "value_classification": "Cautiously Optimistic",
             "timestamp": "1517443200"}], "metadata": {"error": None}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http, \
            pytest.raises(ValueError, match="unknown classification"):
        fetch(client=http)

    def out_of_scale(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"value": "140", "value_classification": "Greed",
             "timestamp": "1517443200"}], "metadata": {"error": None}})

    with httpx.Client(transport=httpx.MockTransport(out_of_scale)) as http, \
            pytest.raises(ValueError, match="0-100"):
        fetch(client=http)


# -- what was removed --------------------------------------------------------


@pytest.mark.fast
def test_the_news_screen_and_its_logic_are_gone(client: TestClient) -> None:
    """Removed rather than hidden. A page left served but unlinked is a page
    that still answers, and an event write with no caller is a write that can
    still be made."""
    import importlib

    paths = {route.path for route in client.app.routes}
    assert "/news" not in paths
    assert not any(p.startswith("/api/event") for p in paths), (
        f"event routes survive: {sorted(p for p in paths if p.startswith('/api/event'))}"
    )
    assert client.get("/news").status_code == 404

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("tradelab.news")

    # The measurement and the corpus did not go with the screen. §11.3's
    # abnormal-return estimator is read by studies, not by a page, and deleting
    # it with the markup would have taken a result-bearing module out with a
    # view.
    assert importlib.import_module("tradelab.events.reaction")


@pytest.mark.fast
def test_every_built_screen_appears_in_the_menu(client: TestClient) -> None:
    """A route that serves a page and is not linked is a page nobody finds."""
    import re

    menu = client.get("/static/app.js").text
    linked = set(re.findall(r'href: "(/[a-z_-]+)"', menu))

    # /login is deliberately absent from the menu: it is where an unauthenticated
    # visitor is sent, not somewhere to navigate to.
    assert _screens(client) - linked == {"/login"}, (
        f"served but not in the menu: {_screens(client) - linked - {'/login'}}"
    )


@pytest.mark.fast
def test_the_menu_offers_no_screen_that_is_gone(client: TestClient) -> None:
    """The other direction, which is how /news would have lingered: a menu entry
    pointing at a deleted page 404s in the browser, where nothing watches."""
    import re

    menu = client.get("/static/app.js").text
    assert "/news" not in menu
    assert "/trends" not in menu
    for href in set(re.findall(r'href: "(/[a-z_-]+)"', menu)):
        assert client.get(href).status_code == 200, f"{href} is in the menu but 404s"


@pytest.mark.fast
def test_the_scenario_screen_flags_unsaved_changes(client: TestClient) -> None:
    """The Save button sits with the title, away from the sliders it governs, so
    a change made at the bottom of the page can be forgotten on the way up.

    Without the flag the move trades a tidier header for a setting the operator
    believes took effect — which is the same failure as a silently dropped field,
    arriving through the layout instead.
    """
    markup = client.get("/static/market_sentiment.html").text
    assert "Unsaved changes" in markup
    assert "function paintDirty" in markup
    # The baseline is what the server confirmed, never what the form holds. Read
    # back off the controls it would call a failed save clean.
    assert "stored = {trend: saved.trend" in markup


# -- nothing is written twice ------------------------------------------------


@pytest.mark.fast
def test_each_pane_shows_the_history_of_its_own_dial(client: TestClient) -> None:
    """A change to the sentiment listed under the market trend would read as the market trend
    having been set to the value it already held, which is the repetition the
    write path exists to avoid."""
    markup = client.get("/static/market_sentiment.html").text
    assert 'id="t-history"' in markup
    assert 'id="s-history"' in markup
    assert "/api/scenario/history" in markup
    # The previous value is derived from the adjacent entry only where it was
    # not recorded, so a real one is never overwritten by a guess.
    assert "e.from ?? (raw[i + 1]" in markup


@pytest.mark.integration
def test_saving_the_same_scenario_twice_records_it_once(monkeypatch) -> None:
    """A log that records non-changes cannot answer when something was last
    changed, which is the only question it exists for.

    The no-op test lives in the UPDATE's WHERE rather than in a read-then-
    compare, because between those two statements another request can change the
    value and the comparison would be against something no longer there. Tested
    through the API, since that is the path an operator takes.
    """
    import sqlalchemy as sa_

    from tradelab.config import Settings

    monkeypatch.setenv("TRADELAB_ADMIN_SALT", SALT)
    monkeypatch.setenv("TRADELAB_ADMIN_HASH", hash_password(PASSWORD, SALT))
    monkeypatch.setenv("TRADELAB_SESSION_SECRET", new_secret())
    engine = sa_.create_engine(Settings().database_url)
    client = TestClient(create_app(engine=engine))

    email = engine.connect().execute(
        sa_.text("SELECT email FROM users LIMIT 1")).scalar()
    if email is None:
        pytest.skip("no operator row")
    assert client.post(
        "/api/session", json={"email": email, "password": PASSWORD}).status_code == 200

    def entries() -> int:
        with engine.connect() as conn:
            return conn.execute(sa_.text(
                "SELECT count(*) FROM audit_log WHERE action = 'scenario.set'"
            )).scalar_one()

    before = entries()
    body = {"direction": "DOWN", "magnitude": 4, "sentiment": 33}

    first = client.put("/api/scenario", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["changed"] is True
    assert entries() == before + 1

    for _ in range(3):
        repeat = client.put("/api/scenario", json=body)
        assert repeat.status_code == 200, repeat.text
        # Idempotent and honest about it, rather than returning 200 and writing.
        assert repeat.json()["changed"] is False
        # The state is still reported, so a client that missed the first reply
        # does not have to guess what the value is now.
        assert repeat.json()["magnitude"] == 4
    assert entries() == before + 1, "a repeated save added a log entry"

    moved = client.put("/api/scenario", json={**body, "sentiment": 34})
    assert moved.json()["changed"] is True
    assert entries() == before + 2

    # The entry says what it replaced, so a reader can tell which dial moved
    # without holding the whole log in their head.
    with engine.connect() as conn:
        payload = conn.execute(sa_.text(
            "SELECT payload FROM audit_log WHERE action = 'scenario.set' "
            "ORDER BY id DESC LIMIT 1")).scalar_one()
    assert payload["from"] == {"direction": "DOWN", "magnitude": 4, "sentiment": 33}

    history = client.get("/api/scenario/history?limit=5").json()
    assert history[0]["sentiment"] == 34
    assert history[0]["from"]["sentiment"] == 33


@pytest.mark.integration
def test_an_unchanged_reading_is_not_rewritten(monkeypatch) -> None:
    """The daily run fetches an overlap on purpose. Without the distinctness
    test almost every write would be a no-op recorded as a write, and
    `ingest_time` would mean "when the cron last ran" rather than "when this
    value last changed" — and only the second is a question the column can
    answer."""
    from datetime import UTC, datetime

    import sqlalchemy as sa_

    from tradelab.collectors.sentiment import Reading, store
    from tradelab.config import Settings

    engine = sa_.create_engine(Settings().database_url)
    day = datetime(1990, 1, 1, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(sa_.text("DELETE FROM sentiment_readings WHERE day = :d"),
                     {"d": day.date()})

    reading = Reading(day=day, value=42, classification="Fear")
    assert store(engine, [reading]) == 1
    with engine.connect() as conn:
        first_ingest = conn.execute(sa_.text(
            "SELECT ingest_time FROM sentiment_readings WHERE day = :d"),
            {"d": day.date()}).scalar_one()

    assert store(engine, [reading]) == 0, "an identical reading was rewritten"
    with engine.connect() as conn:
        assert conn.execute(sa_.text(
            "SELECT ingest_time FROM sentiment_readings WHERE day = :d"),
            {"d": day.date()}).scalar_one() == first_ingest

    # A value that genuinely differs is a revision, and is written rather than
    # swallowed: §4.3 says a revised series with no vintages cannot be read as
    # it was known at the time, and hiding the revision would make that
    # undetectable.
    assert store(engine, [Reading(day=day, value=43, classification="Fear")]) == 1

    with engine.begin() as conn:
        conn.execute(sa_.text("DELETE FROM sentiment_readings WHERE day = :d"),
                     {"d": day.date()})


@pytest.mark.fast
def test_the_bars_show_what_is_applied_and_save_is_what_moves_them(
    client: TestClient,
) -> None:
    """The sliders show what is being considered; the coloured bars show what is
    in force. Between a drag and a save the two disagree on purpose, and that
    disagreement is the whole reason there is a button.

    Pinned because it is easy to undo by accident: wiring a pin to the slider
    is the obvious thing to write, and it would make the screen say a value was
    applied the moment it was dragged.
    """
    markup = client.get("/static/market_sentiment.html").text

    assert "function paintApplied" in markup
    # The pins are placed from `stored` — what the server confirmed — and from
    # nothing else.
    assert '$("s-pin").style.left = `${stored.sentiment}%`' in markup
    assert '$("t-pin").style.left = `${trackPct(stored.trend)}%`' in markup

    # And painted again right after a save, which is what makes the bar catch up.
    body = markup[markup.index('$("save").onclick'):]
    assert "paintApplied();" in body, "a save does not move the bars"


@pytest.mark.fast
def test_the_index_and_the_assumption_share_one_bar_but_not_one_mark(
    client: TestClient,
) -> None:
    """The separate 'Test under' scale is gone: two gradients for one concept
    read as two concepts. One bar now, with two marks — the thin one is what the
    index printed, the solid one is what a simulation is told to assume.

    They stay distinguishable because that is the distinction the whole screen
    rests on. A single mark would put a measurement and an opinion in the same
    place, which is what separate tables and separate routes exist to prevent.
    """
    markup = client.get("/static/market_sentiment.html").text

    # One gradient in the Fear and Greed pane, carrying both marks.
    pane = markup[markup.index("<h2>Fear and Greed</h2>"):markup.index('id="s-history"')]
    assert pane.count('class="scale"') == 1, "the pane has more than one gradient"
    assert 'class="mark" id="fg-pin"' in pane, "the measured mark is missing"
    assert 'class="pin" id="s-pin"' in pane, "the applied pin is missing"
    # Named on screen, not left to be inferred from two similar lines.
    assert ">index<" in pane and ">applied<" in pane

    # The slider sits above the bar it reads against, not below it.
    assert pane.index('id="s-range"') < pane.index('class="scale"')


@pytest.mark.fast
def test_saving_the_scenario_is_what_starts_a_run(client: TestClient) -> None:
    """The Save button activates the algorithm, so the run hangs off the write
    rather than off a second route the screen has to remember to call. There is
    still no new write route: a run is recorded by the save that caused it.
    """
    import inspect

    from tradelab.api.app import create_app as factory

    source = inspect.getsource(factory)
    put = source[source.index('@app.put("/api/scenario")'):]
    put = put[:put.index('@app.get("/api/scenario/runs")')]
    assert "scenario_run.execute" in put, "saving does not start a run"
    # And only on a change, for the same reason the log records only one:
    # re-running an identical scenario produces an identical answer.
    assert put.index('"changed": False') < put.index("scenario_run.execute")

    paths = {route.path for route in client.app.routes}
    assert "/api/scenario/runs" in paths
    # Reading runs is a read. Nothing about a scenario run is a second write.
    methods = {
        method
        for route in client.app.routes
        if getattr(route, "path", None) == "/api/scenario/runs"
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}


@pytest.mark.fast
def test_the_algorithm_screen_no_longer_credits_news_for_the_sizing(
    client: TestClient,
) -> None:
    """Step 10 named a screen that has been deleted. A sequence describing a
    step by a mechanism that no longer exists is worse than one that omits it,
    because it reads as current."""
    # Read from the constant rather than through the endpoint: the endpoint
    # needs a session, and what is being checked is the text itself.
    import inspect

    from tradelab.api.app import create_app as factory

    source = inspect.getsource(factory)
    assert "News conditions the size" not in source
    assert "The scenario conditions the size" in source
    # And it says what the tilt cannot do, since that is the part an operator
    # has to be able to read without opening the code.
    assert "it does not signal" in source


@pytest.mark.fast
def test_the_algorithm_screen_reports_a_run_rather_than_describing_a_path(
    client: TestClient,
) -> None:
    """The sequence used to be a static list. Saving the scenario now runs it,
    so the screen marks what each step actually did — and the marking is
    derived, never asserted in the page.

    A screen that decides for itself which steps ran keeps saying so after they
    stop.
    """
    markup = client.get("/static/algorithm.html").text

    assert "function verdictFor" in markup
    # Which steps a scenario run executes comes from the runner, so the two
    # cannot disagree about what ran.
    assert "data.scenario_steps" in markup
    assert "steps.substituted.includes(n)" in markup

    # Execution steps are "never run", not "not reached": the runs table is
    # empty and that is measured, not inferred from the last scenario run.
    assert "data.observed.runs === 0" in markup
    assert "never run" in markup

    # And no step's status is hardcoded by number.
    import re

    assert not re.search(r'step\.n\s*(===|==)\s*\d', markup), (
        "a step's status is decided by its number rather than by what happened")


@pytest.mark.fast
def test_the_runner_declares_which_steps_it_skips(client: TestClient) -> None:
    """A scenario run does not redo the search — it takes a declared reference,
    which is exactly why it cannot report a strategy result. Marking steps 3 to
    5 as having run would make the screen claim one."""
    from tradelab.scenario.run import EXECUTED_STEPS, GATE_STEP, SUBSTITUTED_STEPS

    assert SUBSTITUTED_STEPS == (3, 4, 5)
    assert GATE_STEP in EXECUTED_STEPS
    assert not set(EXECUTED_STEPS) & set(SUBSTITUTED_STEPS), (
        "a step is both executed and substituted")
    # Every research step is accounted for, so none is silently unclassified.
    assert set(EXECUTED_STEPS) | set(SUBSTITUTED_STEPS) == set(range(1, GATE_STEP + 1))


@pytest.mark.integration
def test_the_book_shows_the_latest_snapshot_of_each_open_position(monkeypatch) -> None:
    """`positions` is a snapshot series keyed on (run, asset, as_of), and three
    things about reading it are easy to get wrong in ways that look fine.

    Taking every row counts a position once per snapshot ever written. Taking
    the newest row overall reports one asset as the whole book. And a flat
    position is not an open one — but its realised P&L does not stop counting
    when the position closed.
    """
    import uuid as uuid_

    import sqlalchemy as sa_

    from tradelab.config import Settings

    monkeypatch.setenv("TRADELAB_ADMIN_SALT", SALT)
    monkeypatch.setenv("TRADELAB_ADMIN_HASH", hash_password(PASSWORD, SALT))
    monkeypatch.setenv("TRADELAB_SESSION_SECRET", new_secret())
    engine = sa_.create_engine(Settings().database_url)
    client = TestClient(create_app(engine=engine))

    email = engine.connect().execute(
        sa_.text("SELECT email FROM users LIMIT 1")).scalar()
    strategy = engine.connect().execute(
        sa_.text("SELECT id FROM strategies LIMIT 1")).scalar()
    cost = engine.connect().execute(
        sa_.text("SELECT id FROM cost_models LIMIT 1")).scalar()
    venue = engine.connect().execute(
        sa_.text("SELECT code FROM venues LIMIT 1")).scalar()
    if None in (email, strategy, cost, venue):
        pytest.skip("no seed rows to hang a probe run from")
    assert client.post(
        "/api/session", json={"email": email, "password": PASSWORD}).status_code == 200

    before = client.get("/api/positions").json()
    run_id, limit_id = uuid_.uuid4(), uuid_.uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(sa_.text(
                "INSERT INTO risk_limit_configs VALUES (:i,'ZZPROBE',5,2,10,10,"
                "10000,0.001,0.1,60,2.0,1.0,3.0,0.05,0.2,10,3,50,now())"),
                {"i": limit_id})
            conn.execute(sa_.text(
                "INSERT INTO runs (id, strategy_id, mode, venue, params, "
                "  initial_capital, base_currency, risk_limit_config_id, "
                "  cost_model_id, code_version, image_digest, config_hash, "
                "  status, started_at) "
                "VALUES (:i,:s,'simulator',:v,CAST('{}' AS jsonb),10000,'USDT',"
                "  :l,:c,'zzprobe','zzprobe','zzprobe','running',now())"),
                {"i": run_id, "s": strategy, "v": venue, "l": limit_id, "c": cost})
            for asset, quantity, realised in (
                ("ZZLONG", 2, 0), ("ZZSHORT", -3, 10), ("ZZFLAT", 0, 40),
            ):
                for offset in ("2 hours", "0 hours"):
                    conn.execute(sa_.text(
                        "INSERT INTO positions (run_id, asset, as_of, qty, "
                        "  avg_entry, unrealised_pnl, realised_pnl, funding_paid) "
                        f"VALUES (:r,:a,now() - interval '{offset}',:q,100,5,:p,1)"),
                        {"r": run_id, "a": asset, "q": quantity, "p": realised})

        book = client.get("/api/positions").json()
        mine = {p["asset"]: p for p in book["positions"]
                if p["asset"].startswith("ZZ")}

        # One row per open position, not one per snapshot.
        assert set(mine) == {"ZZLONG", "ZZSHORT"}, (
            "a flat position is open, or a snapshot was counted twice")
        assert mine["ZZLONG"]["side"] == "LONG"
        assert mine["ZZSHORT"]["side"] == "SHORT"
        # Mode travels with the position. §20.2: shown, never editable.
        assert mine["ZZLONG"]["mode"] == "simulator"

        # The closed one still contributes its realised P&L: 0 + 10 + 40.
        assert book["realised_pnl"] - before["realised_pnl"] == pytest.approx(50)
        # And the two live ones contribute unrealised once each, not twice.
        assert book["unrealised_pnl"] - before["unrealised_pnl"] == pytest.approx(15)
    finally:
        with engine.begin() as conn:
            conn.execute(sa_.text("DELETE FROM positions WHERE run_id = :r"),
                         {"r": run_id})
            conn.execute(sa_.text("DELETE FROM runs WHERE id = :r"), {"r": run_id})
            conn.execute(sa_.text("DELETE FROM risk_limit_configs WHERE id = :i"),
                         {"i": limit_id})


@pytest.mark.fast
def test_the_book_says_why_it_is_empty(client: TestClient) -> None:
    """A blank table reads as a quiet day. Forward books now run in the
    simulator (PREREG-008, 009), so an empty table may be a flat book rather
    than an empty registry, and either way nothing has been promoted. The empty
    state says both and points at the sequence."""
    markup = client.get("/static/index.html").text
    assert "empty-why" in markup
    assert "Nothing has been promoted through §15" in markup
    assert "fake capital" in markup
    assert "/algorithm" in markup


@pytest.mark.fast
def test_the_home_screen_computes_no_figure(client: TestClient) -> None:
    """§20.1 puts the boundary at this surface deriving a number. Profit and
    loss is stored by whatever folded it from fills; a figure this page derived
    would disagree with the engine's the moment either changed."""
    markup = client.get("/static/index.html").text
    for derived in ("* price", "mark_price", "qty *", "- avg_entry", "avg_entry -"):
        assert derived not in markup, f"the home screen computes: {derived}"
    # Negated for display only, which is presentation and not derivation:
    # funding paid is a cost, and showing it as a positive gain would invert it.
    assert "money(-book.funding_paid" in markup


@pytest.mark.fast
def test_the_risk_screen_lists_every_limit_the_engine_has(client: TestClient) -> None:
    """The list is derived from the engine, not written into the page.

    A screen with its own copy of the limits keeps showing one after it is
    removed and misses the one that was added — and this is the screen where
    that matters most, because a limit nobody can see is one nobody checks.
    """
    import dataclasses

    from tradelab.risk.limits import STOPS, RiskLimits

    fields = {f.name for f in dataclasses.fields(RiskLimits)}

    # Every limit is explained, beside the limit rather than on the page. A new
    # one without a line here fails instead of shipping undocumented.
    assert set(STOPS) == fields, (
        f"undescribed: {sorted(fields - set(STOPS))}; "
        f"described but gone: {sorted(set(STOPS) - fields)}")

    markup = client.get("/static/risk_limits.html").text
    # None of the sixteen names is hardcoded in the page.
    for name in fields:
        assert name not in markup, f"{name} is written into the screen"
    assert "data.limits.map" in markup


@pytest.mark.fast
def test_halting_breaches_are_marked_as_such(client: TestClient) -> None:
    """A limit that halts the run is a different thing from one that refuses an
    order, and §14.1 adds a third: a drawdown halt a person must clear, because
    one that resumes by itself has not been reviewed and the review is the
    control.

    Taken from the engine's own sets so the screen cannot disagree with what
    actually happens.
    """
    from tradelab.risk.limits import HALTING, REQUIRES_HUMAN

    assert {b.value for b in REQUIRES_HUMAN} <= {b.value for b in HALTING}, (
        "a breach needs a person to clear it but does not halt")
    assert {b.value for b in HALTING} == {
        "max_daily_loss", "max_consecutive_errors", "max_drawdown_from_peak"}

    markup = client.get("/static/risk_limits.html").text
    assert "halts the run" in markup and "needs a person" in markup
    assert "l.halts" in markup, "the screen decides halting for itself"


@pytest.mark.fast
def test_a_risk_configuration_is_created_and_never_edited(client: TestClient) -> None:
    """§12.4 lists sizing and risk limits as tunable, and §20 gives this screen
    "limit changes (admin)" — so it writes. What it must not do is *edit*.

    §14.1 versions configurations and has each run reference one precisely so a
    limit cannot change beneath a position opened under it. An UPDATE would undo
    that: every past run would silently report limits it never ran under.
    """
    methods = {
        method
        for route in client.app.routes
        if getattr(route, "path", None) == "/api/risk-limits"
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET", "POST"}, f"the risk config answers to {methods}"

    import inspect

    from tradelab.api.app import create_app as factory

    source = inspect.getsource(factory)
    route = source[source.index('@app.post("/api/risk-limits"'):]
    route = route[:route.index('@app.get("/api/positions")')]
    assert "INSERT INTO risk_limit_configs" in route
    assert "UPDATE risk_limit_configs" not in route


@pytest.mark.fast
def test_every_limit_is_bounded_before_it_can_be_set() -> None:
    """A risk limit is what stops a bad decision, so one set loosely enough has
    been removed while still appearing on the screen. `max_daily_loss` at 1.0 is
    "halt after losing everything", which reads like a configured control and is
    not one.

    A field with no bound cannot be set at all, which is the safe direction: a
    limit nobody bounded is one anybody can disable.
    """
    import dataclasses

    from tradelab.risk.bounds import BOUNDS, Invalid, validate
    from tradelab.risk.limits import RiskLimits

    defaults = RiskLimits()
    fields = {f.name for f in dataclasses.fields(RiskLimits)}
    assert set(BOUNDS) == fields, (
        f"unbounded: {sorted(fields - set(BOUNDS))}")

    good = {name: getattr(defaults, name) for name in fields}
    assert validate(good), "the engine's own defaults are refused"

    # Values that disable the control rather than merely loosening it.
    for field, value in (
        ("max_daily_loss", 1.0),
        ("max_drawdown_from_peak", 0.99),
        ("max_leverage", 100),
        ("max_orders_per_minute", 100_000),
        ("max_position_pct_equity", 5),
    ):
        with pytest.raises(Invalid):
            validate({**good, field: value})

    # Individually fine, jointly meaningless: a minimum above its maximum
    # refuses every order, which is a halt reporting itself as a sizing rule.
    with pytest.raises(Invalid, match="refuse every order"):
        validate({**good, "min_position_notional": 50_000})
    with pytest.raises(Invalid, match="bounded by gross"):
        validate({**good, "max_net_exposure": 5})

    # A configuration is the whole set; a partial one takes defaults nobody
    # chose, and an unknown field is a limit the operator believes exists.
    with pytest.raises(Invalid, match="missing"):
        validate({k: v for k, v in good.items() if k != "max_leverage"})
    with pytest.raises(Invalid, match="unknown"):
        validate({**good, "max_hope": 3})


@pytest.mark.fast
def test_the_form_cannot_offer_what_the_server_refuses(client: TestClient) -> None:
    """The bounds reach the form from the same place the endpoint checks them,
    so the two cannot drift. A bound in the form is only a suggestion anyway —
    the endpoint is reachable without it — but one that disagrees with the
    server is worse than none, because it teaches the operator a wrong range.
    """
    markup = client.get("/static/risk_limits.html").text
    assert 'min="${escape(l.low)}" max="${escape(l.high)}"' in markup
    assert "l.bound" in markup, "the form does not show the reason a value is refused"
    # And a rationale is required, because a limit change nobody can review is
    # one nobody did.
    assert 'id="c-why"' in markup


@pytest.mark.integration
def test_exactly_one_risk_configuration_is_active(monkeypatch) -> None:
    """The one in force is the newest, so "only one is active" holds by
    construction rather than by a flag somebody has to keep correct. A flag can
    be set on two rows; a maximum cannot be reached twice.

    The case that would break it is a tie, which the database refuses — two
    configurations sharing a timestamp would make "newest" ambiguous, and the
    screen could show ACTIVE against a row a run would not pick up.
    """
    import uuid as uuid_

    import sqlalchemy as sa_

    from tradelab.config import Settings

    monkeypatch.setenv("TRADELAB_ADMIN_SALT", SALT)
    monkeypatch.setenv("TRADELAB_ADMIN_HASH", hash_password(PASSWORD, SALT))
    monkeypatch.setenv("TRADELAB_SESSION_SECRET", new_secret())
    engine = sa_.create_engine(Settings().database_url)
    client = TestClient(create_app(engine=engine))

    email = engine.connect().execute(
        sa_.text("SELECT email FROM users LIMIT 1")).scalar()
    if email is None:
        pytest.skip("no operator row")
    assert client.post(
        "/api/session", json={"email": email, "password": PASSWORD}).status_code == 200

    probes = [uuid_.uuid4() for _ in range(2)]
    stamp = "2031-01-01 00:00:00+00"
    try:
        with engine.begin() as conn:
            conn.execute(sa_.text(
                "INSERT INTO risk_limit_configs VALUES (:i,'ZZPROBE-A',5,2,10,10,"
                f"10000,0.001,0.1,60,2.0,1.0,3.0,0.05,0.2,10,3,50,'{stamp}')"),
                {"i": probes[0]})

        body = client.get("/api/risk-limits").json()
        active = [h for h in body["history"] if h["active"]]
        assert len(active) == 1, f"{len(active)} configurations claim to be active"
        # The newest wins, and the values on the page come from that same row.
        assert active[0]["name"] == "ZZPROBE-A"
        assert body["config_name"] == "ZZPROBE-A"

        # A tie is refused rather than ordered by something nobody would look at.
        with pytest.raises(sa_.exc.IntegrityError), engine.begin() as conn:
            conn.execute(sa_.text(
                "INSERT INTO risk_limit_configs VALUES (:i,'ZZPROBE-B',5,2,10,10,"
                f"10000,0.001,0.1,60,2.0,1.0,3.0,0.05,0.2,10,3,50,'{stamp}')"),
                {"i": probes[1]})
    finally:
        with engine.begin() as conn:
            conn.execute(sa_.text("DELETE FROM risk_limit_configs WHERE id = ANY(:i)"),
                         {"i": probes})


@pytest.mark.fast
def test_the_screen_marks_one_active_and_the_rest_inactive(
    client: TestClient,
) -> None:
    """Which one is in force is shown, not inferred from a date being the
    largest — that is a comparison the reader has to make, and gets wrong when
    the list is long."""
    markup = client.get("/static/risk_limits.html").text
    assert "h.active" in markup
    assert '"active" : "inactive"' in markup or "active\" : \"inactive" in markup
    # Decided by the server from the row the values also come from, so the badge
    # and the numbers on the page cannot disagree.
    assert "h.active ?" in markup


@pytest.mark.fast
def test_the_screen_offers_one_scale_with_five_in_the_middle(
    client: TestClient,
) -> None:
    """The three direction buttons are gone. Five is neutral, and the scale runs
    bearish-left to bullish-right like the sentiment scale beneath it, so the
    two cannot disagree about which way is which.

    A date is still not offered: the trend is a condition to test under and not
    a record of something that happened.
    """
    markup = client.get("/static/market_sentiment.html").text

    for absent in ('id="t-up"', 'id="t-normal"', 'id="t-down"',
                   'type="date"', 'type="datetime-local"', 'id="t-when"'):
        assert absent not in markup, f"the screen still offers {absent}"

    assert 'id="t-mag" min="1" max="10"' in markup
    # The neutral point is marked on the bar, not left to be inferred from a
    # number being halfway along a track that does not start at zero.
    assert 'id="t-neutral"' in markup
    assert "nothing new" in markup


@pytest.mark.fast
def test_the_menu_is_the_working_loop_and_nothing_else(client: TestClient) -> None:
    """Set the conditions, run the algorithm, see where it stops, see what would
    stop it if it ran — plus the record. Everything else that was on the menu
    either had its number already showing on Home or Algorithm, or was a
    promise: two entries marked "soon" advertised screens that did not exist and
    could not be reached.

    Asserted as equality, because the reason to add one back is worth having to
    state.
    """
    import re

    menu = client.get("/static/app.js").text
    linked = re.findall(r'href: "(/[a-z_-]+)"', menu)

    assert linked == [
        "/home", "/algorithm", "/market-sentiment", "/parameters",
        "/audit", "/risk-limits",
    ], f"the menu is now {linked}"

    # Nothing is advertised that cannot be opened.
    assert "soon: true" not in menu
    for href in linked:
        assert client.get(href).status_code == 200, f"{href} is offered but 404s"


@pytest.mark.fast
def test_a_removed_screen_leaves_its_data_behind(client: TestClient) -> None:
    """The screens went; the tables, the endpoints and the rows did not. What
    was removed is a page and a link, so a count that mattered still reaches
    the operator from where it was already shown.
    """
    paths = {route.path for route in client.app.routes}
    for endpoint in ("/api/strategies", "/api/experiments", "/api/data-quality"):
        assert endpoint in paths, f"{endpoint} went with its screen"

    # And the counts survive where they were already displayed.
    assert "data-quality" in client.get("/static/index.html").text
    assert "experiments" in client.get("/static/algorithm.html").text


@pytest.mark.fast
def test_the_home_page_reads_only_fields_the_scenario_returns(client: TestClient) -> None:
    """Home showed "NEUTRAL undefined" for as long as it outlived b3f90ee: the
    trend became one 1-10 scale, `magnitude` left the response, and the card
    went on reading it — and on testing for a "NORMAL" that no longer exists."""
    import inspect
    import re

    route = next(r for r in client.app.routes
                 if getattr(r, "path", None) == "/api/scenario"
                 and "GET" in getattr(r, "methods", set()))
    returned = set(re.findall(r'"(\w+)":', inspect.getsource(route.endpoint)))
    page = client.get("/home").text
    read = set(re.findall(r"scenario\.(\w+)", page))

    assert read, "the home page no longer reads the scenario; update this test"
    assert read <= returned, f"home reads {sorted(read - returned)}, which is never sent"
    assert '"NORMAL"' not in page, "NORMAL is not a direction the scale produces"


@pytest.mark.fast
def test_every_open_position_shows_who_guided_it(client: TestClient) -> None:
    """PREREG-009: the operator's reading and the algorithm's rule share one
    book, and the operator asked to see which sized each position."""
    import inspect

    route = next(r for r in client.app.routes
                 if getattr(r, "path", None) == "/api/positions")
    source = inspect.getsource(route.endpoint)
    for field in ('"guide"', '"trend"', '"trend_source"'):
        assert field in source
    markup = client.get("/static/index.html").text
    assert "<th>Guide</th>" in markup
    assert "not validated" in markup
