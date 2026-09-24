from __future__ import annotations

import pytest

from tradelab.determinism import REQUIRED_ENV, NonDeterministicEnvironment, check_environment


@pytest.mark.fast
def test_conftest_pinned_the_environment() -> None:
    assert check_environment(strict=False) == []


@pytest.mark.fast
def test_unpinned_environment_raises_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpinned environment must fail, not warn. Results here are not recordable."""
    monkeypatch.setenv("POLARS_MAX_THREADS", "16")
    with pytest.raises(NonDeterministicEnvironment, match="POLARS_MAX_THREADS"):
        check_environment(strict=True)


@pytest.mark.fast
def test_required_env_matches_compose(repo_root) -> None:
    """The guard and docker-compose.yml must not drift apart."""
    compose = (repo_root / "docker-compose.yml").read_text()
    for key in REQUIRED_ENV:
        assert f"{key}:" in compose, f"{key} is guarded in code but missing from compose"
