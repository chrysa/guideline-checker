"""Playwright E2E configuration — the suite starts and stops its own server.

The previous version required a server already running on port 8080 and waited on
`#loading` and `#tab-nav`, neither of which has existed since the interface was
rebuilt. Because `addopts` carries `--ignore=tests/e2e`, nothing ran it and nobody
found out. A suite that cannot run is worse than none: it reads as coverage.

The workspace is two synthetic projects rather than the real one next door, so the
fleet view has a deterministic number of rows and the scan finishes in a moment.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT_S = 30
SCAN_TIMEOUT_MS = 60_000

PROJECT_NAMES = ("alpha-service", "beta-service")


def _free_port() -> int:
    """Ask the OS for a port nobody is using, so parallel runs cannot collide."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A workspace of two minimal projects.

    ``discover_projects`` accepts a directory carrying a ``.git`` entry and a rule
    source, so a plain file named ``.git`` is enough — no repository needed.
    """
    root = tmp_path_factory.mktemp("workspace")
    for name in PROJECT_NAMES:
        project = root / name
        (project / "src").mkdir(parents=True)
        (project / ".git").write_text("gitdir: /dev/null\n", encoding="utf-8")
        (project / "CLAUDE.md").write_text("# Rules\n\n- No print() calls\n", encoding="utf-8")
        (project / "src" / "app.py").write_text('print("hello")\n', encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def live_server(workspace: Path) -> Iterator[str]:
    """Run the workshop against ``workspace`` and yield its base URL."""
    port = _free_port()
    env = {
        **os.environ,
        "AUTH_MODE": "disabled",
        "GC_WORKSPACE": str(workspace),
        "SCAN_ROOT": str(workspace / PROJECT_NAMES[0]),
        "GC_CLAUDE": "0",  # the workshop auto-enables the LLM when the CLI is on PATH
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "guideline_checker.web.app:app", "--port", str(port)],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_until_healthy(url, process)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def _wait_until_healthy(url: str, process: subprocess.Popen[bytes]) -> None:
    """Poll ``/health`` until it answers, failing loudly if the server died first."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode(errors="replace") if process.stdout else ""
            pytest.fail(f"the workshop exited before answering /health:\n{output}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.2)
    process.terminate()
    pytest.fail(f"the workshop did not answer /health within {STARTUP_TIMEOUT_S}s")


@pytest.fixture(scope="session")
def base_url(live_server: str) -> str:
    """Base URL for pytest-playwright, pointing at the server this suite started."""
    return live_server


@pytest.fixture
def fleet_view(page: Page, base_url: str) -> Page:
    """Open the all-projects view and wait for its rows.

    Project rows live only in that view, and the scan runs in the background, so
    waiting on the first row is what tells us the page is ready.

    The wait is on ``.row``, not ``a.row``: were the row to regress to a ``<div>``,
    an anchor-only wait would make every test time out on the fixture instead of
    failing on its own assertion — five minutes of red saying nothing.
    """
    page.goto(f"{base_url}/#all")
    page.wait_for_selector("#rules .row", timeout=SCAN_TIMEOUT_MS)
    return page
