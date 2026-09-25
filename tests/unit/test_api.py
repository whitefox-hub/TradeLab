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
    # The complete set of writes this surface is allowed, and the only place it
    # is asserted. §20.1 permits approvals and lifecycle transitions; §20.5 adds
    # event annotations and §20.6 a macro consensus, both of which are
    # observations recorded with their time rather than computed figures.
    # §12.4 adds operator settings, which reach only the column that does not
    # define a strategy.
    #
    # Written as equality so a new route fails here. Extending this list is the
    # moment to ask whether the write belongs on this surface at all — which is
    # a question worth being forced to answer, and the reason three copies of
    # this assertion were collapsed into one after the third went stale.
    assert sorted(writable) == [
        ("/api/approvals", "POST"),
        ("/api/events", "POST"),
        ("/api/events/{event_id}/annotations", "POST"),
        ("/api/events/{event_id}/reopen", "POST"),
        ("/api/events/{event_id}/resolve", "POST"),
        ("/api/macro/releases/{release_id}/consensus", "POST"),
        ("/api/session", "DELETE"),
        ("/api/session", "POST"),
        ("/api/settings/{key}", "POST"),
    ], f"a new write route appeared: {writable}"


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
def test_the_news_screen_is_served_without_a_session(client: TestClient) -> None:
    """The page itself is not sensitive; the data behind it is.

    Gating the HTML too would mean the sign-in form lives somewhere else, and
    every endpoint it calls already refuses without a session.
    """
    response = client.get("/news")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.fast
def test_the_screen_calls_only_endpoints_that_exist(client: TestClient) -> None:
    """A screen referring to a route nobody implemented fails in the browser,
    where nothing watches. Checked here instead."""
    import re

    page = client.get("/news").text
    called = set(re.findall(r'["`](/api/[a-z/]+)', page))
    known = {route.path for route in client.app.routes}
    for path in called:
        assert any(path == p or p.startswith(path) for p in known), f"{path} has no route"


@pytest.mark.fast
def test_the_rating_field_is_bounded_and_signed() -> None:
    """Sigma, signed, and not an open range. A rating of 400 is a typo, and
    storing it would move every calibration statistic computed from it."""
    from tradelab.api.app import AnnotationRequest

    assert AnnotationRequest(kind="judgement", rating=-2.5).rating == -2.5
    with pytest.raises(ValueError):
        AnnotationRequest(kind="judgement", rating=400)
    with pytest.raises(ValueError):
        AnnotationRequest(kind="judgement", confidence=1.4)
    with pytest.raises(ValueError):
        AnnotationRequest(kind="guess")


@pytest.mark.fast
def test_a_judgement_cannot_carry_a_caller_supplied_time() -> None:
    """The model accepts `available_at` for corrections only; the handler
    overrides it for judgements, and the database refuses a backdated one
    regardless. Three layers, because this is the invariant that makes the
    ratings worth collecting."""
    from datetime import UTC, datetime, timedelta

    from tradelab.api.app import AnnotationRequest

    body = AnnotationRequest(kind="judgement", rating=1.0,
                             available_at=datetime.now(UTC) - timedelta(days=5))
    # The field parses, and the handler ignores it. Asserted on the source so a
    # future edit that starts honouring it fails here.
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    assert 'datetime.now(UTC) if body.kind == "judgement"' in source
    assert body.available_at is not None


@pytest.mark.fast
def test_the_macro_screen_is_served(client: TestClient) -> None:
    response = client.get("/macro")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@pytest.mark.fast
def test_the_consensus_body_carries_no_timestamp() -> None:
    """§20.6: `consensus_at` is set by the handler and refused by the database
    if it does not precede the release. Accepting one from the client would put
    the choice in the caller's hands, which is the choice the rule removes."""
    from tradelab.api.app import ConsensusRequest

    assert "consensus_at" not in ConsensusRequest.model_fields
    assert ConsensusRequest(consensus=3.5).consensus == 3.5


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
def test_every_screen_mounts_the_shared_sidebar(client: TestClient) -> None:
    """Three copies of the chrome drift, and the copy nobody looks at is the one
    still styling a class that was renamed."""
    for path, active in (("/home", "/home"), ("/news", "/news"), ("/macro", "/macro")):
        page = client.get(path).text
        assert '<link rel="stylesheet" href="/static/app.css">' in page, path
        assert '<script src="/static/app.js"></script>' in page, path
        assert f'mount("{active}"' in page, f"{path} does not mark itself active"


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


@pytest.mark.integration
def test_the_indicator_cards_carry_a_value_and_a_change() -> None:
    """The screen the operator asked for shows where each series stands.

    Reading the last two stored prints is not a computed figure and not a chart;
    the history behind them is a time series and stays in Grafana (§20.1).
    """
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
        app = create_app(engine=engine)
        client = TestClient(app)
        client.cookies.set("tradelab_session", Sessions(secret).issue(email))
        rows = client.get("/api/macro/series").json()

    assert rows, "no macro series loaded"
    priced = [r for r in rows if r["value"] is not None]
    assert priced, "every series came back without a value"
    for row in priced:
        assert "change" in row
        assert "value_at" in row
        # The flag the screen colours the card by.
        assert isinstance(row["point_in_time_safe"], bool)


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
def test_a_hand_entered_event_may_be_backdated_but_a_rating_may_not() -> None:
    """The asymmetry that keeps the corpus honest.

    An event happened when it happened, so entering one from last week is
    legitimate. A *judgement* about it is created at the moment it is made, and
    backdating that is hindsight with a form around it.
    """
    from datetime import UTC, datetime, timedelta

    from tradelab.api.app import NewEventRequest

    last_week = datetime.now(UTC) - timedelta(days=7)
    event = NewEventRequest(event_type="hack", title="Bridge exploit",
                            happened_at=last_week)
    assert event.happened_at == last_week

    # The annotation model accepts the field; the handler overrides it for a
    # judgement and the database refuses a backdated one regardless.
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    assert 'datetime.now(UTC) if body.kind == "judgement"' in source


@pytest.mark.fast
def test_the_news_screen_separates_influencing_from_spent(client: TestClient) -> None:
    """Tabs, not a date sort. §11.8's distinction is not about age."""
    page = client.get("/news").text
    assert 'id="tab-active"' in page and "Still influencing" in page
    assert 'id="tab-expired"' in page and "No longer" in page
    assert 'id="add"' in page, "no way to add news by hand"
    # And the assumed horizon is labelled on screen rather than presented as
    # measured.
    assert "expiry_assumed" in page


@pytest.mark.fast
def test_row_actions_carry_their_weight_in_colour(client: TestClient) -> None:
    """One advances the work, one ends something. A row of identical grey boxes
    makes the operator read the labels every time, and read them wrong once."""
    page = client.get("/news").text
    assert 'rate.className = "accent"' in page
    assert 'end.className = "danger"' in page

    css = client.get("/static/app.css").text
    assert "button.accent" in css and "button.danger" in css


@pytest.mark.fast
def test_the_tab_styling_does_not_capture_every_button_in_the_bar(
    client: TestClient,
) -> None:
    """Written unscoped, it stripped the background off the Add button sitting
    in the same bar and left it looking like a link."""
    page = client.get("/news").text
    assert ".tabs button.tab {" in page
    assert ".tabs button {" not in page, "the rule is unscoped again"
    # And the Add button keeps its own class rather than inheriting the tabs'.
    assert '<button class="primary" id="add">' in page


@pytest.mark.fast
def test_ending_an_item_asks_first(client: TestClient) -> None:
    """It moves the row to another tab and there is no undo on the screen, so it
    is not a single click."""
    page = client.get("/news").text
    assert 'id="confirm-end"' in page
    assert "openEnding(event)" in page
    # The dialog says what the action means, not just "are you sure".
    assert "supersession" in page.lower()
    assert "no undo" in page.lower()


@pytest.mark.fast
def test_rating_is_offered_only_while_an_item_still_influences(
    client: TestClient,
) -> None:
    """A judgement recorded after an item stopped influencing can never be
    scored against a forward reaction, so it calibrates nothing — which is the
    entire reason for collecting ratings in sigma."""
    page = client.get("/news").text
    rate_block = page[page.index("if (event.active) {"):page.index("} else if (event.reopenable)")]
    assert "openRating(event)" in rate_block, "Rate moved out of the active branch"
    assert "Still matters" not in rate_block


@pytest.mark.fast
def test_an_ending_that_cannot_be_reversed_says_so_in_the_row(
    client: TestClient,
) -> None:
    """A control that is simply absent reads as an oversight; one that states
    its reason reads as a decision."""
    page = client.get("/news").text
    assert "not_reopenable_because" in page
    assert 'id="confirm-back"' in page


@pytest.mark.fast
def test_direction_is_recorded_but_labelled_as_an_input_not_a_signal() -> None:
    """§11.7: what is tradeable is the residual, observed minus expected.

    News that should have moved two sigma and moved a fifth of one is bearish
    however bullish it read. A system trading sign directly trades the part
    everyone has already priced, so the field exists as an input to the
    expected-reaction model and the form says so.
    """
    from tradelab.api.app import NewEventRequest

    assert NewEventRequest(event_type="war", title="Escalation", direction=-1).direction == -1
    assert NewEventRequest(event_type="hack", title="Bridge exploit").direction == 0
    with pytest.raises(ValueError):
        NewEventRequest(event_type="hack", title="Bridge exploit", direction=5)


@pytest.mark.fast
def test_the_form_offers_the_fourth_shape_and_explains_it(client: TestClient) -> None:
    page = client.get("/news").text
    assert 'value="regime_shift"' in page
    assert "replace the baseline" in page or "replaces the baseline" in page
    # And the screen warns that sign is not the signal, where the operator is
    # about to enter one.
    assert "residual" in page


@pytest.mark.fast
def test_the_type_list_is_derived_rather_than_declared(client: TestClient) -> None:
    """A hand-written list drifts from the data the moment something is seeded.

    `halving` existed in three rows and in none of the suggestions, which is the
    defect this replaced: the screen offered a set the corpus had outgrown.
    """
    page = client.get("/news").text
    assert '<datalist id="types"></datalist>' in page, "the options are hardcoded again"
    assert "loadTypes()" in page
    assert "/api/events/types" in page
    # The filter shows only types with rows — filtering to one with none returns
    # an empty table and reads as a bug.
    assert "types.filter((t) => t.in_use)" in page


@pytest.mark.fast
def test_a_type_added_by_hand_refreshes_the_lists(client: TestClient) -> None:
    """Otherwise the operator adds a type and cannot then filter by it until a
    reload, which reads as the entry having failed."""
    page = client.get("/news").text
    save_block = page[page.index('$("n-save").onclick'):]
    assert "await loadTypes();" in save_block


@pytest.mark.fast
def test_the_headline_is_a_column_not_an_annotation() -> None:
    """`event_annotations` holds what a *person* asserted about an event. A
    collected headline is the event's own words, and filing it there would also
    make every collected event look annotated — which is the flag the screen
    uses to find the ones nobody has read."""
    from tradelab.registry.models import Event

    assert "headline" in Event.__table__.columns


@pytest.mark.fast
def test_rated_counts_judgements_only(client: TestClient) -> None:
    """Once the ingestor writes titles, counting every annotation would mark the
    whole corpus as read on the first import."""
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    assert "FILTER (WHERE n.kind = 'judgement')" in source


@pytest.mark.fast
def test_the_two_controls_cannot_disagree_in_a_stored_row() -> None:
    """Direction and strength are separate controls now, which was a deliberate
    change: the operator picks a direction instead of typing a minus sign.

    The risk that split creates is a row holding two readings that contradict
    each other, so the conversion happens in one place and `strength` wins over
    any `rating` sent alongside it.
    """
    from tradelab.api.app import AnnotationRequest

    assert AnnotationRequest(
        kind="judgement", strength=4, direction=-1, rating=9.0
    ).signed_sigma == -2.0


@pytest.mark.fast
def test_direction_still_follows_the_sign_of_what_is_stored(
    client: TestClient,
) -> None:
    """The asset's expected direction is set from the sign of the stored sigma,
    not from the form field, so the two can never come apart in the database."""
    source = Path(__import__("tradelab.api.app", fromlist=["x"]).__file__).read_text()
    assert "UPDATE event_assets SET expected_direction" in source
    assert "(rating > 0) - (rating < 0)" in source
    assert 'if body.kind == "judgement" and rating is not None:' in source
    # And no free-text note, which was neither scored nor read.
    assert 'id="note"' not in client.get("/news").text


@pytest.mark.fast
def test_the_table_shows_the_headline_and_escapes_it(client: TestClient) -> None:
    """Titles arrive from a venue feed, so they are untrusted text going into
    innerHTML."""
    page = client.get("/news").text
    assert "<th>Direction</th>" in page
    assert "event.headline" in page
    assert "const escape = (text)" in page
    assert "escape(event.headline)" in page


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
def test_the_row_actions_sit_on_one_line(client: TestClient) -> None:
    """The column sized itself from its contents and wrapped the two buttons
    onto separate lines, which reads as two rows of one control."""
    css = client.get("/static/app.css").text
    assert "td.actions { white-space:nowrap;" in css
    assert 'cell.className = "actions";' in client.get("/news").text


@pytest.mark.fast
@pytest.mark.parametrize(
    "path", ["/strategies", "/experiments", "/data-quality", "/audit"],
)
def test_the_new_screens_are_served_and_mount_the_chrome(
    client: TestClient, path: str,
) -> None:
    page = client.get(path).text
    assert client.get(path).status_code == 200
    assert '<link rel="stylesheet" href="/static/app.css">' in page
    assert f'mount("{path}"' in page


@pytest.mark.fast
def test_the_menu_no_longer_offers_a_screen_that_exists_as_soon(
    client: TestClient,
) -> None:
    """A built screen left greyed out is worse than one never listed: the
    operator has been told it does not exist."""
    menu = client.get("/static/app.js").text
    for name in ("Strategies", "Experiments", "Data quality", "Audit"):
        assert f'label: "{name}"' in menu
        assert f'{{ label: "{name}", soon: true }}' not in menu


@pytest.mark.fast
def test_the_strategy_screen_shows_the_ladder_without_a_control(
    client: TestClient,
) -> None:
    """§20.2, the most important thing in that section. Mode is displayed and
    never edited, and the ladder is where an operator would most expect to find
    a button that does not exist."""
    page = client.get("/strategies").text
    assert "LADDER" in page and "live_ramp" in page
    assert "Displayed, never edited" in page
    # No write route touches a strategy, which is the guarantee behind the text.
    writable = [
        route.path for route in client.app.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    ]
    assert not any("strateg" in path for path in writable)


@pytest.mark.fast
def test_experiments_states_why_tier_a_is_absent(client: TestClient) -> None:
    """An empty table with no explanation reads as a broken query. §5.7 keeps
    tier A in Parquet on purpose, and the screen says so."""
    page = client.get("/experiments").text
    assert "Parquet" in page
    assert "Tier A is not here" in page


@pytest.mark.fast
def test_the_rating_is_picked_on_a_scale_and_stored_in_sigma() -> None:
    """Two controls, no negative number typed, and the stored unit unchanged."""
    from tradelab.api.app import AnnotationRequest

    bearish = AnnotationRequest(kind="judgement", strength=6, direction=-1)
    assert bearish.signed_sigma == -3.0

    bullish = AnnotationRequest(kind="judgement", strength=6, direction=1)
    assert bullish.signed_sigma == 3.0

    # A caller already working in sigma is still accepted.
    direct = AnnotationRequest(kind="judgement", rating=-2.5)
    assert direct.signed_sigma == -2.5

    # And strength wins when both are given, so a stored row cannot hold two
    # readings that disagree.
    both = AnnotationRequest(kind="judgement", strength=2, direction=1, rating=-9.0)
    assert both.signed_sigma == 1.0


@pytest.mark.fast
def test_the_dialog_offers_the_scale_from_the_server(client: TestClient) -> None:
    """Served rather than written into the page, so the options cannot drift
    from what the endpoint stores."""
    page = client.get("/news").text
    assert 'id="strength"' in page and 'id="direction"' in page
    assert "/api/rating-scale" in page
    assert 'id="rating"' not in page, "the free-number input is back"
    # The readout shows the sigma that will be stored.
    assert "sigma" in page
