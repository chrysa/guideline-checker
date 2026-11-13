from __future__ import annotations

from guideline_checker.fleet.distribution import Expectations
from guideline_checker.fleet.gh_client import GhClient, GhResult
from guideline_checker.fleet.manifest import RepoTarget
from guideline_checker.fleet.origin_audit import run_origin_audit

_CANON = "# chrysa — Transverse Standards\nbody\n"
_EXP = Expectations(canonical_standards=_CANON, license_text="MIT License\n")


def _make_runner(repo_files: dict[str, dict[str, str]]):
    """Runner serving per-repo file maps; unknown repo → 404."""

    def runner(args):  # type: ignore[no-untyped-def]
        joined = " ".join(args)
        # repo_exists probe
        if joined.endswith("--jq .name"):
            repo = args[1].split("/")[-1]
            return GhResult(repo in repo_files, repo + "\n", "", 0)
        if joined.endswith("--jq .default_branch"):
            return GhResult(True, "main\n", "", 0)
        # contents read: repos/chrysa/<repo>/contents/<path>?ref=main
        spec = args[-1]  # repos/chrysa/<repo>/contents/<path>?ref=main
        repo = spec.split("/")[2]
        path = spec.split("/contents/")[1].split("?")[0]
        files = repo_files.get(repo, {})
        return GhResult(True, files[path], "", 0) if path in files else GhResult(False, "", "404", 1)

    return runner


def _compliant() -> dict[str, str]:
    return {
        ".chrysa/STANDARDS.md": _CANON,
        "CLAUDE.md": "@.chrysa/STANDARDS.md\n",
        ".pre-commit-config.yaml": "repos:\n  - repo: https://github.com/chrysa/pre-commit-tools\n",
        "LICENSE": "MIT License\n",
    }


class TestRunOriginAudit:
    def test_compliant_repo_has_no_violations(self) -> None:
        client = GhClient(runner=_make_runner({"alpha": _compliant()}))
        results = run_origin_audit([RepoTarget(name="alpha")], _EXP, client)
        assert results[0].errors == 0
        assert results[0].fetch_failed is False

    def test_drifting_repo_reports_errors(self) -> None:
        files = _compliant()
        del files["LICENSE"]
        client = GhClient(runner=_make_runner({"alpha": files}))
        results = run_origin_audit([RepoTarget(name="alpha")], _EXP, client)
        assert results[0].errors == 1

    def test_unreachable_repo_marks_fetch_failed(self) -> None:
        client = GhClient(runner=_make_runner({}))  # no repos resolve
        results = run_origin_audit([RepoTarget(name="ghost")], _EXP, client)
        assert results[0].fetch_failed is True
        assert results[0].errors == 1
