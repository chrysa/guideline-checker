# ── Stage 1: deps — install production dependencies ───────────────────────────
FROM python:3.14-slim AS deps

# The build context excludes .git, so setuptools-scm cannot read the tag. The
# release/deploy passes the GitVersion tag as --build-arg VERSION=<tag> to stamp
# the real version; a plain build falls back to a deterministic dev version.
ARG VERSION=0.0.0+unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

WORKDIR /app

COPY pyproject.toml README.md ./
COPY guideline_checker/ ./guideline_checker/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# ── Stage 2: test — run the full test suite ───────────────────────────────────
FROM deps AS test

# The lifecycle checks inspect real git repositories, so the test image needs git.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Editable install so coverage records repo-relative paths (guideline_checker/…)
# instead of site-packages paths, which SonarCloud cannot map to source files.
RUN pip install --no-cache-dir -e ".[dev]"

COPY tests/ ./tests/
# The shipped referential — tests assert against it (test_guidelines, test_declarative_detectors).
COPY guidelines/ ./guidelines/

CMD ["pytest", "tests", "-v", \
    "--cov=guideline_checker", \
    "--cov-report=xml:/app/coverage.xml", \
    "--cov-report=term-missing"]

# ── Stage 3: lint — ruff + mypy quality checks ────────────────────────────────
FROM test AS lint

CMD ["sh", "-c", "ruff check guideline_checker && mypy guideline_checker"]

# ── Stage 4: web — FastAPI dashboard ─────────────────────────────────────────
FROM deps AS web-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir ".[web]"

FROM python:3.14-slim AS web

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCAN_ROOT=/workspace

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=web-deps /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=web-deps /usr/local/bin /usr/local/bin
COPY guideline_checker/ /app/guideline_checker/

USER appuser

EXPOSE 8080

HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1

CMD ["uvicorn", "guideline_checker.web.app:app", \
    "--host", "0.0.0.0", "--port", "8080"]

# ── Stage 5: production — minimal CLI image ───────────────────────────────────
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

# ── Stage 6: dev — production + test/lint/debug tooling (editable install) ─────
FROM production AS dev

ARG VERSION=0.0.0+unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

# Install dev/test/lint tooling as root, then drop back to the non-root user.
USER root
WORKDIR /app

COPY pyproject.toml README.md ./
COPY tests/ ./tests/
COPY guidelines/ ./guidelines/

RUN pip install --no-cache-dir -e ".[dev]"

USER appuser

ENTRYPOINT []
CMD ["bash"]
