"""GitHub client resilience: pagination, typed errors, rate limits, retries, ETag cache, redaction."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from guideline_checker.fleet.scan.errors import (
    AuthError,
    ForbiddenError,
    InvalidResponseError,
    NetworkError,
    NotFoundError,
    RateLimitedError,
    SecondaryRateLimitedError,
    ServerError,
)
from guideline_checker.fleet.scan.github import GithubClient
from guideline_checker.fleet.scan.http_cache import EtagCache
from guideline_checker.fleet.scan.models import ErrorKind, GithubConfig
from guideline_checker.fleet.scan.redaction import REDACTED, TokenRedactingFilter

from .conftest import API

TOKEN = "ghp_" + "S3cr3tT0kenValue" * 3


class Script:
    """A transport replaying a scripted list of responses, recording each request."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _client(config: GithubConfig, script: Script, **kwargs) -> GithubClient:
    kwargs.setdefault("sleep", Sleeps())
    return GithubClient(config=config, token=kwargs.pop("token", None), transport=httpx.MockTransport(script), **kwargs)


def _cfg(base: GithubConfig, **updates) -> GithubConfig:
    return base.model_copy(update=updates)


def _run(coro):
    return asyncio.run(coro)


class TestPagination:
    def test_follows_link_header_across_pages(self, config) -> None:
        link = f'<{API}/repos/o/r/tags?page=2&per_page=2>; rel="next"'
        script = Script(
            httpx.Response(200, json=[1, 2], headers={"link": link}),
            httpx.Response(200, json=[3]),
        )

        async def go():
            async with _client(config.github, script) as gh:
                return await gh.get_paginated("/repos/o/r/tags")

        page = _run(go())
        assert page.items == [1, 2, 3]
        assert page.pages == 2
        assert page.truncated is False
        assert "per_page=2" in str(script.requests[0].url)
        assert page.endpoint == "/repos/o/r/tags?per_page=2"

    def test_max_pages_caps_and_flags_truncation(self, config) -> None:
        def page(n: int) -> httpx.Response:
            return httpx.Response(200, json=[n], headers={"link": f'<{API}/x?page={n + 1}>; rel="next"'})

        script = Script(page(1), page(2), page(3), page(4))

        async def go():
            async with _client(config.github, script) as gh:
                return await gh.get_paginated("/x")

        result = _run(go())
        assert result.items == [1, 2, 3]  # max_pages = 3 in the fixture config
        assert result.truncated is True
        assert len(script.requests) == 3

    def test_next_link_to_another_host_is_refused(self, config) -> None:
        script = Script(httpx.Response(200, json=[1], headers={"link": '<https://evil.test/x?page=2>; rel="next"'}))

        async def go():
            async with _client(config.github, script, token=TOKEN) as gh:
                await gh.get_paginated("/x")

        with pytest.raises(InvalidResponseError, match="leaves the API host"):
            _run(go())
        assert len(script.requests) == 1

    def test_non_list_page_is_invalid(self, config) -> None:
        script = Script(httpx.Response(200, json={"a": 1}))

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_paginated("/x")

        with pytest.raises(InvalidResponseError):
            _run(go())


class TestTypedErrors:
    @pytest.mark.parametrize(
        ("status", "body", "error", "kind"),
        [
            (404, {"message": "Not Found"}, NotFoundError, ErrorKind.NOT_FOUND),
            (403, {"message": "Resource not accessible by integration"}, ForbiddenError, ErrorKind.FORBIDDEN),
            (401, {"message": "Bad credentials"}, AuthError, ErrorKind.AUTH),
            (409, {"message": "Git Repository is empty."}, InvalidResponseError, ErrorKind.INVALID_RESPONSE),
        ],
    )
    def test_status_maps_to_typed_error(self, config, status, body, error, kind) -> None:
        script = Script(httpx.Response(status, json=body))

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_json("/repos/o/r")

        with pytest.raises(error) as info:
            _run(go())
        assert info.value.kind is kind
        assert info.value.endpoint == "/repos/o/r"
        assert len(script.requests) == 1  # never retried

    def test_non_json_body_is_invalid_response(self, config) -> None:
        script = Script(httpx.Response(200, text="<html>"))

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_json("/x")

        with pytest.raises(InvalidResponseError, match="not JSON"):
            _run(go())

    def test_relative_path_is_rejected(self, config) -> None:
        gh = _client(config.github, Script())
        with pytest.raises(ValueError, match="must start with"):
            gh.url(path="repos/o/r")
        _run(gh.aclose())


class TestRetries:
    def test_bounded_retries_on_502_then_server_error(self, config) -> None:
        sleeps = Sleeps()
        script = Script(*(httpx.Response(502) for _ in range(3)))

        async def go():
            async with _client(config.github, script, sleep=sleeps) as gh:
                await gh.get_json("/x")

        with pytest.raises(ServerError) as info:
            _run(go())
        assert info.value.kind is ErrorKind.SERVER_ERROR
        assert len(script.requests) == 3  # max_attempts
        assert len(sleeps.delays) == 2

    def test_recovers_after_transient_502(self, config) -> None:
        script = Script(httpx.Response(502), httpx.Response(200, json={"ok": True}))

        async def go():
            async with _client(config.github, script) as gh:
                return await gh.get_json("/x")

        assert _run(go()).data == {"ok": True}

    def test_network_errors_are_retried_then_typed(self, config) -> None:
        script = Script(
            httpx.ConnectError("boom"),
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("boom"),
        )

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_json("/x")

        with pytest.raises(NetworkError) as info:
            _run(go())
        assert info.value.kind is ErrorKind.NETWORK
        assert len(script.requests) == 3

    def test_backoff_is_exponential_capped_and_jittered(self, config) -> None:
        cfg = _cfg(
            config.github,
            retries=config.github.retries.model_copy(
                update={"max_attempts": 4, "backoff_base_s": 1.0, "backoff_max_s": 3.0}
            ),
        )
        sleeps = Sleeps()
        script = Script(*(httpx.Response(503) for _ in range(4)))

        async def go():
            async with _client(cfg, script, sleep=sleeps) as gh:
                await gh.get_json("/x")

        with pytest.raises(ServerError):
            _run(go())
        ceilings = [1.0, 2.0, 3.0]
        assert len(sleeps.delays) == 3
        for delay, ceiling in zip(sleeps.delays, ceilings, strict=True):
            assert ceiling / 2 <= delay <= ceiling


class TestPrimaryRateLimit:
    def _headers(self, remaining: int, reset: float) -> dict[str, str]:
        return {"x-ratelimit-remaining": str(remaining), "x-ratelimit-reset": str(int(reset))}

    def test_waits_for_reset_when_short(self, config) -> None:
        now = 1_000_000.0
        sleeps = Sleeps()
        script = Script(
            httpx.Response(200, json={}, headers=self._headers(remaining=3, reset=now + 30)),
            httpx.Response(200, json={}, headers=self._headers(remaining=4999, reset=now + 3600)),
        )

        async def go():
            async with _client(config.github, script, sleep=sleeps, clock=lambda: now) as gh:
                await gh.get_json("/a")
                await gh.get_json("/b")
                return gh.rate_remaining

        assert _run(go()) == 4999
        assert sleeps.delays == [30.0]

    def test_aborts_when_reset_beyond_max_wait(self, config) -> None:
        now = 1_000_000.0
        script = Script(httpx.Response(200, json={}, headers=self._headers(remaining=2, reset=now + 3600)))

        async def go():
            async with _client(config.github, script, clock=lambda: now) as gh:
                await gh.get_json("/a")
                await gh.get_json("/b")

        with pytest.raises(RateLimitedError) as info:
            _run(go())
        assert info.value.kind is ErrorKind.RATE_LIMITED
        assert info.value.reset_epoch == now + 3600
        assert len(script.requests) == 1  # second call never sent

    def test_exhausted_403_waits_then_succeeds(self, config) -> None:
        now = 1_000_000.0
        sleeps = Sleeps()
        script = Script(
            httpx.Response(403, json={"message": "API rate limit exceeded"}, headers=self._headers(0, now + 20)),
            httpx.Response(200, json={"ok": 1}, headers=self._headers(5000, now + 3600)),
        )

        async def go():
            async with _client(config.github, script, sleep=sleeps, clock=lambda: now) as gh:
                return await gh.get_json("/a")

        assert _run(go()).data == {"ok": 1}
        assert sleeps.delays == [20.0]

    def test_exhausted_403_beyond_max_wait_raises(self, config) -> None:
        now = 1_000_000.0
        script = Script(
            httpx.Response(403, json={"message": "API rate limit exceeded"}, headers=self._headers(0, now + 7200))
        )

        async def go():
            async with _client(config.github, script, clock=lambda: now) as gh:
                await gh.get_json("/a")

        with pytest.raises(RateLimitedError):
            _run(go())


class TestSecondaryRateLimit:
    def test_honours_retry_after(self, config) -> None:
        sleeps = Sleeps()
        script = Script(
            httpx.Response(
                403, json={"message": "You have exceeded a secondary rate limit"}, headers={"retry-after": "7"}
            ),
            httpx.Response(200, json={"ok": 1}),
        )

        async def go():
            async with _client(config.github, script, sleep=sleeps) as gh:
                return await gh.get_json("/a")

        assert _run(go()).data == {"ok": 1}
        assert sleeps.delays == [7.0]

    def test_message_without_retry_after_uses_secondary_backoff(self, config) -> None:
        sleeps = Sleeps()
        script = Script(
            httpx.Response(429, json={"message": "slow down"}),
            httpx.Response(200, json={}),
        )

        async def go():
            async with _client(config.github, script, sleep=sleeps) as gh:
                await gh.get_json("/a")

        _run(go())
        assert sleeps.delays == [1.0]  # secondary_backoff_s in the fixture config

    def test_persistent_secondary_limit_is_typed(self, config) -> None:
        script = Script(*(httpx.Response(403, json={"message": "secondary rate limit"}) for _ in range(3)))

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_json("/a")

        with pytest.raises(SecondaryRateLimitedError) as info:
            _run(go())
        assert info.value.kind is ErrorKind.SECONDARY_RATE_LIMITED

    def test_retry_after_beyond_max_wait_raises_immediately(self, config) -> None:
        script = Script(httpx.Response(429, json={}, headers={"retry-after": "3600"}))

        async def go():
            async with _client(config.github, script) as gh:
                await gh.get_json("/a")

        with pytest.raises(SecondaryRateLimitedError):
            _run(go())
        assert len(script.requests) == 1


class TestEtagCache:
    def _cfg(self, config, ttl: int) -> GithubConfig:
        return _cfg(config.github, cache=config.github.cache.model_copy(update={"enabled": True, "ttl_s": ttl}))

    def test_304_reuses_cached_body(self, config, tmp_path: Path) -> None:
        now = [100.0]
        script = Script(
            httpx.Response(200, json={"v": 1}, headers={"etag": '"abc"'}),
            httpx.Response(304),
        )
        cfg = self._cfg(config, ttl=60)

        async def go():
            async with _client(cfg, script, cache_root=tmp_path, clock=lambda: now[0]) as gh:
                first = await gh.get_json("/a")
                now[0] = 500.0  # past the 60s TTL: revalidate with If-None-Match
                second = await gh.get_json("/a")
                return first, second

        first, second = _run(go())
        assert first.data == second.data == {"v": 1}
        assert second.from_cache is True
        assert script.requests[1].headers["if-none-match"] == '"abc"'
        assert list((tmp_path / cfg.cache.dir).glob("*.json"))

    def test_fresh_entry_served_without_request(self, config, tmp_path: Path) -> None:
        script = Script(httpx.Response(200, json={"v": 2}, headers={"etag": '"e"'}))
        cfg = self._cfg(config, ttl=3600)

        async def go():
            async with _client(cfg, script, cache_root=tmp_path, clock=lambda: 10.0) as gh:
                await gh.get_json("/a")
                return await gh.get_json("/a")

        cached = _run(go())
        assert cached.data == {"v": 2}
        assert cached.from_cache is True
        assert len(script.requests) == 1

    def test_cache_files_never_contain_the_token(self, config, tmp_path: Path) -> None:
        script = Script(httpx.Response(200, json={"v": 3}, headers={"etag": '"e"'}))
        cfg = self._cfg(config, ttl=3600)

        async def go():
            async with _client(cfg, script, cache_root=tmp_path, token=TOKEN) as gh:
                await gh.get_json("/a")

        _run(go())
        for path in (tmp_path / cfg.cache.dir).glob("*.json"):
            assert TOKEN not in path.read_text(encoding="utf-8")
            assert path.name.removesuffix(".json").isalnum()  # hashed name, no traversal possible

    def test_poisoned_cached_next_link_is_refused(self, config, tmp_path: Path) -> None:
        cfg = self._cfg(config, ttl=3600)
        cache = EtagCache(directory=tmp_path / cfg.cache.dir, ttl_s=3600, clock=lambda: 10.0, token=TOKEN)
        first = f"{API}/x?per_page={cfg.pagination.per_page}"
        cache.put(url=first, etag='"e"', body=[1], next_url="https://evil.test/x?page=2")
        script = Script()

        async def go():
            async with _client(cfg, script, cache_root=tmp_path, clock=lambda: 10.0, token=TOKEN) as gh:
                await gh.get_paginated("/x")

        with pytest.raises(InvalidResponseError, match="leaves the API host"):
            _run(go())
        assert script.requests == []


class TestTokenHandling:
    def test_token_sent_as_bearer_header_only(self, config) -> None:
        script = Script(httpx.Response(200, json={}))

        async def go():
            async with _client(config.github, script, token=TOKEN) as gh:
                await gh.get_json("/a")

        _run(go())
        request = script.requests[0]
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)

    def test_token_is_redacted_from_logs(self, config, caplog) -> None:
        script = Script(httpx.Response(502), httpx.Response(200, json={}))
        caplog.set_level(logging.DEBUG)

        async def go():
            async with _client(config.github, script, token=TOKEN) as gh:
                await gh.get_json("/a")
                logging.getLogger("guideline_checker.fleet.scan.github").warning("leak attempt %s", TOKEN)
                logging.getLogger("guideline_checker.fleet.scan.engine").error("header Authorization: Bearer %s", TOKEN)

        _run(go())
        rendered = "\n".join(caplog.messages)
        assert TOKEN not in rendered
        assert REDACTED in rendered

    def test_filter_scrubs_token_shapes_and_headers(self) -> None:
        redactor = TokenRedactingFilter(secrets=())
        text = redactor.redact("t=github_pat_" + "A" * 30 + " Authorization: token abcdef ghs_" + "b" * 30)
        assert "github_pat_" not in text
        assert "abcdef" not in text
        assert "ghs_" not in text
