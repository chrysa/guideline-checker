"""Scan orchestration: scope → collect (bounded concurrency) → evaluate → checkpoint → aggregate.

Invariants the engine guarantees:
* every requested repository appears exactly once in the scope and in ``repos`` — a repo
  absent from the catalog is reported as ``error/config`` ("not in catalog"), never dropped;
* each finished repository is checkpointed atomically before the next aggregate step, so an
  interruption (Ctrl-C, cancellation, crash) leaves a resumable run;
* a resume reuses saved results only when the config fingerprint matches, unless the
  caller forces it (which is then recorded in the manifest and the report);
* a primary rate-limit abort stops collection: the remaining repositories are reported
  ``error/rate_limited`` and are collected again by a later resume.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from guideline_checker import __version__
from guideline_checker.fleet.scan.collect import CollectFn, RepoEvidence
from guideline_checker.fleet.scan.config import CatalogEntry, EffectiveConfig
from guideline_checker.fleet.scan.errors import ConfigValidationError, RateLimitedError, ResumeRefusedError
from guideline_checker.fleet.scan.models import (
    CollectionError,
    ErrorKind,
    ExcludedRepo,
    RepoOutcome,
    RepoResult,
    RunManifest,
    RunStatus,
    ScanReport,
    ScanScope,
    ScopeError,
    utc_now,
)
from guideline_checker.fleet.scan.profiles import resolve_profiles
from guideline_checker.fleet.scan.revival import rank_candidates, revival_signals
from guideline_checker.fleet.scan.rules import RuleContext, evaluate_rule
from guideline_checker.fleet.scan.run_store import RunStore, new_run_id
from guideline_checker.fleet.scan.scoring import aggregate, score_repo

logger = logging.getLogger("guideline_checker.fleet.scan.engine")

NOT_IN_CATALOG = "not in catalog"
ARCHIVED_ON_GITHUB = "archived on GitHub; out of scope, not evaluated"

Clock = Callable[[], datetime]


def resolve_scope(*, config: EffectiveConfig, subset: str | None, repos: list[str] | None) -> list[str]:
    """Requested repositories, de-duplicated case-insensitively, in request order.

    Explicit ``repos`` win over ``subset``; neither means the whole catalog. Requested
    names absent from the catalog are kept (they are reported as errors, not dropped).
    """
    if repos:
        wanted = repos
    elif subset is not None:
        if subset not in config.repos.subsets:
            known = ", ".join(sorted(config.repos.subsets)) or "none"
            raise ConfigValidationError([f"unknown subset '{subset}' (known: {known})"])
        wanted = config.repos.subsets[subset]
    else:
        wanted = [entry.repo.full_name for entry in config.catalog.values()]
    seen: set[str] = set()
    scope: list[str] = []
    for raw in wanted:
        name = raw.strip()
        entry = config.lookup(name)
        canonical = entry.repo.full_name if entry is not None else name
        if name and canonical.lower() not in seen:
            seen.add(canonical.lower())
            scope.append(canonical)
    return scope


@dataclass
class _AbortState:
    """Set once a primary rate-limit abort happened; later repos are not collected."""

    error: RateLimitedError | None = None


class ScanEngine:
    def __init__(
        self,
        *,
        config: EffectiveConfig,
        output_dir: Path,
        clock: Clock = utc_now,
        publish: Callable[[ScanReport, RunStore], None] | None = None,
    ) -> None:
        self._config = config
        self._output_dir = output_dir
        self._clock = clock
        self._publish = publish
        self.run_id: str | None = None

    # ── public API ────────────────────────────────────────────────────────────

    async def run(self, *, requested: list[str], subset: str | None, collect: CollectFn) -> ScanReport:
        started = self._clock()
        store = RunStore(output_dir=self._output_dir, run_id=new_run_id(started))
        self.run_id = store.run_id
        manifest = RunManifest(
            run_id=store.run_id,
            status=RunStatus.RUNNING,
            started_at=started,
            tool_version=__version__,
            rules_version=self._config.conformance.rules_version,
            rules_sha256=self._config.rules_sha256,
            config_fingerprint=self._config.config_fingerprint,
            subset=subset,
            requested=requested,
        )
        store.write_manifest(manifest)
        return await self._execute(store=store, manifest=manifest, done={}, collect=collect)

    async def resume(self, *, run_id: str, collect: CollectFn, force_config_change: bool) -> ScanReport:
        store = RunStore(output_dir=self._output_dir, run_id=run_id)
        self.run_id = run_id
        manifest = store.read_manifest()
        fingerprint = self._config.config_fingerprint
        changed = manifest.config_fingerprint != fingerprint
        if changed and not force_config_change:
            raise ResumeRefusedError(
                f"run {run_id}: config fingerprint changed ({manifest.config_fingerprint[:12]} → {fingerprint[:12]}); "
                "rerun, or pass --force-config-change to mix results from both configurations"
            )
        previous = (
            [*manifest.previous_fingerprints, manifest.config_fingerprint]
            if changed
            else manifest.previous_fingerprints
        )
        manifest = manifest.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "resumed": True,
                "forced_config_change": manifest.forced_config_change or changed,
                "config_fingerprint": fingerprint,
                "rules_version": self._config.conformance.rules_version,
                "rules_sha256": self._config.rules_sha256,
                "previous_fingerprints": previous,
                "finished_at": None,
            }
        )
        store.write_manifest(manifest)
        done = {repo: r for repo, r in store.load_repos(manifest.requested).items() if r.is_final}
        logger.info("resuming %s: %d/%d repo(s) already final", run_id, len(done), len(manifest.requested))
        return await self._execute(store=store, manifest=manifest, done=done, collect=collect)

    def report(self, *, run_id: str) -> ScanReport:
        """Rebuild the report from saved results; unfinished repos are ``error/interrupted``."""
        store = RunStore(output_dir=self._output_dir, run_id=run_id)
        self.run_id = run_id
        manifest = store.read_manifest()
        saved = store.load_repos(manifest.requested)
        results = [saved.get(repo) or self._interrupted(repo) for repo in manifest.requested]
        finished = manifest.finished_at or max(self._clock(), manifest.started_at)
        return self._build_report(manifest=manifest, results=results, finished_at=finished)

    # ── execution ─────────────────────────────────────────────────────────────

    async def _execute(
        self, *, store: RunStore, manifest: RunManifest, done: dict[str, RepoResult], collect: CollectFn
    ) -> ScanReport:
        semaphore = asyncio.Semaphore(self._config.github.concurrency)
        abort = _AbortState()
        pending = [repo for repo in manifest.requested if repo not in done]
        tasks = [
            asyncio.create_task(
                self._process(repo=repo, collect=collect, semaphore=semaphore, abort=abort, store=store)
            )
            for repo in pending
        ]
        try:
            fresh = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            store.write_manifest(manifest.model_copy(update={"status": RunStatus.INTERRUPTED}))
            logger.warning("run %s interrupted; resume with --resume %s", store.run_id, store.run_id)
            raise
        by_repo = {**done, **{r.repo: r for r in fresh}}
        results = [by_repo[repo] for repo in manifest.requested]
        finished = max(self._clock(), manifest.started_at)
        store.write_manifest(manifest.model_copy(update={"status": RunStatus.COMPLETED, "finished_at": finished}))
        report = self._build_report(manifest=manifest, results=results, finished_at=finished)
        if self._publish is not None:
            self._publish(report, store)
        return report

    async def _process(
        self, *, repo: str, collect: CollectFn, semaphore: asyncio.Semaphore, abort: _AbortState, store: RunStore
    ) -> RepoResult:
        entry = self._config.lookup(repo)
        if entry is None:
            result = self._error(repo=repo, entry=None, kind=ErrorKind.CONFIG, message=NOT_IN_CATALOG)
        elif not entry.repo.include:
            result = self._excluded(entry)
        else:
            async with semaphore:
                result = await self._collect_one(entry=entry, collect=collect, abort=abort)
        store.write_repo(result)
        return result

    async def _collect_one(self, *, entry: CatalogEntry, collect: CollectFn, abort: _AbortState) -> RepoResult:
        repo = entry.repo.full_name
        if abort.error is not None:
            message = f"collection stopped after a primary rate-limit abort: {abort.error}"
            return self._error(repo=repo, entry=entry, kind=ErrorKind.RATE_LIMITED, message=message)
        try:
            evidence = await collect(repo)
        except RateLimitedError as exc:
            abort.error = exc
            logger.error("primary rate limit: stopping collection (%s)", exc)
            return self._error(repo=repo, entry=entry, kind=ErrorKind.RATE_LIMITED, message=str(exc))
        if evidence.archived:
            return self._excluded(entry, reason=ARCHIVED_ON_GITHUB)
        return self._evaluate(entry=entry, evidence=evidence)

    def _evaluate(self, *, entry: CatalogEntry, evidence: RepoEvidence) -> RepoResult:
        now = self._clock()
        conformance = self._config.conformance
        explicit = entry.repo.scan_profile if isinstance(entry.repo.scan_profile, list) else None
        resolution = resolve_profiles(profiles=conformance.profiles, tree=evidence.tree, explicit=explicit)
        ctx = RuleContext(evidence=evidence, now=now)
        checks = [evaluate_rule(rule=rule, ctx=ctx, resolution=resolution) for rule in conformance.rules]
        scores, blocking = score_repo(checks)
        errors = list(evidence.errors.values()) + [f.error for f in evidence.files.values() if f.error is not None]
        fatal = "metadata" in evidence.errors
        signals = (
            revival_signals(evidence=evidence, now=now) if self._config.reporters.revival.enabled and not fatal else []
        )
        return RepoResult(
            repo=entry.repo.full_name,
            outcome=RepoOutcome.ERROR if fatal else RepoOutcome.EVALUATED,
            project_id=entry.project.id,
            canonical=entry.project.canonical_name,
            role=entry.repo.role,
            aliases=entry.project.aliases,
            ref=evidence.default_branch,
            commit_sha=evidence.head.sha if evidence.head else None,
            profiles=resolution.matched,
            profiles_status=resolution.status,
            collected_at=evidence.collected_at,
            checks=checks,
            collection_errors=errors,
            scores=scores,
            blocked=bool(blocking),
            blocking_failures=blocking,
            revival_signals=signals,
        )

    # ── result factories ──────────────────────────────────────────────────────

    def _error(self, *, repo: str, entry: CatalogEntry | None, kind: ErrorKind, message: str) -> RepoResult:
        return RepoResult(
            repo=repo,
            outcome=RepoOutcome.ERROR,
            project_id=entry.project.id if entry else None,
            canonical=entry.project.canonical_name if entry else None,
            role=entry.repo.role if entry else None,
            aliases=entry.project.aliases if entry else [],
            collection_errors=[CollectionError(kind=kind, message=message)],
        )

    def _excluded(self, entry: CatalogEntry, *, reason: str | None = None) -> RepoResult:
        return RepoResult(
            repo=entry.repo.full_name,
            outcome=RepoOutcome.EXCLUDED,
            project_id=entry.project.id,
            canonical=entry.project.canonical_name,
            role=entry.repo.role,
            aliases=entry.project.aliases,
            exclusion_reason=reason or entry.repo.exclusion_reason,
        )

    def _interrupted(self, repo: str) -> RepoResult:
        return self._error(
            repo=repo,
            entry=self._config.lookup(repo),
            kind=ErrorKind.INTERRUPTED,
            message="run interrupted before collection",
        )

    def _build_report(self, *, manifest: RunManifest, results: list[RepoResult], finished_at: datetime) -> ScanReport:
        scope = ScanScope(
            requested=list(manifest.requested),
            collected=[r.repo for r in results if r.collected_at is not None],
            evaluated=[r.repo for r in results if r.outcome is RepoOutcome.EVALUATED],
            excluded=[
                ExcludedRepo(repo=r.repo, reason=r.exclusion_reason or "")
                for r in results
                if r.outcome is RepoOutcome.EXCLUDED
            ],
            errors=[
                ScopeError(repo=r.repo, kind=r.collection_errors[0].kind, message=r.collection_errors[0].message)
                for r in results
                if r.outcome is RepoOutcome.ERROR
            ],
        )
        revival = self._config.reporters.revival
        return ScanReport(
            run_id=manifest.run_id,
            started_at=manifest.started_at,
            finished_at=finished_at,
            tool_version=manifest.tool_version,
            rules_version=manifest.rules_version,
            rules_sha256=manifest.rules_sha256,
            config_fingerprint=manifest.config_fingerprint,
            resumed=manifest.resumed,
            forced_config_change=manifest.forced_config_change,
            scope=scope,
            repos=results,
            aggregates=aggregate(results),
            revival_candidates=rank_candidates(results=results, top_n=revival.top_n) if revival.enabled else [],
        )
