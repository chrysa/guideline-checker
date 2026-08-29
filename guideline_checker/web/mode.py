"""Runtime mode for the guideline-checker web app: viewer vs workshop.

The dashboard can run in two postures, selected by the ``GC_MODE`` env var:

viewer
    Read-only. Every state-changing / disk-writing route is refused (403).
    Intended for a container that bind-mounts the scanned tree ``:ro`` and is
    exposed more widely — it can never scan, propose, persist, or arm a rule.
workshop  *(default)*
    Full read-write. The propose → prove → persist loop is available.

The mode is a *defence-in-depth* boundary on top of authentication: a viewer
deployment refuses mutations even for an authenticated caller, so a read-only
surface cannot be turned into a writer by leaking a credential.
"""

from __future__ import annotations

import os
from enum import StrEnum

from fastapi import HTTPException, status


class AppMode(StrEnum):
    """Supported runtime modes."""

    VIEWER = "viewer"
    WORKSHOP = "workshop"


def resolve_mode() -> AppMode:
    """Return the active mode from ``GC_MODE`` (default: workshop)."""
    raw = os.environ.get("GC_MODE", "workshop").lower()
    try:
        return AppMode(raw)
    except ValueError as err:
        valid = ", ".join(m.value for m in AppMode)
        msg = f"Unknown GC_MODE={raw!r}. Valid values: {valid}"
        raise ValueError(msg) from err


def require_workshop() -> None:
    """FastAPI dependency: refuse the request when running in viewer mode."""
    if resolve_mode() == AppMode.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is disabled in read-only viewer mode (GC_MODE=viewer)",
        )
