# tradelab-api — FastAPI + built front end. Read-heavy; writes only approvals.
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

FROM base AS deps
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".[api]"

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv PYTHONUNBUFFERED=1
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app
COPY tradelab ./tradelab
RUN useradd -m -u 1000 api && chown -R api /app
USER api
EXPOSE 8000
CMD ["uvicorn", "tradelab.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
