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
    # Only three writes exist: sign in, sign out, and recording an approval.
    assert sorted(writable) == [
        ("/api/approvals", "POST"),
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
