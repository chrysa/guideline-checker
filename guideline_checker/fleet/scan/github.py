"""Async GitHub REST client: read-only GETs with bounded, header-driven resilience.

* Concurrency is bounded by a semaphore (``github.concurrency``).
* Pagination follows the ``Link: rel="next"`` header up to ``pagination.max_pages``; a
  ``next`` link pointing at another host is refused, so the token never leaves the API host.
* 5xx and transport failures are retried ``retries.max_attempts`` times with exponential
  backoff + jitter.
* Primary rate limit: ``x-ratelimit-remaining``/``x-ratelimit-reset`` are tracked; at or
  below ``min_remaining`` the client waits for the reset when it is ``<= max_wait_s``,
  otherwise raises :class:`RateLimitedError`. Limits come from headers, never constants.
* Secondary rate limit (403/429 with ``retry-after`` or a "secondary rate limit" message)
  waits ``retry-after`` (or ``secondary_backoff_s``) and retries within the same budget.
* ETag conditional requests against an on-disk cache (``304`` reuses the cached body).

The token is sent only as an ``Authorization`` header and is never logged (a redaction
filter is installed on this module's and httpx's loggers).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from guideline_checker.fleet.scan.errors import (
    AuthError,
    ForbiddenError,
    GithubError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    SecondaryRateLimitedError,
    ServerError,
)
from guideline_checker.fleet.scan.http_cache import CacheEntry, EtagCache
from guideline_checker.fleet.scan.models import GithubConfig
from guideline_checker.fleet.scan.redaction import install_redaction

logger = logging.getLogger("guideline_checker.fleet.scan.github")

API_VERSION = "2022-11-28"
_ACCEPT = "application/vnd.github+json"
_SECONDARY_MARKER = "secondary rate limit"
_RATE_LIMITED_STATUSES = frozenset({HTTPStatus.FORBIDDEN, HTTPStatus.TOO_MANY_REQUESTS})

Sleeper = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class ApiResponse:
    """A decoded JSON body plus its provenance (endpoint without host, fetch time)."""

    data: Any
    endpoint: str
    fetched_at: datetime
    from_cache: bool = False


@dataclass(frozen=True)
class PagedResponse:
    """All items of a paginated list. ``truncated`` = ``max_pages`` hit with a ``next`` link left."""

    items: list[Any]
    endpoint: str
    fetched_at: datetime
    pages: int
    truncated: bool


@dataclass(frozen=True)
class _Page:
    body: Any
    next_url: str | None
    fetched_at: float
    from_cache: bool


@dataclass(frozen=True)
class _Retry:
    error: GithubError
    delay_s: float | None  # None = exponential backoff


class GithubClient:
    """Read-only GitHub REST client. Use as ``async with GithubClient(...) as gh``."""

    def __init__(
        self,
        *,
        config: GithubConfig,
        token: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_root: Path | None = None,
        sleep: Sleeper = asyncio.sleep,
        clock: Clock = time.time,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()  # noqa: S311 - jitter only, not cryptographic
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._remaining: int | None = None
        self._reset_epoch: float | None = None
        install_redaction(secrets=(token,) if token else ())
        headers = {"Accept": _ACCEPT, "X-GitHub-Api-Version": API_VERSION, "User-Agent": "guideline-checker-fleet-scan"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(config.timeouts.read_s, connect=config.timeouts.connect_s),
            transport=transport,
            follow_redirects=True,
        )
        self._cache: EtagCache | None = None
        if config.cache.enabled:
            base = cache_root if cache_root is not None else Path.cwd()
            self._cache = EtagCache(
                directory=base / config.cache.dir, ttl_s=config.cache.ttl_s, clock=clock, token=token
            )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    @property
    def rate_remaining(self) -> int | None:
        return self._remaining

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(self, path: str, *, params: dict[str, str | int] | None = None) -> ApiResponse:
        """GET one resource and decode its JSON body."""
        url = self.url(path=path, params=params)
        page = await self._get(url)
        return ApiResponse(
            data=page.body, endpoint=self._endpoint(url), fetched_at=_utc(page.fetched_at), from_cache=page.from_cache
        )

    async def get_paginated(self, path: str, *, params: dict[str, str | int] | None = None) -> PagedResponse:
        """GET every page of a list endpoint, following ``Link: rel=next`` up to ``max_pages``."""
        query: dict[str, str | int] = {"per_page": self._config.pagination.per_page, **(params or {})}
        url: str | None = self.url(path=path, params=query)
        first_url = url
        items: list[Any] = []
        pages = 0
        fetched_at = self._clock()
        while url is not None and pages < self._config.pagination.max_pages:
            page = await self._get(url)
            if not isinstance(page.body, list):
                raise InvalidResponseError("expected a JSON list", endpoint=self._endpoint(url))
            items.extend(page.body)
            fetched_at = min(fetched_at, page.fetched_at) if pages else page.fetched_at
            pages += 1
            url = page.next_url
        return PagedResponse(
            items=items,
            endpoint=self._endpoint(first_url or ""),
            fetched_at=_utc(fetched_at),
            pages=pages,
            truncated=url is not None,
        )

    def url(self, *, path: str, params: dict[str, str | int] | None = None) -> str:
        """Absolute API URL with deterministically ordered query parameters (stable cache keys)."""
        if not path.startswith("/"):
            raise ValueError(f"API path must start with '/': {path!r}")
        query = urlencode(sorted((params or {}).items()))
        return f"{self._config.api_url}{path}" + (f"?{query}" if query else "")

    # ── request loop ──────────────────────────────────────────────────────────

    async def _get(self, url: str) -> _Page:
        # Every URL — including a next link replayed from the on-disk cache — must stay
        # on the API host before the token-bearing client sees it.
        if not url.startswith(f"{self._config.api_url}/"):
            raise InvalidResponseError("URL leaves the API host; refused", endpoint=self._endpoint(url))
        cached = self._cache.get(url) if self._cache is not None else None
        if cached is not None and self._cache is not None and self._cache.is_fresh(cached):
            return _Page(body=cached.body, next_url=cached.next_url, fetched_at=cached.fetched_at, from_cache=True)
        attempts = self._config.retries.max_attempts
        last_error: GithubError = NetworkError("no attempt made", endpoint=self._endpoint(url))
        for attempt in range(1, attempts + 1):
            await self._await_primary_budget(url)
            outcome = await self._attempt(url=url, cached=cached)
            if isinstance(outcome, _Page):
                return outcome
            last_error = outcome.error
            if attempt < attempts:
                delay = outcome.delay_s if outcome.delay_s is not None else self._backoff(attempt)
                logger.warning(
                    "retrying %s in %.1fs (%s, attempt %d/%d)", url, delay, last_error.kind, attempt, attempts
                )
                await self._sleep(delay)
        raise last_error

    async def _attempt(self, *, url: str, cached: CacheEntry | None) -> _Page | _Retry:
        headers: dict[str, str] = {}
        if cached is not None and cached.etag and self._config.cache.use_etag:
            headers["If-None-Match"] = cached.etag
        endpoint = self._endpoint(url)
        try:
            async with self._semaphore:
                response = await self._http.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            return _Retry(error=NetworkError(f"timeout ({type(exc).__name__})", endpoint=endpoint), delay_s=None)
        except httpx.TransportError as exc:
            return _Retry(
                error=NetworkError(f"transport error ({type(exc).__name__})", endpoint=endpoint), delay_s=None
            )
        self._record_rate_limit(response.headers)
        return self._classify(response=response, url=url, cached=cached)

    def _classify(self, *, response: httpx.Response, url: str, cached: CacheEntry | None) -> _Page | _Retry:
        status = response.status_code
        endpoint = self._endpoint(url)
        if status == HTTPStatus.NOT_MODIFIED and cached is not None and self._cache is not None:
            entry = self._cache.touch(cached)
            return _Page(body=entry.body, next_url=entry.next_url, fetched_at=entry.fetched_at, from_cache=True)
        if HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
            return self._success(response=response, url=url)
        if status == HTTPStatus.UNAUTHORIZED:
            raise AuthError("authentication failed (401)", endpoint=endpoint, status_code=status)
        if status in _RATE_LIMITED_STATUSES:
            return self._rate_limited(response=response, endpoint=endpoint)
        if status == HTTPStatus.NOT_FOUND:
            raise NotFoundError("not visible to this token (404)", endpoint=endpoint, status_code=status)
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return _Retry(
                error=ServerError(f"server error {status}", endpoint=endpoint, status_code=status), delay_s=None
            )
        raise InvalidResponseError(
            f"unexpected status {status}: {_message(response)}", endpoint=endpoint, status_code=status
        )

    def _success(self, *, response: httpx.Response, url: str) -> _Page:
        endpoint = self._endpoint(url)
        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidResponseError("response body is not JSON", endpoint=endpoint) from exc
        next_url = response.links.get("next", {}).get("url")
        if next_url is not None and not next_url.startswith(f"{self._config.api_url}/"):
            raise InvalidResponseError("pagination link leaves the API host; refused", endpoint=endpoint)
        etag = response.headers.get("etag")
        if self._cache is not None and etag:
            entry = self._cache.put(url=url, etag=etag, body=body, next_url=next_url)
            return _Page(body=body, next_url=next_url, fetched_at=entry.fetched_at, from_cache=False)
        return _Page(body=body, next_url=next_url, fetched_at=self._clock(), from_cache=False)

    def _rate_limited(self, *, response: httpx.Response, endpoint: str) -> _Retry:
        status = response.status_code
        message = _message(response)
        retry_after = _float_header(response.headers, "retry-after")
        limits = self._config.rate_limit
        if retry_after is not None or _SECONDARY_MARKER in message.lower() or status == HTTPStatus.TOO_MANY_REQUESTS:
            wait = retry_after if (retry_after is not None and limits.honor_retry_after) else limits.secondary_backoff_s
            error = SecondaryRateLimitedError(f"secondary rate limit: {message}", endpoint=endpoint, status_code=status)
            if wait > limits.max_wait_s:
                raise error
            return _Retry(error=error, delay_s=wait)
        if response.headers.get("x-ratelimit-remaining") == "0":
            wait = (self._reset_epoch or self._clock()) - self._clock()
            if wait > limits.max_wait_s:
                raise RateLimitedError(
                    f"primary rate limit exhausted; reset in {wait:.0f}s > max_wait_s",
                    endpoint=endpoint,
                    status_code=status,
                    reset_epoch=self._reset_epoch,
                )
            self._remaining = None  # the retry delay below already waits for the reset
            return _Retry(
                error=RateLimitedError("primary rate limit exhausted", endpoint=endpoint, status_code=status),
                delay_s=max(wait, 0.0),
            )
        raise ForbiddenError(f"forbidden (403): {message}", endpoint=endpoint, status_code=status)

    async def _await_primary_budget(self, url: str) -> None:
        """Wait for the reset (bounded by ``max_wait_s``) once remaining <= ``min_remaining``."""
        limits = self._config.rate_limit
        if self._remaining is None or self._remaining > limits.min_remaining or self._reset_epoch is None:
            return
        wait = self._reset_epoch - self._clock()
        if wait > limits.max_wait_s:
            raise RateLimitedError(
                f"rate limit budget {self._remaining} <= {limits.min_remaining}; reset in {wait:.0f}s > max_wait_s",
                endpoint=self._endpoint(url),
                reset_epoch=self._reset_epoch,
            )
        if wait > 0:
            logger.warning("rate limit budget low (%d); waiting %.0fs for reset", self._remaining, wait)
            await self._sleep(wait)
        self._remaining = None

    def _record_rate_limit(self, headers: httpx.Headers) -> None:
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if remaining is not None and remaining.isdigit():
            self._remaining = int(remaining)
        if reset is not None and reset.isdigit():
            self._reset_epoch = float(reset)

    def _backoff(self, attempt: int) -> float:
        retries = self._config.retries
        ceiling = min(retries.backoff_max_s, retries.backoff_base_s * (2.0 ** (attempt - 1)))
        return ceiling / 2 + self._rng.uniform(0, ceiling / 2)

    def _endpoint(self, url: str) -> str:
        """Provenance label: the URL without the API host (never carries credentials)."""
        return url.removeprefix(self._config.api_url) or "/"


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _float_header(headers: httpx.Headers, name: str) -> float | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and isinstance(body.get("message"), str):
        return str(body["message"])[:200]
    return ""
