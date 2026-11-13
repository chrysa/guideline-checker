"""Multi-mode authentication for guideline-checker web API.

Select the active mode via the ``AUTH_MODE`` environment variable.
Set ``AUTH_ENABLED=false`` to disable all authentication (equivalent to
``AUTH_MODE=disabled``).

Supported modes
---------------
disabled
    No authentication required. Only for local dev or trusted networks.
api_key  *(default)*
    ``X-Api-Key`` request header checked against the ``API_KEY`` env var.
    If ``API_KEY`` is unset the request is refused (fail closed, 500).
local
    HTTP Basic Auth.  Credentials checked against ``LOCAL_USERNAME`` /
    ``LOCAL_PASSWORD`` env vars.  If either is unset the request is refused (fail closed, 500).
ldap
    HTTP Basic Auth forwarded to an LDAP server via a simple bind.
    Required env vars: ``LDAP_URL``, ``LDAP_USER_DN_TEMPLATE``.
    Optional: ``LDAP_USER_DN_TEMPLATE`` (default: ``uid={username},dc=example,dc=com``).
oidc
    Bearer JWT validated against an OIDC provider JWKS endpoint.
    Required env var: ``OIDC_ISSUER``.
    Optional: ``OIDC_AUDIENCE``, ``OIDC_JWKS_URI`` (auto-discovered from issuer).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from enum import StrEnum
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

# ── Auth mode ──────────────────────────────────────────────────────────────────


class MissingAuthDependencyError(RuntimeError):
    """An optional auth-backend dependency (LDAP, OIDC) is not installed."""


class AuthMode(StrEnum):
    """Supported authentication modes."""

    DISABLED = "disabled"
    API_KEY = "api_key"
    LOCAL = "local"
    LDAP = "ldap"
    OIDC = "oidc"


def _resolve_mode() -> AuthMode:
    """Return the active auth mode from environment variables."""
    if os.environ.get("AUTH_ENABLED", "true").lower() == "false":
        return AuthMode.DISABLED
    raw = os.environ.get("AUTH_MODE", "api_key").lower()
    try:
        return AuthMode(raw)
    except ValueError as err:
        valid = ", ".join(m.value for m in AuthMode)
        msg = f"Unknown AUTH_MODE={raw!r}. Valid values: {valid}"
        raise ValueError(msg) from err


# ── Security scheme instances ──────────────────────────────────────────────────

_api_key_hdr = APIKeyHeader(name="X-Api-Key", auto_error=False)
_http_basic = HTTPBasic(auto_error=False)
_bearer = HTTPBearer(auto_error=False)


# ── Per-mode verifiers ─────────────────────────────────────────────────────────


def _check_api_key(key: str | None) -> None:
    expected = os.environ.get("API_KEY", "")
    if not expected:
        # Fail closed: api_key mode is selected but no key is configured.
        # Never silently serve open access — refuse until it is configured.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is misconfigured: API_KEY is not set",
        )
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )


def _check_local(creds: HTTPBasicCredentials | None) -> None:
    username = os.environ.get("LOCAL_USERNAME", "")
    password = os.environ.get("LOCAL_PASSWORD", "")
    if not username or not password:
        # Fail closed: local mode is selected but credentials are incomplete.
        # A half-configured credential (only one of the two set) must not open access.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication is misconfigured: LOCAL_USERNAME/LOCAL_PASSWORD are not both set",
        )
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )
    ok = secrets.compare_digest(creds.username, username) and secrets.compare_digest(creds.password, password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def _check_ldap(creds: HTTPBasicCredentials | None) -> None:
    try:
        from ldap3 import ALL, SIMPLE, Connection, Server
    except ImportError as exc:
        raise MissingAuthDependencyError("ldap3 is required for LDAP auth. Install with: pip install ldap3") from exc

    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    url = os.environ.get("LDAP_URL", "ldap://localhost")
    dn_tpl = os.environ.get("LDAP_USER_DN_TEMPLATE", "uid={username},dc=example,dc=com")
    user_dn = dn_tpl.format(username=creds.username)

    server = Server(url, get_info=ALL)
    conn = Connection(server, user=user_dn, password=creds.password, authentication=SIMPLE)
    if not conn.bind():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid LDAP credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    conn.unbind()


# JWKS in-memory cache: {uri: (payload, fetched_at_monotonic)}
_jwks_cache: dict[str, tuple[dict[str, Any], float]] = {}
_JWKS_TTL = 3600.0  # seconds


def _fetch_jwks(uri: str) -> dict[str, Any]:
    import httpx

    cached = _jwks_cache.get(uri)
    if cached and (time.monotonic() - cached[1]) < _JWKS_TTL:
        return cached[0]
    resp = httpx.get(uri, timeout=10)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    _jwks_cache[uri] = (data, time.monotonic())
    return data


def _check_oidc(bearer: HTTPAuthorizationCredentials | None) -> None:
    try:
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey as _RSAPublicKey
    except ImportError as exc:
        raise MissingAuthDependencyError("PyJWT is required for OIDC auth. Install with: pip install PyJWT") from exc

    if bearer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    issuer = os.environ.get("OIDC_ISSUER", "")
    audience = os.environ.get("OIDC_AUDIENCE", "") or None
    if not issuer:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OIDC_ISSUER is not configured",
        )

    jwks_uri = os.environ.get("OIDC_JWKS_URI", "") or f"{issuer.rstrip('/')}/.well-known/jwks.json"
    jwks = _fetch_jwks(jwks_uri)

    try:
        header = pyjwt.get_unverified_header(bearer.credentials)
        kid = header.get("kid")
        public_key: _RSAPublicKey | None = None
        for k in jwks.get("keys", []):
            if kid is None or k.get("kid") == kid:
                raw_key = pyjwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(k))
                if not isinstance(raw_key, _RSAPublicKey):
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="JWKS key is not an RSA public key",
                    )
                public_key = raw_key
                break
        if public_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No matching signing key found in JWKS",
            )
        decode_kw: dict[str, Any] = {"algorithms": ["RS256"]}
        if audience:
            decode_kw["audience"] = audience
        pyjwt.decode(bearer.credentials, public_key, **decode_kw)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


# ── Main dependency ────────────────────────────────────────────────────────────


def require_auth(
    api_key: Annotated[str | None, Security(_api_key_hdr)] = None,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_http_basic)] = None,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> None:
    """Universal auth dependency — select active mode with AUTH_MODE env var.

    Modes: disabled | api_key (default) | local | ldap | oidc
    """
    mode = _resolve_mode()
    if mode == AuthMode.DISABLED:
        return
    if mode == AuthMode.API_KEY:
        _check_api_key(api_key)
    elif mode == AuthMode.LOCAL:
        _check_local(credentials)
    elif mode == AuthMode.LDAP:
        _check_ldap(credentials)
    elif mode == AuthMode.OIDC:
        _check_oidc(bearer)


AuthDep = Annotated[None, Depends(require_auth)]
