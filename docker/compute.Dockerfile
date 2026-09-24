# tradelab-compute — collectors, feature engine, experiment runner, validator, dagster.
# NOTE: base images must be pinned by sha256 before phase 0 is signed off (Plan.md §2.2).
# `make pin-digests` resolves the current digests and rewrites the FROM lines.
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

FROM base AS deps
WORKDIR /app
COPY pyproject.toml uv.lock* ./
# Process substitution is not available under /bin/sh, so this installs from
# pyproject directly. `uv.lock`, when present, is what actually pins versions.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".[collectors,research,orchestration]"

FROM base AS runtime
# The package is installed in the deps stage, before the source exists, so the
# console script resolves but the module does not. PYTHONPATH makes /app
# importable — and it is the only option that also works for tradelab-exec,
# which deliberately copies only part of the package.
ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv PYTHONPATH=/app
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY tradelab ./tradelab
# alembic.ini is required at runtime: `alembic upgrade head` reads
# `script_location` from it and fails with exit 255 without it.
COPY pyproject.toml alembic.ini ./
RUN useradd -m -u 1000 lab && chown -R lab /app
USER lab
CMD ["python", "-c", "import tradelab; print('tradelab-compute ready')"]
