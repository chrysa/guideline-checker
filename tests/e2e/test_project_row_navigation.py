"""The project row must behave as a link, not as a div pretending to be one.

`CLAUDE.md` forbids a `<div>` wired as a control and requires navigation to be
URL-addressable. Those are claims about what a browser does with the markup, so
only a browser can settle them — the unit suite can assert the HTML is emitted,
never that ⌘-click opens a tab.
"""

from __future__ import annotations

from playwright.sync_api import Page

# A hash route resolves in milliseconds. A long wait here only means a regression
# takes minutes to report what it could report in seconds.
ROUTE_TIMEOUT_MS = 5_000


def test_a_project_row_is_a_real_anchor(fleet_view: Page) -> None:
    """A `<div role="button">` would satisfy a screen reader and nothing else."""
    row = fleet_view.locator("#rules .row").first

    assert row.evaluate("el => el.tagName") == "A"
    assert (row.get_attribute("href") or "").startswith("#p=")


def test_clicking_a_row_puts_the_project_in_the_url(fleet_view: Page) -> None:
    """The view must be shareable: the address has to name what is on screen."""
    row = fleet_view.locator("#rules .row").first
    name = row.get_attribute("href") or ""

    row.click()

    fleet_view.wait_for_function("() => location.hash.startsWith('#p=')", timeout=ROUTE_TIMEOUT_MS)
    assert fleet_view.url.endswith(name)


def test_the_keyboard_opens_a_row_without_a_keydown_handler(fleet_view: Page) -> None:
    """Enter activates an anchor natively — the hand-rolled handler is gone.

    This is the assertion that would have caught a regression back to a `<div>`:
    a div with `tabindex` still focuses, but Enter does nothing without script.
    """
    row = fleet_view.locator("#rules .row").first
    row.focus()

    row.press("Enter")

    assert "#p=" in fleet_view.url


def test_a_row_can_be_opened_in_a_new_tab(fleet_view: Page) -> None:
    """⌘-click / middle-click is the point of using an anchor at all."""
    row = fleet_view.locator("#rules .row").first
    href = row.get_attribute("href") or ""

    with fleet_view.context.expect_page(timeout=ROUTE_TIMEOUT_MS) as new_tab:
        row.click(modifiers=["ControlOrMeta"])

    opened = new_tab.value
    # A hash link opens the tab first and navigates a moment later, so the page
    # is still about:blank when expect_page() returns.
    opened.wait_for_url(f"**{href}", timeout=ROUTE_TIMEOUT_MS)
    assert opened.url.endswith(href)
    opened.close()


def test_back_returns_to_the_fleet_view(fleet_view: Page) -> None:
    """Navigating must leave a history entry rather than swallow the Back button."""
    fleet_view.locator("#rules .row").first.click()
    fleet_view.wait_for_function("() => location.hash.startsWith('#p=')", timeout=ROUTE_TIMEOUT_MS)

    fleet_view.go_back()

    fleet_view.wait_for_function("() => location.hash === '#all'", timeout=ROUTE_TIMEOUT_MS)
    assert fleet_view.url.endswith("#all")
