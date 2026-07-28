"""The shipped HTML referential, proven against real markup.

A rule that loads but never fires is `dead` in this tool's own vocabulary — and
one of these three was, on the first attempt, because its character class used
PCRE `\\x{...}` syntax that Python rejects. These tests exist so that cannot ship
again: each rule is proven on markup that must trigger it, and on markup that
must not.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guideline_checker.checker import run_checks

REFERENTIAL = Path(__file__).resolve().parents[1] / "guidelines"

OFFENDING = """<html><head><meta name="viewport" content="width=device-width"></head><body>
<button onclick="go()">Go</button>
<img src="a.png">
<button>◐</button>
</body></html>
"""

CLEAN = """<html><head><meta name="viewport" content="width=device-width"></head><body>
<button data-action="go">Go</button>
<img src="a.png" alt="">
<button><svg class="icon"><use href="#i"/></svg></button>
</body></html>
"""


def _rules_fired(root: Path) -> set[str]:
    """Rule texts that produced at least one violation on a page under ``root``."""
    fired: set[str] = set()
    for result in run_checks(root=root):
        for violation in result.violations:
            if violation.file.suffix in {".html", ".htm"}:
                fired.add(violation.rule)
    return fired


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal project carrying the shipped referential.

    The referential is read from the *scanned root*, not from the installed
    package — a fixture without it silently checks nothing.
    """
    shutil.copytree(REFERENTIAL, tmp_path / "guidelines")
    return tmp_path


def test_an_inline_event_handler_is_flagged(project: Path) -> None:
    (project / "page.html").write_text(OFFENDING, encoding="utf-8")
    assert any("event delegation" in rule for rule in _rules_fired(project))


def test_an_image_without_alt_is_flagged(project: Path) -> None:
    (project / "page.html").write_text(OFFENDING, encoding="utf-8")
    assert any("alt attribute" in rule for rule in _rules_fired(project))


def test_an_emoji_used_as_a_control_icon_is_flagged(project: Path) -> None:
    (project / "page.html").write_text(OFFENDING, encoding="utf-8")
    assert any("vector icon" in rule for rule in _rules_fired(project))


def test_correct_markup_triggers_nothing(project: Path) -> None:
    """Delegation, an explicit empty alt and an inline SVG must all pass."""
    (project / "page.html").write_text(CLEAN, encoding="utf-8")
    assert _rules_fired(project) == set()


def test_a_decorative_image_declares_an_empty_alt_rather_than_omitting_it(
    project: Path,
) -> None:
    (project / "page.html").write_text('<img src="a.png" alt="">\n', encoding="utf-8")
    assert not any("alt attribute" in rule for rule in _rules_fired(project))


def test_every_html_rule_regex_compiles() -> None:
    """A PCRE-flavoured class loads fine and then never matches — the dead-rule trap."""
    import re

    import yaml

    document = yaml.safe_load((REFERENTIAL / "languages" / "html.yml").read_text(encoding="utf-8"))
    patterns = [
        pattern
        for rule in document["rules"]
        for pattern in rule.get("detect", {}).get("forbid_regex", [])
    ]
    assert patterns
    for pattern in patterns:
        re.compile(pattern)
