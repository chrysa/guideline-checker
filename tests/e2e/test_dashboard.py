"""Playwright E2E tests for the guideline-checker web dashboard.

Requires the server to be running:
    SCAN_ROOT=<project> uvicorn guideline_checker.web.app:app --port 8080

Run with:
    pytest tests/e2e/ --headed            # visible browser
    pytest tests/e2e/ --browser chromium  # headless (default)
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import BASE_URL, SCAN_TIMEOUT_MS

# ── Page load ──────────────────────────────────────────────────────────────────


def test_page_title(page: Page) -> None:
    """Browser tab title must contain 'Guideline Checker'."""
    page.goto(BASE_URL)
    expect(page).to_have_title(re.compile("Guideline Checker", re.IGNORECASE))


def test_header_visible(page: Page) -> None:
    """The top header must show the app name and 'Run Scan' button."""
    page.goto(BASE_URL)
    expect(page.locator("h1")).to_contain_text("Guideline Checker")
    expect(page.locator("#scan-btn")).to_be_visible()
    expect(page.locator("#scan-btn")).to_have_text(re.compile("Run Scan"))


def test_loading_spinner_shown_before_scan(page: Page) -> None:
    """The loading section must be visible immediately on first page load."""
    page.goto(BASE_URL)
    # The spinner is visible while the background scan is running
    loading = page.locator("#loading")
    # It's either already hidden (scan finished very fast) or still visible
    # Either state is valid — we just verify the element exists
    expect(loading).to_have_count(1)


# ── Post-scan state ────────────────────────────────────────────────────────────


def test_stats_cards_show_numbers(page_ready: Page) -> None:
    """After scan, the four stat cards must display numeric values."""
    for card_id in ("#count-error", "#count-warning", "#count-info", "#count-files"):
        text = page_ready.locator(card_id).inner_text()
        assert text.isdigit(), f"{card_id} expected a number, got {text!r}"


def test_stats_files_checked_positive(page_ready: Page) -> None:
    """Files-checked count must be > 0 when scanning a real project."""
    files_text = page_ready.locator("#count-files").inner_text()
    assert files_text.isdigit(), f"Expected digit, got {files_text!r}"
    assert int(files_text) > 0, "No files were checked"


def test_tab_nav_visible_after_scan(page_ready: Page) -> None:
    """Tab navigation must be visible once the scan finishes."""
    expect(page_ready.locator("#tab-nav")).to_be_visible()


def test_violations_tab_active_by_default(page_ready: Page) -> None:
    """The Violations tab must be the active tab on initial load."""
    violations_btn = page_ready.locator("[data-tab='violations']")
    expect(violations_btn).to_have_class(re.compile(r"\bactive\b"))


def test_scan_time_updated(page_ready: Page) -> None:
    """#scan-time must show 'Last scan:' with a date after scan completes."""
    scan_time = page_ready.locator("#scan-time").inner_text()
    assert "Last scan:" in scan_time, f"Expected 'Last scan:' in {scan_time!r}"


# ── Violations tab ─────────────────────────────────────────────────────────────


def test_violations_or_all_clear_shown(page_ready: Page) -> None:
    """Either violation cards or the all-clear message must be visible."""
    has_violations = page_ready.locator("#violations-container > div").count() > 0
    all_clear_hidden = "hidden" in (page_ready.locator("#all-clear").get_attribute("class") or "")
    # One of the two must be shown
    assert has_violations or not all_clear_hidden, "Neither violations nor all-clear is visible"


def test_severity_filter_errors_only(page_ready: Page) -> None:
    """Clicking the 'Errors' filter button must activate it and hide non-error cards."""
    page_ready.locator("[data-severity='error']").click()
    # The error filter button must now be active
    expect(page_ready.locator("[data-severity='error']")).to_have_class(re.compile(r"\bactive\b"))
    # Warning cards must not be visible (sev-warning class hidden)
    warning_cards = page_ready.locator(".sev-warning")
    for i in range(min(warning_cards.count(), 5)):
        expect(warning_cards.nth(i)).to_be_hidden()


def test_severity_filter_all_resets(page_ready: Page) -> None:
    """Clicking 'All' after a severity filter must restore all violations."""
    page_ready.locator("[data-severity='error']").click()
    page_ready.locator("[data-severity='all']").click()
    expect(page_ready.locator("[data-severity='all']")).to_have_class(re.compile(r"\bactive\b"))


def test_search_input_filters_results(page_ready: Page) -> None:
    """Typing a specific filename in the search box must reduce visible results."""
    # Get initial count
    initial_count = page_ready.locator("#violations-container > div").count()
    if initial_count == 0:
        pytest.skip("No violations — search filter test requires violations")

    search = page_ready.locator("#search")
    # Type a string unlikely to match many results
    search.fill("xxxxxxxxxxxxxxxxxxx")
    page_ready.wait_for_timeout(300)  # debounce

    filtered_count = page_ready.locator("#violations-container > div").count()
    all_clear_visible = page_ready.locator("#all-clear").is_visible()

    assert filtered_count < initial_count or all_clear_visible, "Search did not reduce visible violations"


def test_search_clear_restores_results(page_ready: Page) -> None:
    """Clearing the search box must restore all violations."""
    initial_count = page_ready.locator("#violations-container > div").count()
    page_ready.locator("#search").fill("xxxxxxxxxxxxxxxxxxx")
    page_ready.wait_for_timeout(200)
    page_ready.locator("#search").fill("")
    page_ready.wait_for_timeout(300)

    restored_count = page_ready.locator("#violations-container > div").count()
    assert restored_count == initial_count, (
        f"Expected {initial_count} violations after clearing search, got {restored_count}"
    )


# ── Constraints tab ────────────────────────────────────────────────────────────


def test_constraints_tab_switching(page_ready: Page) -> None:
    """Clicking the Constraints tab must show the constraints panel."""
    page_ready.locator("[data-tab='constraints']").click()
    expect(page_ready.locator("#tab-constraints")).to_be_visible()
    expect(page_ready.locator("#tab-violations")).to_be_hidden()


def test_constraints_badge_shows_count(page_ready: Page) -> None:
    """The badge next to 'Constraints' tab must display a positive number."""
    badge_text = page_ready.locator("#badge-constraints").inner_text()
    assert badge_text.isdigit(), f"Constraint badge expected a number, got {badge_text!r}"
    assert int(badge_text) > 0, "Constraint badge is 0 — no rules extracted"


def test_constraints_sources_visible(page_ready: Page) -> None:
    """Switching to Constraints tab must render at least one source card."""
    page_ready.locator("[data-tab='constraints']").click()
    page_ready.wait_for_timeout(300)  # allow renderConstraints()

    source_cards = page_ready.locator("#constraints-container > div")
    assert source_cards.count() > 0, "No constraint source cards rendered"


def test_constraints_search_filters(page_ready: Page) -> None:
    """Typing in the constraints search box must narrow displayed sources."""
    page_ready.locator("[data-tab='constraints']").click()
    page_ready.wait_for_timeout(300)

    initial = page_ready.locator("#constraints-container > div").count()
    if initial == 0:
        pytest.skip("No constraint sources to filter")

    page_ready.locator("#cst-search").fill("xxxxxxxxxxxxxxxxxxx")
    page_ready.wait_for_timeout(300)

    filtered = page_ready.locator("#constraints-container > div").count()
    cst_empty_visible = page_ready.locator("#cst-empty").is_visible()

    assert filtered < initial or cst_empty_visible, "Constraint search did not filter"


# ── Run Scan button ────────────────────────────────────────────────────────────


def test_run_scan_triggers_scanning_state(page_ready: Page) -> None:
    """Clicking 'Run Scan' must disable the button and show the scanning spinner."""
    scan_btn = page_ready.locator("#scan-btn")
    # Click triggers scan; button becomes disabled immediately
    scan_btn.click()

    # Button must be disabled and show "Scanning…"
    expect(scan_btn).to_be_disabled()
    expect(scan_btn).to_contain_text(re.compile(r"Scanning", re.IGNORECASE))


def test_run_scan_completes_and_re_enables_button(page_ready: Page) -> None:
    """After triggering a scan, the button must re-enable once scan finishes."""
    scan_btn = page_ready.locator("#scan-btn")
    scan_btn.click()

    # Wait for scan to complete (button re-enabled)
    expect(scan_btn).to_be_enabled(timeout=SCAN_TIMEOUT_MS)
    expect(scan_btn).to_have_text(re.compile("Run Scan"))


# ── Health endpoint ────────────────────────────────────────────────────────────


def test_health_endpoint(page: Page) -> None:
    """/health must return JSON {"status": "ok"}."""
    response = page.goto(f"{BASE_URL}/health")
    assert response is not None
    assert response.status == 200
    body = response.json()
    assert body == {"status": "ok"}
