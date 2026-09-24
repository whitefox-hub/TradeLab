# tradelab-exec — executor and risk engine ONLY.
# Deliberately excludes research dependencies, notebook tooling and LLM clients.
# This is a credential boundary, not a size optimisation (Plan.md §2.2, §14.4).
FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

FROM base AS deps
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".[exec]"

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime
# The package is installed in the deps stage, before the source exists, so the
# console script resolves but the module does not. PYTHONPATH makes /app
# importable — and it is the only option that also works for tradelab-exec,
# which deliberately copies only part of the package.
ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY tradelab/execution ./tradelab/execution
COPY tradelab/risk ./tradelab/risk
COPY tradelab/registry ./tradelab/registry
COPY tradelab/__init__.py tradelab/config.py ./tradelab/
RUN useradd -m -u 1000 exec_user && chown -R exec_user /app
USER exec_user
CMD ["python", "-c", "print('tradelab-exec ready')"]
