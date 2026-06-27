from __future__ import annotations

from pathlib import Path

from guideline_checker.gh_client import GhClient, GhResult
from guideline_checker.scanner_source import LocalScanner, OriginScanner


class TestLocalScanner:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
        assert LocalScanner(tmp_path).read_file("LICENSE") == "MIT"

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert LocalScanner(tmp_path).read_file("nope.txt") is None


class TestOriginScanner:
    def test_resolves_default_branch_then_reads(self) -> None:
        calls: list[list[str]] = []

        def runner(args):  # type: ignore[no-untyped-def]
            calls.append(list(args))
            if args[1] == "repos/chrysa/foo":
                return GhResult(True, "develop\n", "", 0)
            return GhResult(True, "content-on-develop", "", 0)

        scanner = OriginScanner("chrysa", "foo", GhClient(runner=runner))
        assert scanner.read_file(".chrysa/STANDARDS.md") == "content-on-develop"
        assert scanner.ref == "develop"
        # default branch resolved exactly once even across multiple reads
        scanner.read_file("CLAUDE.md")
        assert sum(1 for c in calls if c[1] == "repos/chrysa/foo") == 1

    def test_explicit_ref_skips_default_branch_lookup(self) -> None:
        def runner(args):  # type: ignore[no-untyped-def]
            assert args[1] != "repos/chrysa/foo", "must not look up default branch"
            return GhResult(True, "x", "", 0)

        scanner = OriginScanner("chrysa", "foo", GhClient(runner=runner), ref="main")
        assert scanner.read_file("LICENSE") == "x"
