from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.fixers import ARTIFACT_PATH, FIX_CONTENT, apply_fix, plan_fixes
from guideline_checker.fleet.distribution import Expectations
from guideline_checker.loader import InstructionFile, SourceType

_EXP = Expectations(canonical_standards="CANON\n", license_text="MIT\n")


def test_license_fixer_returns_template() -> None:
    assert FIX_CONTENT["license-present"](_EXP) == "MIT\n"


def test_standards_fixer_returns_canonical() -> None:
    assert FIX_CONTENT["standards-file"](_EXP) == "CANON\n"


def test_artifact_paths_cover_all_fixers() -> None:
    assert set(ARTIFACT_PATH) == set(FIX_CONTENT)


def _result(rules: list[str]) -> RuleResult:
    instr = InstructionFile(
        path=Path("<d>"), apply_to="", description="", content="", source_type=SourceType.GUIDELINES_YAML
    )
    viols = [
        Violation(file=Path(ARTIFACT_PATH.get(r, r)), line_number=1, line_content="", rule=r, severity="error")
        for r in rules
    ]
    return RuleResult(instruction=instr, violations=viols)


def test_plan_includes_only_fixable_paths() -> None:
    plan = plan_fixes(_result(["license-present", "standards-file"]), _EXP)
    assert sorted(plan.paths) == sorted(["LICENSE", ".chrysa/STANDARDS.md"])


def _full_result(rules: list[str]) -> RuleResult:
    return _result(rules)


def test_apply_fix_dry_run_returns_marker_without_calls() -> None:
    from guideline_checker.fleet.gh_client import GhClient

    def runner(args):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not call gh")

    out = apply_fix("chrysa", "alpha", _full_result(["license-present"]), _EXP, GhClient(runner=runner), dry_run=True)
    assert out == "DRY-RUN"


def test_apply_fix_no_fixable_returns_none() -> None:
    from guideline_checker.fleet.gh_client import GhClient

    def runner(args):  # type: ignore[no-untyped-def]
        raise AssertionError("a non-fixable result must not call gh")

    out = apply_fix("chrysa", "alpha", _full_result(["precommit-pin"]), _EXP, GhClient(runner=runner), dry_run=False)
    assert out is None


def test_apply_fix_idempotent_when_pr_exists() -> None:
    from guideline_checker.fleet.gh_client import GhClient, GhResult

    def runner(args):  # type: ignore[no-untyped-def]
        if " ".join(args).startswith("pr list"):
            return GhResult(True, "https://github.com/chrysa/alpha/pull/3\n", "", 0)
        raise AssertionError("must short-circuit on existing PR")

    out = apply_fix("chrysa", "alpha", _full_result(["license-present"]), _EXP, GhClient(runner=runner), dry_run=False)
    assert out == "https://github.com/chrysa/alpha/pull/3"


def test_apply_fix_happy_path_opens_pr() -> None:
    from guideline_checker.fleet.gh_client import GhClient, GhResult

    calls: list[str] = []

    # The `gh` calls this flow makes, in order, as a table of (matcher, response).
    # A row per API call reads as the script of the happy path; anything not listed
    # falls through to "unexpected" and fails the assertion below.
    responses = (
        (lambda j: j.startswith("pr list"), GhResult(True, "\n", "", 0)),  # no existing PR
        (lambda j: j.endswith("--jq .default_branch"), GhResult(True, "main\n", "", 0)),
        (lambda j: j.endswith("--jq .object.sha"), GhResult(True, "deadbeef\n", "", 0)),
        (lambda j: "git/refs" in j, GhResult(True, "", "", 0)),  # create_branch
        (lambda j: j.endswith("--jq .sha"), GhResult(False, "", "404", 1)),  # no existing file content sha
        (lambda j: "--method PUT" in j, GhResult(True, "", "", 0)),  # put_file
        (lambda j: j.startswith("pr create"), GhResult(True, "https://github.com/chrysa/alpha/pull/7\n", "", 0)),
    )

    def runner(args):  # type: ignore[no-untyped-def]
        joined = " ".join(args)
        calls.append(joined)
        return next((r for matches, r in responses if matches(joined)), GhResult(False, "", "unexpected", 1))

    out = apply_fix(
        "chrysa",
        "alpha",
        _full_result(["license-present", "standards-file"]),
        _EXP,
        GhClient(runner=runner),
        dry_run=False,
    )
    assert out == "https://github.com/chrysa/alpha/pull/7"
    assert any("git/refs" in c for c in calls)
    assert sum(1 for c in calls if "--method PUT" in c) == 2  # one PUT per fixable artifact


def test_apply_fix_returns_none_when_branch_creation_fails() -> None:
    from guideline_checker.fleet.gh_client import GhClient, GhResult

    def runner(args):  # type: ignore[no-untyped-def]
        joined = " ".join(args)
        if joined.startswith("pr list"):
            return GhResult(True, "\n", "", 0)
        if joined.endswith("--jq .default_branch"):
            return GhResult(True, "main\n", "", 0)
        if joined.endswith("--jq .object.sha"):
            return GhResult(False, "", "404", 1)  # branch_sha None → abort
        raise AssertionError("must abort before mutating")

    out = apply_fix("chrysa", "alpha", _full_result(["license-present"]), _EXP, GhClient(runner=runner), dry_run=False)
    assert out is None
