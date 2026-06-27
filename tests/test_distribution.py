from __future__ import annotations

from guideline_checker.distribution import Expectations, audit
from guideline_checker.manifest import RepoTarget

_CANON = "# chrysa — Transverse Standards\nbody\n"
_EXP = Expectations(canonical_standards=_CANON, license_text="MIT License\n")


class _FakeScanner:
    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def read_file(self, rel_path: str) -> str | None:
        return self._files.get(rel_path)


def _compliant_files() -> dict[str, str]:
    return {
        ".chrysa/STANDARDS.md": _CANON,
        "CLAUDE.md": "# Repo\n@.chrysa/STANDARDS.md\n",
        ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/chrysa/pre-commit-tools\n",
        "LICENSE": "MIT License\n",
    }


class TestAuditCompliant:
    def test_no_violations_when_all_present(self) -> None:
        target = RepoTarget(name="alpha")
        assert audit(_FakeScanner(_compliant_files()), target, _EXP) == []


class TestAuditDrift:
    def test_standards_file_mismatch(self) -> None:
        files = _compliant_files()
        files[".chrysa/STANDARDS.md"] = "stale\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["standards-file"]
        assert str(violations[0].file) == ".chrysa/STANDARDS.md"

    def test_missing_claude_import(self) -> None:
        files = _compliant_files()
        files["CLAUDE.md"] = "# Repo\nno import here\n"
        violations = audit(_FakeScanner(files), RepoTarget(name="alpha"), _EXP)
        assert [v.rule for v in violations] == ["claude-import"]

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
