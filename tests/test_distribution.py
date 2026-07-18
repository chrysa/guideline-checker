from __future__ import annotations

from guideline_checker.distribution import (
    STANDARDS_BLOCK_END,
    STANDARDS_BLOCK_START,
    Expectations,
    audit,
    inject_standards_block,
    render_standards_block,
    standards_body,
)
from guideline_checker.manifest import RepoTarget

_CANON = "# chrysa — Transverse Standards\nbody\n"
_EXP = Expectations(canonical_standards=_CANON, license_text="MIT License\n")
_CLAUDE_OK = "# Repo — alpha\n\n" + render_standards_block(_EXP)


class _FakeScanner:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def read_file(self, rel_path: str) -> str | None:
        return self._files.get(rel_path)


def _compliant_files() -> dict[str, str]:
    return {
        "CLAUDE.md": _CLAUDE_OK,
        ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/chrysa/pre-commit-tools\n",
        "LICENSE": "MIT License\n",
    }


class TestAuditCompliant:
    def test_no_violations_when_all_present(self) -> None:
        target = RepoTarget(name="alpha")
        assert audit(_FakeScanner(_compliant_files()), target, _EXP) == []


class TestAuditDrift:
    def test_standards_block_body_mismatch(self) -> None:
        files = _compliant_files()
        files["CLAUDE.md"] = f"# Repo\n\n{STANDARDS_BLOCK_START}\nstale body\n{STANDARDS_BLOCK_END}\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["standards-block"]
        assert str(violations[0].file) == "CLAUDE.md"

    def test_missing_standards_block(self) -> None:
        files = _compliant_files()
        files["CLAUDE.md"] = "# Repo\nno managed block here\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["standards-block"]

    def test_missing_claude_file(self) -> None:
        files = _compliant_files()
        del files["CLAUDE.md"]
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["standards-block"]

    def test_missing_precommit_pin(self) -> None:
        files = _compliant_files()
        files[".pre-commit-config.yaml"] = "repos: []\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["precommit-pin"]

    def test_missing_license(self) -> None:
        files = _compliant_files()
        del files["LICENSE"]
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["license-present"]


class TestApplicability:
    def test_non_applicable_license_is_not_a_violation(self) -> None:
        files = _compliant_files()
        del files["LICENSE"]
        target = RepoTarget(name="perso", license_applicable=False)
        assert audit(_FakeScanner(files), target, _EXP) == []


class TestStandardsBody:
    def test_strips_leading_html_header_comment(self) -> None:
        canonical = "<!--\n  MANAGED FILE — DO NOT EDIT.\n-->\n\n# chrysa — Transverse Standards\nbody\n"
        assert standards_body(canonical) == "# chrysa — Transverse Standards\nbody"

    def test_keeps_content_when_no_header_comment(self) -> None:
        assert standards_body("# Title\nbody\n") == "# Title\nbody"


class TestInjectStandardsBlock:
    def test_creates_block_when_claude_absent(self) -> None:
        assert inject_standards_block(None, _EXP) == render_standards_block(_EXP)

    def test_appends_block_when_missing(self) -> None:
        out = inject_standards_block("# Repo\nlocal rules\n", _EXP)
        assert STANDARDS_BLOCK_START in out and out.startswith("# Repo\nlocal rules\n")

    def test_refreshes_existing_block_preserving_surroundings(self) -> None:
        stale = f"# Repo\n\n{STANDARDS_BLOCK_START}\nstale\n{STANDARDS_BLOCK_END}\n\n# Footer\n"
        out = inject_standards_block(stale, _EXP)
        assert "stale" not in out
        assert out.startswith("# Repo\n\n") and out.endswith("# Footer\n")
        assert standards_body(_CANON) in out
