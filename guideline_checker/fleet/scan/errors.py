"""Typed errors raised by the fleet scanner.

Every GitHub failure maps to exactly one :class:`ErrorKind`, so a collection error is
always reported with a machine-readable kind — never as a bare message or a boolean.
"""

from __future__ import annotations

from guideline_checker.fleet.scan.models import ErrorKind


class ScanError(Exception):
    """Base class of every error the scanner raises on purpose."""


class ConfigValidationError(ScanError):
    """One or more configuration problems; all of them are aggregated in ``problems``."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("invalid fleet-scan configuration:\n" + "\n".join(f"  - {p}" for p in problems))


class ResumeRefusedError(ScanError):
    """A resume was refused (unknown run, or a config fingerprint mismatch without force)."""


class ReporterDisabledError(ScanError):
    """A reporter that is a disabled stub (Notion) was asked to run."""


class GithubError(ScanError):
    """A GitHub API call failed. ``kind`` is the typed classification reported downstream."""

    kind: ErrorKind = ErrorKind.INVALID_RESPONSE

    def __init__(self, message: str, *, endpoint: str | None = None, status_code: int | None = None) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(GithubError):
    """HTTP 404 — the resource is not visible to this token (never read as "deleted")."""

    kind = ErrorKind.NOT_FOUND


class ForbiddenError(GithubError):
    """HTTP 403 that is not a rate limit — the token lacks access."""

    kind = ErrorKind.FORBIDDEN


class AuthError(GithubError):
    """HTTP 401 — the token is missing, invalid or expired."""

    kind = ErrorKind.AUTH


class RateLimitedError(GithubError):
    """Primary rate limit exhausted and the reset is further away than ``max_wait_s``."""

    kind = ErrorKind.RATE_LIMITED

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        status_code: int | None = None,
        reset_epoch: float | None = None,
    ) -> None:
        self.reset_epoch = reset_epoch
        super().__init__(message, endpoint=endpoint, status_code=status_code)


class SecondaryRateLimitedError(GithubError):
    """Secondary (abuse) rate limit still hit after the bounded retries."""

    kind = ErrorKind.SECONDARY_RATE_LIMITED


class ServerError(GithubError):
    """HTTP 5xx still failing after the bounded retries."""

    kind = ErrorKind.SERVER_ERROR


class NetworkError(GithubError):
    """Transport failure (DNS, connect, read timeout) still failing after the bounded retries."""

    kind = ErrorKind.NETWORK


class GithubConfigError(GithubError):
    """The client cannot run as configured (e.g. a required token is absent)."""

    kind = ErrorKind.CONFIG


class InvalidResponseError(GithubError):
    """Unexpected status (409, 422, ...) or a body that is not the JSON shape expected."""

    kind = ErrorKind.INVALID_RESPONSE
