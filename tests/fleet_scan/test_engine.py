"""End-to-end engine runs against the fake GitHub: scope, errors in totals, checkpoint/resume, reports."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from guideline_checker.fleet.scan.collect import CollectionNeeds, Collector
from guideline_checker.fleet.scan.config import load_config
from guideline_checker.fleet.scan.engine import ARCHIVED_ON_GITHUB, NOT_IN_CATALOG, ScanEngine, resolve_scope
from guideline_checker.fleet.scan.errors import (
    ConfigValidationError,
    RateLimitedError,
    ReporterDisabledError,
    ResumeRefusedError,
)
from guideline_checker.fleet.scan.models import (
    REVIVAL_CAVEAT,
    Dimension,
    ErrorKind,
    RepoOutcome,
    RunManifest,
    RunStatus,
    ScanReport,
    Status,
)
from guideline_checker.fleet.scan.reporters.cli import render_cli
from guideline_checker.fleet.scan.reporters.history import HistoryIndex
from guideline_checker.fleet.scan.reporters.markdown import _cell as markdown_cell
from guideline_checker.fleet.scan.reporters.markdown import render_markdown
from guideline_checker.fleet.scan.reporters.notion import NotionReporter
from guideline_checker.fleet.scan.reporters.publisher import ReportPublisher
from guideline_checker.fleet.scan.run_store import RunStore, repo_file_name

from .conftest import API, NOW, make_repo


def _engine(config, tmp_path: Path, **kwargs) -> ScanEngine:
    publisher = ReportPublisher(config=config.reporters, history_path=tmp_path / "history.jsonl")
    kwargs.setdefault("publish", publisher)
    return ScanEngine(config=config, output_dir=tmp_path / "runs", clock=lambda: NOW, **kwargs)


def _collector(config, client) -> Collector:
    needs = CollectionNeeds.from_rules(
        rules=config.conformance.rules, profiles=config.conformance.profiles, revival=config.reporters.revival.enabled
    )
    return Collector(client=client, needs=needs, max_file_bytes=config.github.content.max_file_bytes)


def _scan(config, make_client, tmp_path: Path, requested: list[str], **kwargs) -> tuple[ScanReport, ScanEngine]:
    engine = _engine(config, tmp_path)

    async def go() -> ScanReport:
        async with make_client(**kwargs) as client:
            return await engine.run(requested=requested, subset=None, collect=_collector(config, client).collect)

    return asyncio.run(go()), engine


def _by_repo(report: ScanReport) -> dict:
    return {r.repo: r for r in report.repos}


class TestScope:
    def test_subset_explicit_and_catalog(self, config) -> None:
        assert resolve_scope(config=config, subset="pilot", repos=None) == [
            "chrysa/alpha",
            "chrysa/beta",
            "chrysa/gamma",
        ]
        assert resolve_scope(config=config, subset="pilot", repos=["CHRYSA/ALPHA", "chrysa/alpha", "x/y"]) == [
            "chrysa/alpha",
            "x/y",
        ]
        assert len(resolve_scope(config=config, subset=None, repos=None)) == 4

    def test_unknown_subset_is_a_config_error(self, config) -> None:
        with pytest.raises(ConfigValidationError, match="unknown subset 'nope'"):
            resolve_scope(config=config, subset="nope", repos=None)


class TestFullRun:
    def test_every_requested_repo_appears_once_with_errors_kept(
        self, config, make_client, fake_github, tmp_path
    ) -> None:
        fake_github.repos["chrysa/delta"].status = 404
        requested = ["chrysa/alpha", "chrysa/beta", "chrysa/gamma", "chrysa/delta", "ghost/nowhere"]
        report, _ = _scan(config, make_client, tmp_path, requested)

        assert [r.repo for r in report.repos] == requested
        scope = report.scope
        assert scope.requested == requested
        assert scope.evaluated == ["chrysa/alpha", "chrysa/beta"]
        assert [e.repo for e in scope.excluded] == ["chrysa/gamma"]
        assert {e.repo: e.kind for e in scope.errors} == {
            "chrysa/delta": ErrorKind.NOT_FOUND,
            "ghost/nowhere": ErrorKind.CONFIG,
        }
        assert "chrysa/delta" in scope.collected and "ghost/nowhere" not in scope.collected
        repos = _by_repo(report)
        assert repos["ghost/nowhere"].collection_errors[0].message == NOT_IN_CATALOG
        assert all(c.status is Status.ERROR for c in repos["chrysa/delta"].checks)
        agg = report.aggregates
        assert (agg.repos_requested, agg.repos_evaluated, agg.repos_excluded, agg.repos_errored) == (5, 2, 1, 2)
        assert agg.error_kinds[ErrorKind.NOT_FOUND] == 1
        assert agg.dimensions[Dimension.CONFORMANCE].error > 0  # errors stay in the denominator

    def test_alpha_is_clean_and_beta_is_blocked(self, config, make_client, tmp_path) -> None:
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha", "chrysa/beta"])
        repos = _by_repo(report)
        alpha, beta = repos["chrysa/alpha"], repos["chrysa/beta"]
        alpha_status = {c.rule_id: c.status for c in alpha.checks}
        assert alpha_status["NODE-001"] is Status.NOT_APPLICABLE
        assert all(s in {Status.PASS, Status.NOT_APPLICABLE} for s in alpha_status.values()), alpha_status
        assert alpha.commit_sha == "a" * 40
        assert alpha.ref == "main"
        assert alpha.profiles == ["all", "container", "python"]
        assert alpha.blocked is False
        assert beta.blocked is True
        assert beta.blocking_failures == ["SEC-001"]
        beta_status = {c.rule_id: c.status for c in beta.checks}
        assert beta_status["CI-001"] is Status.FAIL
        assert beta_status["SCM-001"] is Status.FAIL
        assert beta_status["ACT-001"] is Status.FAIL
        assert beta_status["MK-001"] is Status.FAIL
        assert report.aggregates.blocked_repos == ["chrysa/beta"]
        evidence = next(c for c in alpha.checks if c.rule_id == "DOC-001").evidence[0]
        assert evidence.source_endpoint.startswith("/repos/chrysa/alpha/git/trees/")

    def test_explicit_scan_profile_and_unknown_ci(self, config, make_client, tmp_path) -> None:
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/delta"])
        delta = report.repos[0]
        statuses = {c.rule_id: c.status for c in delta.checks}
        assert delta.profiles == ["all", "node"]
        assert statuses["NODE-001"] is Status.FAIL
        assert statuses["TST-001"] is Status.NOT_APPLICABLE
        assert statuses["CI-001"] is Status.UNKNOWN  # no completed run ≠ pass
        assert statuses["SCM-001"] is Status.PASS  # no commits at all: nothing matches

    def test_truncated_tree_yields_unknown(self, config, make_client, fake_github, tmp_path) -> None:
        fake_github.repos["chrysa/alpha"].truncated = True
        fake_github.repos["chrysa/alpha"].files.pop("README.md")
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha"])
        statuses = {c.rule_id: c.status for c in report.repos[0].checks}
        assert statuses["DOC-001"] is Status.UNKNOWN
        assert statuses["SEC-001"] is Status.UNKNOWN
        assert statuses["NODE-001"] is Status.UNKNOWN  # node profile cannot be ruled out
        assert statuses["PKG-001"] is Status.PASS  # contents API is authoritative even on a truncated tree

    def test_partial_forbidden_facet_is_typed_error(self, config, make_client, fake_github, tmp_path) -> None:
        original = fake_github.handle

        def handle(request):
            if request.url.path.endswith("/actions/runs"):
                import httpx

                return httpx.Response(403, json={"message": "Resource not accessible"})
            return original(request)

        fake_github.handle = handle  # type: ignore[method-assign]
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha"])
        alpha = report.repos[0]
        ci = next(c for c in alpha.checks if c.rule_id == "CI-001")
        assert alpha.outcome is RepoOutcome.EVALUATED
        assert ci.status is Status.ERROR
        assert ci.error_kind is ErrorKind.FORBIDDEN
        assert any(e.kind is ErrorKind.FORBIDDEN for e in alpha.collection_errors)

    def test_large_file_is_unknown(self, config, make_client, fake_github, tmp_path) -> None:
        fake_github.repos["chrysa/alpha"].files["pyproject.toml"] = "[tool.ruff]\n" + "x" * 600_000
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha"])
        pkg = next(c for c in report.repos[0].checks if c.rule_id == "PKG-001")
        assert pkg.status is Status.UNKNOWN
        assert "size cap" in pkg.message

    def test_collects_only_what_rules_need(self, config, make_client, fake_github, tmp_path) -> None:
        _scan(config, make_client, tmp_path, ["chrysa/alpha"])
        contents = sorted(u.split("/contents/")[1].split("?")[0] for u in fake_github.calls if "/contents/" in u)
        assert contents == ["Makefile", "pyproject.toml"]
        assert all("per_page=1" in u and "event=push" in u for u in fake_github.calls if "/actions/runs" in u)

    def test_json_round_trip(self, config, make_client, tmp_path) -> None:
        report, engine = _scan(config, make_client, tmp_path, ["chrysa/alpha", "chrysa/beta", "chrysa/gamma"])
        path = tmp_path / "runs" / engine.run_id / "report.json"
        reloaded = ScanReport.model_validate_json(path.read_text(encoding="utf-8"))
        assert reloaded == report
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["repos"][0]["scores"]["conformance"]["pass"] >= 1
        assert raw["started_at"].endswith("Z") or raw["started_at"].endswith("+00:00")
        assert raw["revival_caveat"] == REVIVAL_CAVEAT

    def test_reports_and_history(self, config, make_client, tmp_path) -> None:
        report, engine = _scan(config, make_client, tmp_path, ["chrysa/alpha", "chrysa/beta", "chrysa/gamma"])
        markdown = (tmp_path / "runs" / engine.run_id / "report.md").read_text(encoding="utf-8")
        for needle in (
            "## Scope",
            "## Blocking failures",
            "chrysa/beta**: SEC-001",
            "## Unknowns",
            "## Revival",
            "## Reprise",
        ):
            assert needle in markdown
        assert "cov" in markdown and "/" in markdown  # denominators are printed
        assert REVIVAL_CAVEAT.capitalize() in markdown
        assert report.revival_candidates[0].repo == "chrysa/alpha"
        assert report.revival_candidates[0].caveat == REVIVAL_CAVEAT
        cli = render_cli(report, max_rows=1)
        assert "BLOCKED repos (1): chrysa/beta" in cli
        assert "1 more repo(s)" in cli or "2 more repo(s)" in cli
        history = HistoryIndex(path=tmp_path / "history.jsonl", retention_runs=2)
        assert [e["run_id"] for e in history.entries()] == [engine.run_id]

    def test_repo_archived_on_github_is_excluded_after_metadata_only(
        self, config, make_client, fake_github, tmp_path
    ) -> None:
        fake_github.repos["chrysa/beta"].metadata["archived"] = True
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/beta"])

        result = _by_repo(report)["chrysa/beta"]
        assert result.outcome is RepoOutcome.EXCLUDED
        assert result.exclusion_reason == ARCHIVED_ON_GITHUB
        assert result.checks == []
        assert [c for c in fake_github.calls if "/repos/chrysa/beta" in c] == [f"{API}/repos/chrysa/beta"]

    def test_fork_is_judged_on_its_own_commits_only(self, config, make_client, fake_github, tmp_path) -> None:
        beta = fake_github.repos["chrysa/beta"]  # its recent history holds an upstream WIP commit
        beta.metadata.update(fork=True, parent={"full_name": "upstream/beta", "default_branch": "dev"})
        beta.compare_commits = [{"sha": "o1", "commit": {"message": "feat: own"}}]
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/beta"])

        scm = next(c for c in report.repos[0].checks if c.rule_id == "SCM-001")
        assert scm.status is Status.PASS
        assert "on top of upstream/beta" in scm.message
        assert f"{API}/repos/chrysa/beta/compare/upstream:dev...{'a' * 40}" in fake_github.calls
        assert not [c for c in fake_github.calls if "/repos/chrysa/beta/commits?" in c]

    def test_fork_own_commits_still_fail(self, config, make_client, fake_github, tmp_path) -> None:
        beta = fake_github.repos["chrysa/beta"]
        beta.metadata.update(fork=True, parent={"full_name": "upstream/beta", "default_branch": "dev"})
        beta.compare_commits = [
            {"sha": "o1", "commit": {"message": "WIP own"}},
            {"sha": "o2", "commit": {"message": "fix: newest"}},
        ]
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/beta"])

        scm = next(c for c in report.repos[0].checks if c.rule_id == "SCM-001")
        assert scm.status is Status.FAIL
        assert [e.locator for e in scm.evidence] == ["o1"]

    def test_fork_without_parent_is_an_error_not_judged_on_upstream(
        self, config, make_client, fake_github, tmp_path
    ) -> None:
        fake_github.repos["chrysa/beta"].metadata["fork"] = True
        report, _ = _scan(config, make_client, tmp_path, ["chrysa/beta"])

        statuses = {c.rule_id: c.status for c in report.repos[0].checks}
        assert statuses["SCM-001"] is Status.ERROR
        assert not [c for c in fake_github.calls if "/repos/chrysa/beta/commits?" in c]

    def test_history_retention(self, config, make_client, tmp_path) -> None:
        for _ in range(3):
            _scan(config, make_client, tmp_path, ["chrysa/gamma"])
        entries = HistoryIndex(path=tmp_path / "history.jsonl", retention_runs=2).entries()
        assert len(entries) == 2  # retention_runs in the fixture

    def test_rate_limit_abort_marks_remaining_repos(self, config, make_client, tmp_path) -> None:
        calls: list[str] = []

        async def collect(repo: str):
            calls.append(repo)
            raise RateLimitedError("primary rate limit exhausted; reset in 3600s > max_wait_s")

        serial = replace(config, github=config.github.model_copy(update={"concurrency": 1}))
        engine = _engine(serial, tmp_path)
        report = asyncio.run(
            engine.run(requested=["chrysa/alpha", "chrysa/beta", "chrysa/delta"], subset=None, collect=collect)
        )
        assert calls == ["chrysa/alpha"]  # nothing collected after the abort
        assert {e.kind for e in report.scope.errors} == {ErrorKind.RATE_LIMITED}
        assert len(report.scope.errors) == 3
        assert "Reprise" in render_markdown(report)
        assert all(not r.is_final for r in report.repos)


class TestCheckpointResume:
    def _interrupting_collector(self, config, client, *, stop_at: str):
        real = _collector(config, client).collect

        async def collect(repo: str):
            if repo == stop_at:
                raise asyncio.CancelledError
            return await real(repo)

        return collect

    def _interrupted_run(self, config, make_client, tmp_path) -> ScanEngine:
        serial = replace(config, github=config.github.model_copy(update={"concurrency": 1}))
        engine = _engine(serial, tmp_path)

        async def go():
            async with make_client() as client:
                collect = self._interrupting_collector(serial, client, stop_at="chrysa/beta")
                await engine.run(
                    requested=["chrysa/alpha", "chrysa/beta", "chrysa/delta"], subset=None, collect=collect
                )

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(go())
        return engine

    def test_interruption_leaves_resumable_run(self, config, make_client, tmp_path) -> None:
        engine = self._interrupted_run(config, make_client, tmp_path)
        store = RunStore(output_dir=tmp_path / "runs", run_id=engine.run_id)
        manifest = store.read_manifest()
        assert manifest.status is RunStatus.INTERRUPTED
        saved = store.load_repos(manifest.requested)
        assert set(saved) == {"chrysa/alpha"}
        # the report of an interrupted run still lists every requested repo
        partial = _engine(config, tmp_path).report(run_id=engine.run_id)
        assert {e.repo: e.kind for e in partial.scope.errors} == {
            "chrysa/beta": ErrorKind.INTERRUPTED,
            "chrysa/delta": ErrorKind.INTERRUPTED,
        }

    def test_resume_with_matching_fingerprint(self, config, make_client, fake_github, tmp_path) -> None:
        engine = self._interrupted_run(config, make_client, tmp_path)
        fake_github.calls.clear()
        resumed = _engine(config, tmp_path)

        async def go():
            async with make_client() as client:
                return await resumed.resume(
                    run_id=engine.run_id, collect=_collector(config, client).collect, force_config_change=False
                )

        report = asyncio.run(go())
        assert report.resumed is True
        assert report.forced_config_change is False
        assert report.scope.evaluated == ["chrysa/alpha", "chrysa/beta", "chrysa/delta"]
        assert not any("/repos/chrysa/alpha" in u for u in fake_github.calls)  # finished repo not re-collected
        manifest = RunStore(output_dir=tmp_path / "runs", run_id=engine.run_id).read_manifest()
        assert manifest.status is RunStatus.COMPLETED

    def test_resume_refused_on_fingerprint_mismatch_unless_forced(
        self, config, make_client, fixture_config_dir, tmp_path
    ) -> None:
        engine = self._interrupted_run(config, make_client, tmp_path)
        path = fixture_config_dir / "conformance.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        doc["rules_version"] = "test-2"
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")
        changed = load_config(fixture_config_dir)

        async def go(force: bool):
            async with make_client() as client:
                return await _engine(changed, tmp_path).resume(
                    run_id=engine.run_id, collect=_collector(changed, client).collect, force_config_change=force
                )

        with pytest.raises(ResumeRefusedError, match="fingerprint changed"):
            asyncio.run(go(force=False))
        report = asyncio.run(go(force=True))
        assert report.forced_config_change is True
        assert report.rules_version == "test-2"
        manifest = RunStore(output_dir=tmp_path / "runs", run_id=engine.run_id).read_manifest()
        assert manifest.previous_fingerprints == [config.config_fingerprint]
        assert "YES — results mix configurations" in render_markdown(report)

    def test_resume_unknown_or_invalid_run(self, config, tmp_path) -> None:
        engine = _engine(config, tmp_path)

        async def collect(repo: str):  # pragma: no cover - never reached
            raise AssertionError(repo)

        with pytest.raises(ResumeRefusedError, match="not found"):
            asyncio.run(engine.resume(run_id="20260101T000000Z-abcdef", collect=collect, force_config_change=False))
        with pytest.raises(ResumeRefusedError, match="invalid run id"):
            asyncio.run(engine.resume(run_id="../../etc", collect=collect, force_config_change=False))

    def test_corrupt_checkpoint_is_recollected(self, config, make_client, tmp_path) -> None:
        engine = self._interrupted_run(config, make_client, tmp_path)
        store = RunStore(output_dir=tmp_path / "runs", run_id=engine.run_id)
        store.repo_path("chrysa/alpha").write_text("{not json", encoding="utf-8")
        assert store.load_repos(["chrysa/alpha"]) == {}

    def test_checkpoint_file_names_cannot_traverse(self) -> None:
        assert repo_file_name("Chrysa/Alpha") == "chrysa__alpha.json"
        for hostile in ("../../etc/passwd", "a/../../b", "owner/..", "/abs/path", "a\\b", "owner/name\n"):
            name = repo_file_name(hostile)
            assert name.startswith("invalid__") and "/" not in name and ".." not in name and "\n" not in name

    def test_markdown_cells_neutralise_api_text(self) -> None:
        cell = markdown_cell("x|y\r\n![p](https://evil.test/p) <img src=//e> `c` a\\b")
        assert "\n" not in cell and "\r" not in cell
        assert "<img" not in cell
        assert "](" not in cell.replace("\\]", "")  # every bracket is escaped, so no link survives
        assert "x\\|y" in cell and "\\`c\\`" in cell

    def test_manifest_round_trip(self, config, make_client, tmp_path) -> None:
        _, engine = _scan(config, make_client, tmp_path, ["chrysa/gamma"])
        manifest = RunStore(output_dir=tmp_path / "runs", run_id=engine.run_id).read_manifest()
        assert isinstance(manifest, RunManifest)
        assert manifest.config_fingerprint == config.config_fingerprint
        assert manifest.finished_at is not None and manifest.finished_at >= manifest.started_at


class TestReportCommand:
    def test_report_regenerates_from_saved_results(self, config, make_client, tmp_path) -> None:
        report, engine = _scan(config, make_client, tmp_path, ["chrysa/alpha", "chrysa/gamma"])
        rebuilt = _engine(config, tmp_path).report(run_id=engine.run_id)
        assert rebuilt.repos == report.repos
        assert rebuilt.scope == report.scope


def test_notion_reporter_is_a_refusing_stub(config, make_client, tmp_path) -> None:
    report, _ = _scan(config, make_client, tmp_path, ["chrysa/gamma"])
    with pytest.raises(ReporterDisabledError, match="disabled"):
        NotionReporter(config=config.reporters.notion).publish(report)


def test_report_model_rejects_inconsistent_scope(config, make_client, tmp_path) -> None:
    report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha", "chrysa/gamma"])
    data = report.model_dump(mode="json")
    data["repos"] = data["repos"][:1]
    with pytest.raises(ValueError, match="exactly once in repos"):
        ScanReport.model_validate(data)
    data = report.model_dump(mode="json")
    data["finished_at"] = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="finished_at"):
        ScanReport.model_validate(data)
    data = report.model_dump(mode="json")
    data["started_at"] = "2026-10-24T12:00:00"  # naive
    with pytest.raises(ValueError, match="timezone-aware"):
        ScanReport.model_validate(data)


def test_missing_default_branch_is_a_typed_collection_error(config, make_client, fake_github, tmp_path) -> None:
    fake_github.repos["chrysa/alpha"] = make_repo("chrysa/alpha", metadata={"default_branch": None})
    report, _ = _scan(config, make_client, tmp_path, ["chrysa/alpha"])
    alpha = report.repos[0]
    assert alpha.outcome is RepoOutcome.EVALUATED
    assert any(e.message == "no default branch reported" for e in alpha.collection_errors)
    assert {c.status for c in alpha.checks} <= {
        Status.ERROR,
        Status.UNKNOWN,
        Status.PASS,
        Status.FAIL,
        Status.NOT_APPLICABLE,
    }
