# ── Stage 1: deps — install production dependencies ───────────────────────────
FROM python:3.14-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY guideline_checker/ ./guideline_checker/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# ── Stage 2: test — run the full test suite ───────────────────────────────────
FROM deps AS test

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir ".[dev]"

COPY tests/ ./tests/

CMD ["pytest", "tests", "-v", \
    "--cov=guideline_checker", \
    "--cov-report=xml:/app/coverage.xml", \
    "--cov-report=term-missing"]

# ── Stage 3: lint — ruff + mypy quality checks ────────────────────────────────
FROM test AS lint

CMD ["sh", "-c", "ruff check guideline_checker && mypy guideline_checker"]

# ── Stage 4: production — minimal CLI image ───────────────────────────────────
FROM python:3.14-slim AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=deps /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY guideline_checker/ /app/guideline_checker/

USER appuser

ENTRYPOINT ["guideline-checker"]
CMD ["--help"]
