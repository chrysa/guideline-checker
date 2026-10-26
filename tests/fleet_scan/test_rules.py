"""Rule methods and profile resolution: pass / fail / not_applicable / unknown / error."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import TypeAdapter

from guideline_checker.fleet.scan.collect import (
    CommitInfo,
    FileContent,
    HeadCommit,
    Provenance,
    RecentCommits,
    RepoEvidence,
    TreeSnapshot,
    WorkflowRun,
)
from guideline_checker.fleet.scan.globbing import glob_matches
from guideline_checker.fleet.scan.models import (
    METHOD_TYPES,
    CollectionError,
    ErrorKind,
    MethodSpec,
    ProfileDef,
    RuleDef,
    Status,
)
from guideline_checker.fleet.scan.profiles import Detection, resolve_profiles
from guideline_checker.fleet.scan.rules import REGISTRY, RuleContext, declared_make_targets, evaluate_rule

from .conftest import NOW

PROV = Provenance(endpoint="/repos/o/r/git/trees/sha", fetched_at=NOW)
METHOD = TypeAdapter(MethodSpec)
PROFILES = {
    "python": ProfileDef.model_validate({"detect": {"any_file": ["pyproject.toml"]}}),
    "node": ProfileDef.model_validate({"detect": {"any_file": ["package.json"]}}),
    "docs-only": ProfileDef.model_validate({"detect": {"none_of_profiles": ["python", "node"]}}),
}


def tree(*paths: str, truncated: bool = False, dirs: tuple[str, ...] = (), sizes: dict | None = None) -> TreeSnapshot:
    return TreeSnapshot(
        blobs={p: (sizes or {}).get(p, 10) for p in paths}, dirs=frozenset(dirs), truncated=truncated, provenance=PROV
    )


def evidence(**kwargs) -> RepoEvidence:
    kwargs.setdefault("metadata", {"default_branch": "main", "archived": False, "license": {"spdx_id": "MIT"}})
    kwargs.setdefault("metadata_provenance", PROV)
    return RepoEvidence(repo="o/r", collected_at=NOW, **kwargs)


def rule(method: dict, *, profiles: list[str] | None = None, blocking: bool = False) -> RuleDef:
    return RuleDef.model_validate(
        {
            "id": "TST-001",
            "title": "t",
            "source": {"document": "d"},
            "dimension": "conformance",
            "profiles": profiles or ["all"],
            "severity": "high",
            "blocking": blocking,
            "method": method,
        }
    )


def check(method: dict, ev: RepoEvidence, *, profiles: list[str] | None = None, explicit: list[str] | None = None):
    resolution = resolve_profiles(profiles=PROFILES, tree=ev.tree, explicit=explicit)
    return evaluate_rule(
        rule=rule(method, profiles=profiles), ctx=RuleContext(evidence=ev, now=NOW), resolution=resolution
    )


def test_registry_is_closed_and_complete() -> None:
    assert set(REGISTRY) == set(METHOD_TYPES)


class TestGlobbing:
    @pytest.mark.parametrize(
        ("pattern", "path", "hit"),
        [
            ("**/.env", ".env", True),
            ("**/.env", "a/b/.env", True),
            ("*.py", "a/b.py", False),
            ("tests/**/test_*.py", "tests/test_a.py", True),
            ("tests/**/test_*.py", "tests/u/test_a.py", True),
            ("src/?.c", "src/a.c", True),
            ("a+b", "a+b", True),
        ],
    )
    def test_glob(self, pattern: str, path: str, hit: bool) -> None:
        assert bool(glob_matches(pattern=pattern, paths=[path])) is hit


class TestTreeMethods:
    def test_file_exists_pass_fail_and_dir(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md", "docs/"]}}
        assert check(method, evidence(tree=tree("README.md"))).status is Status.PASS
        assert check(method, evidence(tree=tree("x", dirs=("docs",)))).status is Status.PASS
        failed = check(method, evidence(tree=tree("x")))
        assert failed.status is Status.FAIL
        assert failed.evidence[0].source_endpoint == PROV.endpoint

    def test_truncated_tree_makes_absence_unknown(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        assert check(method, evidence(tree=tree("x", truncated=True))).status is Status.UNKNOWN
        absent = {"type": "file_absent", "params": {"paths": [".env"]}}
        assert check(absent, evidence(tree=tree("x", truncated=True))).status is Status.UNKNOWN
        glob = {"type": "tree_glob", "params": {"glob": "tests/**", "min_count": 2}}
        assert check(glob, evidence(tree=tree("tests/a", truncated=True))).status is Status.UNKNOWN

    def test_truncated_tree_still_proves_presence(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        assert check(method, evidence(tree=tree("README.md", truncated=True))).status is Status.PASS
        absent = {"type": "file_absent", "params": {"paths": ["**/.env"]}}
        assert check(absent, evidence(tree=tree("a/.env", truncated=True))).status is Status.FAIL

    def test_file_absent_and_tree_glob(self) -> None:
        absent = {"type": "file_absent", "params": {"paths": ["**/.env"]}}
        assert check(absent, evidence(tree=tree("a/.env"))).status is Status.FAIL
        assert check(absent, evidence(tree=tree("a/.env.example"))).status is Status.PASS
        glob = {"type": "tree_glob", "params": {"glob": "tests/**", "min_count": 2}}
        assert check(glob, evidence(tree=tree("tests/a", "tests/b"))).status is Status.PASS
        assert check(glob, evidence(tree=tree("tests/a"))).status is Status.FAIL

    def test_missing_tree_is_error_when_collection_failed_else_unknown(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        failed = evidence(errors={"tree": CollectionError(kind=ErrorKind.FORBIDDEN, message="403")})
        result = check(method, failed)
        assert result.status is Status.ERROR
        assert result.error_kind is ErrorKind.FORBIDDEN
        assert check(method, evidence()).status is Status.UNKNOWN


class TestContentMethods:
    def _files(self, **files: FileContent) -> dict[str, FileContent]:
        return dict(files)

    def test_file_regex_present_absent_and_missing_policies(self) -> None:
        present = FileContent(path="pyproject.toml", state="present", provenance=PROV, content="[tool.ruff]\n")
        ev = evidence(tree=tree("pyproject.toml"), files={"pyproject.toml": present})
        base = {"type": "file_regex", "params": {"path": "pyproject.toml", "pattern": r"\[tool\.ruff\]"}}
        assert check(base, ev).status is Status.PASS
        absent = {"type": "file_regex", "params": {"path": "pyproject.toml", "pattern": "ruff", "expect": "absent"}}
        assert check(absent, ev).status is Status.FAIL
        missing = FileContent(path="pyproject.toml", state="missing", provenance=PROV)
        ev_missing = evidence(tree=tree("x"), files={"pyproject.toml": missing})
        assert check(base, ev_missing).status is Status.FAIL
        assert check(absent, ev_missing).status is Status.PASS
        unknown = {"type": "file_regex", "params": {"path": "pyproject.toml", "pattern": "x", "missing": "unknown"}}
        assert check(unknown, ev_missing).status is Status.UNKNOWN

    def test_file_regex_any_of_uses_first_present(self) -> None:
        files = {
            "a.cfg": FileContent(path="a.cfg", state="missing", provenance=PROV),
            "b.cfg": FileContent(path="b.cfg", state="present", provenance=PROV, content="key=1"),
        }
        method = {"type": "file_regex", "params": {"any_of": ["a.cfg", "b.cfg"], "pattern": "key="}}
        assert check(method, evidence(tree=tree("b.cfg"), files=files)).status is Status.PASS

    def test_too_large_is_unknown_and_unreadable_is_error(self) -> None:
        method = {"type": "file_regex", "params": {"path": "big.txt", "pattern": "x"}}
        big = FileContent(path="big.txt", state="too_large", provenance=PROV, size=10**9)
        assert check(method, evidence(tree=tree("big.txt"), files={"big.txt": big})).status is Status.UNKNOWN
        err = FileContent(
            path="big.txt",
            state="error",
            provenance=None,
            error=CollectionError(kind=ErrorKind.SERVER_ERROR, message="5xx"),
        )
        result = check(method, evidence(tree=tree("big.txt"), files={"big.txt": err}))
        assert result.status is Status.ERROR
        assert result.error_kind is ErrorKind.SERVER_ERROR

    def test_file_not_collected_follows_head_error(self) -> None:
        method = {"type": "file_regex", "params": {"path": "x", "pattern": "x"}}
        ev = evidence(errors={"head": CollectionError(kind=ErrorKind.INVALID_RESPONSE, message="empty repo")})
        assert check(method, ev).status is Status.ERROR

    def test_makefile_targets(self) -> None:
        content = "help: ## h\n\t@x\nlint test: deps\n\t@y\nVAR := 1\n.PHONY: help\nbuild::\n"
        assert declared_make_targets(content) >= {"help", "lint", "test", "build", ".PHONY"}
        assert "VAR" not in declared_make_targets(content)
        mk = FileContent(path="Makefile", state="present", provenance=PROV, content=content)
        ev = evidence(tree=tree("Makefile"), files={"Makefile": mk})
        assert check({"type": "makefile_targets", "params": {"targets": ["help", "test"]}}, ev).status is Status.PASS
        failed = check({"type": "makefile_targets", "params": {"targets": ["help", "deploy"]}}, ev)
        assert failed.status is Status.FAIL
        assert "deploy" in failed.message
        gone = evidence(
            tree=tree("x"), files={"Makefile": FileContent(path="Makefile", state="missing", provenance=PROV)}
        )
        assert check({"type": "makefile_targets", "params": {"targets": ["help"]}}, gone).status is Status.FAIL


class TestMetadataMethods:
    @pytest.mark.parametrize(
        ("params", "status"),
        [
            ({"field": "archived", "op": "eq", "value": False}, Status.PASS),
            ({"field": "archived", "op": "ne", "value": False}, Status.FAIL),
            ({"field": "default_branch", "op": "in", "value": ["main", "master"]}, Status.PASS),
            ({"field": "license", "op": "eq", "value": "MIT"}, Status.PASS),
            ({"field": "has_wiki", "op": "eq", "value": True}, Status.UNKNOWN),
        ],
    )
    def test_repo_field(self, params: dict, status: Status) -> None:
        assert check({"type": "repo_field", "params": params}, evidence()).status is status

    def test_ci_last_run(self) -> None:
        method = {"type": "ci_last_run", "params": {"max_age_days": 30}}

        def with_run(run):
            return evidence(ci_runs={"default": WorkflowRun(branch="main", run=run, provenance=PROV)})

        fresh = (NOW - timedelta(days=1)).isoformat()
        stale = (NOW - timedelta(days=90)).isoformat()
        assert check(method, with_run({"conclusion": "success", "updated_at": fresh})).status is Status.PASS
        assert check(method, with_run({"conclusion": "success", "updated_at": stale})).status is Status.FAIL
        assert check(method, with_run({"conclusion": "failure", "updated_at": fresh})).status is Status.FAIL
        assert check(method, with_run({"conclusion": "success", "updated_at": "garbage"})).status is Status.UNKNOWN
        none = check(method, with_run(None))
        assert none.status is Status.UNKNOWN
        assert "no completed workflow run" in none.message
        forbidden = evidence(errors={"ci:default": CollectionError(kind=ErrorKind.FORBIDDEN, message="403")})
        assert check(method, forbidden).status is Status.ERROR

    def test_ci_last_run_short_failure_is_unknown(self) -> None:
        method = {"type": "ci_last_run", "params": {"min_failure_duration_s": 15}}

        def failed_after(seconds: int):
            start = NOW - timedelta(hours=1)
            run = {
                "conclusion": "failure",
                "run_started_at": start.isoformat(),
                "updated_at": (start + timedelta(seconds=seconds)).isoformat(),
            }
            return evidence(ci_runs={"default": WorkflowRun(branch="main", run=run, provenance=PROV)})

        blocked = check(method, failed_after(2))
        assert blocked.status is Status.UNKNOWN
        assert "not a code verdict" in blocked.message
        assert check(method, failed_after(300)).status is Status.FAIL
        undated_run = WorkflowRun(branch="main", run={"conclusion": "failure"}, provenance=PROV)
        undated = evidence(ci_runs={"default": undated_run})
        assert check(method, undated).status is Status.FAIL

    def test_commit_messages_absent(self) -> None:
        method = {"type": "commit_messages_absent", "params": {"pattern": "(?i)^wip", "last_n": 2}}

        def with_commits(*messages: str):
            commits = [CommitInfo(sha=f"s{i}", message=m) for i, m in enumerate(messages)]
            return evidence(commits=RecentCommits(commits=commits, provenance=PROV))

        assert check(method, with_commits("feat: a", "fix: b", "WIP old")).status is Status.PASS  # outside last_n
        failed = check(method, with_commits("WIP: a", "fix: b"))
        assert failed.status is Status.FAIL
        assert failed.evidence[0].locator == "s0"
        assert check(method, evidence()).status is Status.UNKNOWN

    def test_last_commit_age(self) -> None:
        method = {"type": "last_commit_age", "params": {"max_days": 30}}

        def with_head(days: int | None):
            when = NOW - timedelta(days=days) if days is not None else None
            return evidence(head=HeadCommit(sha="abc", committed_at=when, provenance=PROV))

        assert check(method, with_head(3)).status is Status.PASS
        assert check(method, with_head(31)).status is Status.FAIL
        assert check(method, with_head(None)).status is Status.UNKNOWN
        errored = evidence(errors={"head": CollectionError(kind=ErrorKind.NETWORK, message="down")})
        assert check(method, errored).status is Status.ERROR


class TestApplicability:
    def test_not_applicable_when_no_profile_matches(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        result = check(method, evidence(tree=tree("README.md")), profiles=["python"])
        assert result.status is Status.NOT_APPLICABLE

    def test_unknown_when_profiles_undeterminable(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        assert check(method, evidence(), profiles=["python"]).status is Status.UNKNOWN
        truncated = evidence(tree=tree("README.md", truncated=True))
        assert check(method, truncated, profiles=["python"]).status is Status.UNKNOWN
        tree_error = evidence(errors={"tree": CollectionError(kind=ErrorKind.NOT_FOUND, message="404")})
        assert check(method, tree_error, profiles=["python"]).status is Status.ERROR

    def test_explicit_profiles_bypass_detection(self) -> None:
        method = {"type": "file_exists", "params": {"any_of": ["README.md"]}}
        result = check(method, evidence(tree=tree("README.md")), profiles=["node"], explicit=["node"])
        assert result.status is Status.PASS

    def test_metadata_error_makes_every_rule_error(self) -> None:
        method = {"type": "repo_field", "params": {"field": "archived", "op": "eq", "value": False}}
        ev = RepoEvidence(
            repo="o/r", collected_at=NOW, errors={"metadata": CollectionError(kind=ErrorKind.NOT_FOUND, message="404")}
        )
        result = check(method, ev)
        assert result.status is Status.ERROR
        assert result.error_kind is ErrorKind.NOT_FOUND

    def test_negative_profile_resolution(self) -> None:
        docs = resolve_profiles(profiles=PROFILES, tree=tree("README.md"), explicit=None)
        assert docs.detections["docs-only"] is Detection.MATCHED
        py = resolve_profiles(profiles=PROFILES, tree=tree("pyproject.toml"), explicit=None)
        assert py.detections["docs-only"] is Detection.NOT_MATCHED
        assert py.matched == ["all", "python"]
        unknown = resolve_profiles(profiles=PROFILES, tree=tree("README.md", truncated=True), explicit=None)
        assert unknown.detections["docs-only"] is Detection.UNDETERMINED
        assert unknown.status is Status.UNKNOWN

    def test_detect_all_files_and_globs(self) -> None:
        profiles = {
            "both": ProfileDef.model_validate({"detect": {"all_files": ["a", "b"]}}),
            "glob": ProfileDef.model_validate({"detect": {"any_glob": ["src/**/*.go"]}}),
        }
        result = resolve_profiles(profiles=profiles, tree=tree("a", "src/x/m.go"), explicit=None)
        assert result.detections["both"] is Detection.NOT_MATCHED
        assert result.detections["glob"] is Detection.MATCHED

    def test_method_spec_rejects_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="does not match any of the expected tags"):
            METHOD.validate_python({"type": "exec", "params": {}})
