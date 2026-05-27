"""Playwright E2E test configuration for the guideline-checker web dashboard."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page

# The live server must be running before the E2E suite is executed.
# Start it with:  SCAN_ROOT=<path> uvicorn guideline_checker.web.app:app --port 8080
BASE_URL = "http://127.0.0.1:8080"

# How long (ms) to wait for the initial background scan to finish.
# padam-av takes ~13s; we allow up to 60s to be safe.
SCAN_TIMEOUT_MS = 60_000


@pytest.fixture(scope="session")
def base_url() -> str:  # type: ignore[override]
    """Base URL for all E2E tests — overrides pytest-playwright's default."""
    return BASE_URL


@pytest.fixture()
def page_ready(page: Page) -> Page:
    """Navigate to the dashboard and wait for the initial scan to finish.

    Returns the Page once the violations panel is visible (loading spinner gone).
    """
    page.goto(BASE_URL)
    # Wait for the loading spinner to disappear — means scan completed
    page.wait_for_selector("#loading.hidden", timeout=SCAN_TIMEOUT_MS)
    # Tab nav should now be visible
    page.wait_for_selector("#tab-nav:not(.hidden)", timeout=5_000)
    return page
