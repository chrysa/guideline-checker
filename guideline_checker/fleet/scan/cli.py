"""``guideline-checker fleet-scan {validate-config, run, report}`` handlers.

Imported lazily by :mod:`guideline_checker.cli` so the default install (without the
``scan`` extra) keeps working. Exit codes: 0 success, 2 configuration/usage error or
refused resume, 130 interrupted (the run stays resumable).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from guideline_checker.fleet.scan.collect import CollectionNeeds, Collector
from guideline_checker.fleet.scan.config import EffectiveConfig, load_config
from guideline_checker.fleet.scan.engine import ScanEngine, resolve_scope
from guideline_checker.fleet.scan.errors import ConfigValidationError, ResumeRefusedError
from guideline_checker.fleet.scan.github import GithubClient
from guideline_checker.fleet.scan.models import ScanReport
from guideline_checker.fleet.scan.reporters.publisher import ReportPublisher
from guideline_checker.fleet.scan.run_store import RunStore

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_INTERRUPTED = 130

logger = logging.getLogger("guideline_checker.fleet.scan")


def run_fleet_scan(args: argparse.Namespace) -> int:
    handler = _HANDLERS.get(args.scan_command or "")
    if handler is None:
        print("usage: guideline-checker fleet-scan {validate-config,run,report} [options]", file=sys.stderr)
        return EXIT_CONFIG
    try:
        return handler(args)
    except ConfigValidationError as exc:
        print(f"[fleet-scan] {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except ResumeRefusedError as exc:
        print(f"[fleet-scan] resume refused: {exc}", file=sys.stderr)
        return EXIT_CONFIG


def _cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config_dir))
    conformance = config.conformance
    print(
        f"[fleet-scan] config OK: {len(conformance.rules)} rule(s), {len(conformance.profiles)} profile(s), "
        f"{len(config.repos.projects)} project(s), {len(config.catalog)} repo(s), "
        f"{len(config.repos.subsets)} subset(s); rules {conformance.rules_version}; "
        f"fingerprint {config.config_fingerprint[:12]}"
    )
    for warning in config.warnings:
        print(f"[fleet-scan] warning: {warning}", file=sys.stderr)
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config_dir))
    _configure_logging(config)
    token = _token(config)
    publisher = _publisher(config)
    engine = ScanEngine(
        config=config, output_dir=_output_dir(config=config, override=args.output_dir), publish=publisher
    )
    requested = _requested(config=config, args=args)
    try:
        report = asyncio.run(_scan(engine=engine, config=config, token=token, args=args, requested=requested))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"[fleet-scan] interrupted; resume with: --resume {engine.run_id}", file=sys.stderr)
        return EXIT_INTERRUPTED
    _print_outcome(report=report, publisher=publisher)
    return EXIT_OK


def _cmd_report(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config_dir))
    publisher = _publisher(config)
    engine = ScanEngine(config=config, output_dir=_output_dir(config=config, override=args.output_dir))
    report = engine.report(run_id=args.run_id)
    publisher(report, RunStore(output_dir=_output_dir(config=config, override=args.output_dir), run_id=args.run_id))
    _print_outcome(report=report, publisher=publisher)
    return EXIT_OK


async def _scan(
    *,
    engine: ScanEngine,
    config: EffectiveConfig,
    token: str | None,
    args: argparse.Namespace,
    requested: list[str] | None,
) -> ScanReport:
    needs = CollectionNeeds.from_rules(
        rules=config.conformance.rules,
        profiles=config.conformance.profiles,
        revival=config.reporters.revival.enabled,
    )
    async with GithubClient(config=config.github, token=token) as client:
        collector = Collector(client=client, needs=needs, max_file_bytes=config.github.content.max_file_bytes)
        if requested is None:
            return await engine.resume(
                run_id=args.resume, collect=collector.collect, force_config_change=args.force_config_change
            )
        return await engine.run(requested=requested, subset=args.subset, collect=collector.collect)


def _requested(*, config: EffectiveConfig, args: argparse.Namespace) -> list[str] | None:
    """The scope of a new run, or ``None`` for a resume (which reuses the original scope)."""
    repos = _split_repos(args.repos)
    if args.resume is not None:
        if args.subset or repos:
            raise ConfigValidationError(["--resume takes the scope of the original run; drop --subset/--repos"])
        return None
    requested = resolve_scope(config=config, subset=args.subset, repos=repos)
    if not requested:
        raise ConfigValidationError(["empty scope: nothing to scan"])
    return requested


def _token(config: EffectiveConfig) -> str | None:
    auth = config.github.auth
    token = os.environ.get(auth.token_env) or None
    if token is None and auth.required:
        raise ConfigValidationError([f"environment variable {auth.token_env} is required but not set"])
    if token is None:
        logger.warning("%s not set: unauthenticated requests (low rate limit, public repos only)", auth.token_env)
    return token


def _publisher(config: EffectiveConfig) -> ReportPublisher:
    return ReportPublisher(config=config.reporters, history_path=Path(config.reporters.history.index))


def _output_dir(*, config: EffectiveConfig, override: str | None) -> Path:
    return Path(override) if override else Path(config.reporters.output_dir)


def _split_repos(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    return [name for chunk in raw for name in chunk.split(",") if name.strip()]


def _configure_logging(config: EffectiveConfig) -> None:
    logging.basicConfig(
        level=config.github.logging.level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )


def _print_outcome(*, report: ScanReport, publisher: ReportPublisher) -> None:
    if publisher.summary:
        print(publisher.summary)
    for path in publisher.written:
        print(f"[fleet-scan] wrote {path}")


_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "validate-config": _cmd_validate,
    "run": _cmd_run,
    "report": _cmd_report,
}
