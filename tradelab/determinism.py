"""Determinism guards and the golden run. Plan.md §2.2, §9.4, §16.6.

Float reduction order depends on thread count, so the same code in the same image
produces different results on a 4-core and a 16-core machine. That breaks the
bit-identical golden run and surfaces as non-determinism in the statistics, which
is the last place anyone looks. These guards make the failure loud and early.
"""

from __future__ import annotations

import hashlib
import os

import polars as pl

# Pinned in docker-compose.yml under x-determinism.
REQUIRED_ENV: dict[str, str] = {
    "PYTHONHASHSEED": "0",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "POLARS_MAX_THREADS": "4",
    "NUMBA_NUM_THREADS": "4",
    "TZ": "UTC",
}


class NonDeterministicEnvironment(RuntimeError):
    """Raised when the environment cannot produce reproducible results."""


def check_environment(strict: bool = True) -> list[str]:
    """Return env vars that are unset or wrong. Raise if strict."""
    problems = [
        f"{key}={os.environ.get(key, '<unset>')!r} (expected {expected!r})"
        for key, expected in REQUIRED_ENV.items()
        if os.environ.get(key) != expected
    ]
    if problems and strict:
        raise NonDeterministicEnvironment(
            "Environment is not pinned for reproducible results:\n  "
            + "\n  ".join(problems)
            + "\nSee Plan.md §2.2. Results produced here must not be recorded."
        )
    return problems


def golden_run(n_rows: int = 500_000, n_groups: int = 997, seed: int = 20260101) -> str:
    """A deterministic, parallel-reduction-heavy computation.

    Exercises exactly the code paths whose results depend on thread count:
    parallel group-by aggregation over float columns. The returned digest must be
    identical on every machine, which is what proves the thread pinning works.
    """
    idx = pl.int_range(0, n_rows, eager=True)
    # Deterministic pseudo-random floats without depending on a RNG implementation.
    x = ((idx * 6364136223846793005 + seed) % 2147483647).cast(pl.Float64) / 2147483647.0
    frame = pl.DataFrame({"g": (idx % n_groups), "x": x, "y": (x * 1.0000001 - 0.5)})

    out = (
        frame.lazy()
        .group_by("g")
        .agg(
            pl.col("x").sum().alias("sx"),
            pl.col("y").mean().alias("my"),
            pl.col("y").std(ddof=1).alias("sy"),
            (pl.col("x") * pl.col("y")).sum().alias("sxy"),
        )
        .sort("g")
        .collect()
    )

    digest = hashlib.sha256()
    for col in ("sx", "my", "sy", "sxy"):
        # repr of float64 is exact round-trip in CPython, so this hashes the bits.
        digest.update("".join(repr(v) for v in out[col].to_list()).encode())
    return digest.hexdigest()
