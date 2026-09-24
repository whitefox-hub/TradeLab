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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache \
        -r <(uv export --no-hashes --extra collectors --extra research --extra orchestration 2>/dev/null || echo "") \
    || VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".[collectors,research,orchestration]"

FROM base AS runtime
ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY tradelab ./tradelab
COPY pyproject.toml ./
RUN useradd -m -u 1000 lab && chown -R lab /app
USER lab
CMD ["python", "-c", "import tradelab; print('tradelab-compute ready')"]
