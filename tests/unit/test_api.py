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
    # The complete set of writes this surface is allowed. §20.1 permits
    # approvals and lifecycle transitions; §20.5 adds event annotations, which
    # are an *observation recorded with its time* rather than a computed figure,
    # and are therefore not an exception to "the application computes nothing".
    #
    # This list is meant to fail when a route is added. Extending it is the
    # moment to ask whether the new write belongs on this surface at all, and
    # that question is the reason the assertion is written as equality rather
    # than as a subset.
    assert sorted(writable) == [
        ("/api/approvals", "POST"),
        ("/api/events/{event_id}/annotations", "POST"),
        ("/api/macro/releases/{release_id}/consensus", "POST"),
        ("/api/session", "DELETE"),
        ("/api/session", "POST"),
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
    import tradelab.api.app as module

    source = Path(module.__file__).read_text()
    for forbidden in ("numpy", "sharpe_stats", "deflated_sharpe", "np."):
        assert forbidden not in source, f"the API imports {forbidden}"


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
def test_writes_are_still_the_four_plus_consensus(client: TestClient) -> None:
    """The route table again, now that macro added one. Written as equality so
    that every addition has to be argued for rather than absorbed."""
    writable = sorted(
        (route.path, method)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    )
    assert writable == [
        ("/api/approvals", "POST"),
        ("/api/events/{event_id}/annotations", "POST"),
        ("/api/macro/releases/{release_id}/consensus", "POST"),
        ("/api/session", "DELETE"),
        ("/api/session", "POST"),
    ], f"a new write route appeared: {writable}"


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
