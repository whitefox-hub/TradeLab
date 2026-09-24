from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tradelab.determinism import golden_run

PROBE = (
    "from tradelab.determinism import golden_run; print(golden_run())"
)


def _digest_with_threads(threads: int, repo_root: Path) -> str:
    """Compute the golden digest in a fresh process with a given thread count."""
    env = {
        **os.environ,
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads),
        "OPENBLAS_NUM_THREADS": str(threads),
        "RAYON_NUM_THREADS": str(threads),
        "POLARS_MAX_THREADS": str(threads),
        "TZ": "UTC",
    }
    out = subprocess.run(
        [sys.executable, "-c", PROBE], env=env, cwd=repo_root,
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.mark.medium
def test_golden_run_reproduces_bit_identically(golden_path: Path) -> None:
    """Numerical regression canary. Plan.md §9.4, §16.6.

    Catches library upgrades, platform differences and code changes that move a
    number. Failure is blocking and is NOT fixed by updating the expected value;
    `make golden-update` demands a written reason for that.
    """
    expected = json.loads(golden_path.read_text())
    assert golden_run() == expected["digest"], (
        "Golden run changed. Either the environment is not pinned (run `make doctor`) "
        "or a code change altered numerical results. Both are blocking."
    )


@pytest.mark.medium
def test_golden_run_is_stable_within_a_process() -> None:
    assert golden_run() == golden_run()


@pytest.mark.slow
@pytest.mark.parametrize("threads", [1, 2, 8])
def test_golden_run_is_thread_invariant(threads: int, repo_root: Path) -> None:
    """Verify thread-invariance rather than assume it. Plan.md §2.2.

    Float reduction order *can* depend on thread count, but whether it does is a
    property of the specific library and build — not a law. Measured here so that
    a future dependency which does introduce it is caught on the day it lands,
    instead of surfacing months later as unexplained non-determinism in a Sharpe
    ratio. If this fails, the fix is to pin threads harder, not to relax the test.
    """
    assert _digest_with_threads(threads, repo_root) == _digest_with_threads(1, repo_root)
