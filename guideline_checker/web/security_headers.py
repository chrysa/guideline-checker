"""Security response headers for the guideline-checker web app.

Adds a Content-Security-Policy and hardening headers to every response. The
single-page dashboard ships its script and styles inline, so ``script-src`` and
``style-src`` must allow ``'unsafe-inline'`` — the CSP therefore cannot be the
primary XSS defence (that is output escaping in ``static/index.html``), but it
still blocks object/base/frame-embedding vectors and stops MIME sniffing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Inline script/style are required by the bundled single-page UI (ADR D-0011);
# everything else is locked down.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach the security headers to every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        return response
