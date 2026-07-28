"""The ``detect.cross_reference`` mechanism: a claim here, its definition there.

Every other mechanism reads one file in isolation, so a whole family of defects
was invisible — documentation citing a command nobody defined, a CSS variable used
but never declared. Neither file is wrong on its own; the defect lives in the gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.checker import run_checks
from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines
from guideline_checker.kinds import CheckKind, kind_of_detector
from guideline_checker.loader import CrossReference, RuleDetector

_CATEGORIES = "categories:\n  - id: correctness\n    description: x\n"


def _violations(root: Path, suffix: str) -> list[str]:
    return [
        v.line_content
        for result in run_checks(root, all_sources=True)
        for v in result.violations
        if v.file.suffix == suffix
    ]


# ─── the mechanism itself ─────────────────────────────────────────────────────


def _referential(root: Path, rule_yaml: str, *, glob: str, name: str) -> None:
    (root / "guidelines" / "languages").mkdir(parents=True, exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")
    (root / "guidelines" / "languages" / name).write_text(
        f'language_target: x\napply_to_glob: "{glob}"\nrules:\n{rule_yaml}', encoding="utf-8"
    )


def test_self_reference_resolves_within_the_citing_file(tmp_path: Path) -> None:
    """``define_in: "@self"`` covers the CSS-variable case: used but never declared."""
    _referential(
        tmp_path,
        "  - id: css-var-declared\n    category: correctness\n    severity: warning\n"
        '    rule: "Declare every custom property you use"\n'
        "    detect:\n      cross_reference:\n"
        "        cite: 'var\\(--([\\w-]+)\\)'\n"
        '        define_in: "@self"\n'
        "        define_as: '--{name}\\s*:'\n",
        glob="**/*.css",
        name="css.yml",
    )
    (tmp_path / "declared.css").write_text(":root{--ink:#000}\na{color:var(--ink)}\n", encoding="utf-8")
    (tmp_path / "orphan.css").write_text("a{color:var(--ghost)}\n", encoding="utf-8")

    found = _violations(tmp_path, ".css")

    assert len(found) == 1
    assert "ghost" in found[0]


def test_an_unreadable_definition_file_reports_nothing(tmp_path: Path) -> None:
    """A missing target file is a different defect, and not this rule's to invent.

    Reporting every citation as unresolved when the Makefile is simply absent
    would bury the real finding under one violation per mention.
    """
    _referential(
        tmp_path,
        "  - id: md-target\n    category: correctness\n    severity: warning\n"
        '    rule: "Documented targets exist"\n'
        "    detect:\n      cross_reference:\n"
        "        cite: '`make ([a-z-]+)`'\n        define_in: Makefile\n        define_as: '^{name}:'\n",
        glob="**/*.md",
        name="md.yml",
    )
    (tmp_path / "README.md").write_text("Run `make lint` and `make test`.\n", encoding="utf-8")

    assert _violations(tmp_path, ".md") == []  # no Makefile at all


def test_a_captured_name_is_escaped_before_lookup(tmp_path: Path) -> None:
    """A capture carrying regex metacharacters must be matched literally."""
    _referential(
        tmp_path,
        "  - id: dotted\n    category: correctness\n    severity: warning\n"
        '    rule: "Cited keys exist"\n'
        "    detect:\n      cross_reference:\n"
        "        cite: '\\[\\[([\\w.]+)\\]\\]'\n        define_in: keys.txt\n        define_as: '^{name} ='\n",
        glob="**/*.md",
        name="md.yml",
    )
    (tmp_path / "keys.txt").write_text("aXb = 1\n", encoding="utf-8")
    (tmp_path / "doc.md").write_text("See [[a.b]].\n", encoding="utf-8")

    # Unescaped, "a.b" would match "aXb" and wrongly pass.
    assert len(_violations(tmp_path, ".md")) == 1


# ─── validation + classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "block",
    [
        "        cite: 'x'\n        define_in: Makefile\n",  # no define_as
        "        cite: 'x'\n        define_as: '^{name}:'\n",  # no define_in
        "        define_in: Makefile\n        define_as: '^{name}:'\n",  # no cite
    ],
)
def test_an_incomplete_cross_reference_is_rejected(tmp_path: Path, block: str) -> None:
    """Two thirds of a cross-reference would load fine and check nothing."""
    _referential(
        tmp_path,
        "  - id: partial\n    category: correctness\n    severity: warning\n"
        '    rule: "Partial"\n    detect:\n      cross_reference:\n' + block,
        glob="**/*.md",
        name="md.yml",
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


def test_the_mechanism_has_its_own_kind() -> None:
    detector = RuleDetector(cross_reference=CrossReference(cite="a", define_in="b", define_as="c"))
    assert kind_of_detector(detector) is CheckKind.CROSS_REFERENCE
