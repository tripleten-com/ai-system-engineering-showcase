# syntax=docker/dockerfile:1
#
# Build context is the REPOSITORY ROOT, not this directory: incident-agent-api is a
# uv workspace member, so the build needs uv.lock and packages/contracts in scope.
# See infra/docker-compose.yml.
ARG UV_VERSION=0.11.8
ARG PYTHON_IMAGE=python:3.11.11-slim

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---- builder -----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/incident-agent-api services/incident-agent-api

# --frozen: resolve from the committed lockfile or fail. --no-dev: no test tooling ships.
RUN uv sync --frozen --no-dev --package incident-agent-api

# ---- runtime -----------------------------------------------------------------
FROM ${PYTHON_IMAGE}
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder --chown=appuser:appuser /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER appuser
EXPOSE 8000
CMD ["uvicorn", "incident_agent_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
