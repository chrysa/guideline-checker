"""Logging filter that scrubs GitHub tokens from every record before it is emitted."""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"
# Classic (ghp_/gho_/ghu_/ghs_/ghr_) and fine-grained (github_pat_) token shapes, plus any
# Authorization header value, are scrubbed even when the exact secret is unknown.
_TOKEN_SHAPES = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")
_AUTH_HEADER = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(bearer|token)?\s*[^\s'\",}]+")


class TokenRedactingFilter(logging.Filter):
    """Replace the configured secret and any token-shaped string in the rendered message."""

    def __init__(self, *, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = tuple(s for s in secrets if s)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        text = _TOKEN_SHAPES.sub(REDACTED, text)
        return _AUTH_HEADER.sub(rf"\1{REDACTED}", text)


# Logger filters only see records logged *on that logger*, not records propagating from
# children, so every module logger of the scanner is listed explicitly; root handlers get the
# filter too, which covers records from any other logger that reach them.
SCAN_LOGGERS = (
    "guideline_checker.fleet.scan",
    "guideline_checker.fleet.scan.github",
    "guideline_checker.fleet.scan.engine",
    "guideline_checker.fleet.scan.run_store",
    "httpx",
    "httpcore",
)


def _replace_filter(target: logging.Filterer, redactor: TokenRedactingFilter) -> None:
    for existing in list(target.filters):
        if isinstance(existing, TokenRedactingFilter):
            target.removeFilter(existing)
    target.addFilter(redactor)


def install_redaction(
    *, secrets: tuple[str, ...], logger_names: tuple[str, ...] = SCAN_LOGGERS
) -> TokenRedactingFilter:
    """Attach one redacting filter to each named logger and to every root-logger handler."""
    redactor = TokenRedactingFilter(secrets=secrets)
    for name in logger_names:
        _replace_filter(logging.getLogger(name), redactor)
    for handler in logging.getLogger().handlers:
        _replace_filter(handler, redactor)
    return redactor
