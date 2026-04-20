# syntax=docker/dockerfile:1.9
# Python 3.14 multi-stage — reusable across chrysa Python services
# Build args:
#   PYTHON_VERSION (default 3.14-slim)
#   APP_PORT       (default 8000)
# Usage:
#   FROM ghcr.io/chrysa/shared-standards/dockerfile-python:latest AS base
#   (or copy this file directly into your repo and adapt)

# ── Build stage ──────────────────────────────────────────────────────────────
ARG PYTHON_VERSION=3.14-slim
FROM python:${PYTHON_VERSION} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_VERSION=1.8.4

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency install first (cached layer)
COPY pyproject.toml poetry.lock* requirements*.txt ./
RUN if [ -f pyproject.toml ]; then \
        pip install "poetry==${POETRY_VERSION}" && \
        poetry install --no-root --no-interaction --without dev ; \
    else \
        pip install -r requirements.txt ; \
    fi

# App code last
COPY . .
RUN if [ -f pyproject.toml ]; then poetry install --no-interaction --without dev ; fi

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION} AS runtime

ARG APP_PORT=8000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/app/.local/bin:$PATH \
    APP_PORT=${APP_PORT}

# Non-root user
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --create-home --shell /bin/bash app

# Copy installed packages + app from builder
COPY --from=builder --chown=app:app /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder --chown=app:app /usr/local/bin /usr/local/bin
COPY --from=builder --chown=app:app /build /app

WORKDIR /app
USER app

EXPOSE ${APP_PORT}

# Healthcheck — app must expose /healthz returning 200
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${APP_PORT}/healthz" || exit 1

# OCI labels (override in CI via --label)
LABEL org.opencontainers.image.source="https://github.com/chrysa/REPLACE-ME" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.vendor="chrysa"

# Override CMD per project
CMD ["python", "-m", "REPLACE_ME.main"]
