"""CLI entry point for guideline-checker."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from guideline_checker.checker import RuleResult, run_checks
from guideline_checker.gh_client import GhClient
from guideline_checker.reporters.html import HtmlReporter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guideline-checker",
        description="Check project compliance against Copilot instruction rules.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── init subcommand ──────────────────────────────────────────────────────
    init_cmd = sub.add_parser("init", help="Scaffold default instruction files in a project.")
    init_cmd.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory).",
    )
    init_cmd.add_argument(
        "--instructions",
        type=Path,
        default=None,
        help="Target instructions directory (default: <root>/.github/instructions).",
    )
    init_cmd.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing instruction files.",
    )

    # ── check subcommand ─────────────────────────────────────────────────────
    check_cmd = sub.add_parser("check", help="Run compliance checks and generate report.")
    check_cmd.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Project root directory (default: current directory).",
    )
    check_cmd.add_argument(
        "--instructions",
        type=Path,
        default=None,
        help="Path to instructions directory (default: <root>/.github/instructions).",
    )
    check_cmd.add_argument(
        "--output",
        type=Path,
        default=Path("guideline-report.html"),
        help="Output HTML report path (default: guideline-report.html).",
    )
    check_cmd.add_argument(
        "--fail-on",
        choices=["error", "warning", "never"],
        default=None,
        dest="fail_on",
        help=(
            "Exit with code 1 if violations at this level or above are found "
            "(default: error, or the [tool.guideline-checker] config value)."
        ),
    )
    check_cmd.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Also write a JSON report to this path.",
    )
    check_cmd.add_argument(
        "--sarif",
        type=Path,
        default=None,
        help="Also write a SARIF 2.1.0 report (GitHub Code Scanning compatible).",
    )
    check_cmd.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Also write a Markdown report to this path.",
    )
    check_cmd.add_argument(
        "--diff",
        action="store_true",
        default=False,
        help="Only check files modified in the current git working tree (git diff --name-only HEAD).",
    )
    check_cmd.add_argument(
        "--no-multi-source",
        action="store_true",
        default=False,
        dest="no_multi_source",
        help=(
            "Only load *.instructions.md from --instructions dir "
            "(disable CLAUDE.md, copilot-instructions.md, AGENTS.md discovery)."
        ),
    )
    check_cmd.add_argument(
        "--linters",
        nargs="*",
        default=None,
        metavar="LINTER",
        help=(
            "Run external linters and include results in the report. "
            "Pass specific linters (ruff mypy eslint) or no argument to auto-detect. "
            "Example: --linters ruff mypy"
        ),
    )
    check_cmd.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help=(
            "Skip files matching this glob (relative to --root). Repeatable, and each value "
            "may be comma-separated. A bare directory name excludes everything beneath it. "
            "Examples: --exclude tests --exclude 'scripts/**,**/*.md'"
        ),
    )
    check_cmd.add_argument(
        "--max-file-size",
        type=int,
        default=None,
        dest="max_file_size",
        metavar="BYTES",
        help=(
            "Maximum size in bytes for a file to be scanned (default: 204800 = 200 KiB). "
            "Larger files are skipped as generated/compiled artefacts. "
            "Also settable via the GUIDELINE_MAX_FILE_SIZE env var."
        ),
    )
    check_cmd.add_argument(
        "--baseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Suppress violations recorded in this baseline file; --fail-on applies only to "
            "new violations. Adopt the checker on a legacy repo without failing on its backlog."
        ),
    )
    check_cmd.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        dest="write_baseline",
        metavar="PATH",
        help=(
            "Snapshot the current violations to this baseline file and exit 0 (no gate). "
            "Commit the file, then run with --baseline to fail only on new violations."
        ),
    )
    check_cmd.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Apply autofixes to the working tree for violations on rules that declare a fix.",
    )
    check_cmd.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="fix_dry_run",
        help="With --fix, print a unified diff of the changes and write nothing.",
    )

    _add_fix_subcommand(sub)

    # ── synthesize subcommand ────────────────────────────────────────────────
    syn_cmd = sub.add_parser(
        "synthesize",
        help="Generate a multi-repo synthesis report from a workspace directory.",
    )
    syn_cmd.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Workspace directory containing multiple project repos.",
    )
    syn_cmd.add_argument(
        "--repos",
        nargs="*",
        default=None,
        metavar="REPO",
        help=("Explicit list of repo subdirectory names to include. When omitted, all subdirectories are processed."),
    )
    syn_cmd.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Synthesis HTML output path (default: <workspace>/guideline-synthesis.html).",
    )
    syn_cmd.add_argument(
        "--linters",
        nargs="*",
        default=None,
        metavar="LINTER",
        help=(
            "Run external linters on each repo. Pass specific linters (ruff mypy eslint) or no argument to auto-detect."
        ),
    )
    syn_cmd.add_argument(
        "--no-multi-source",
        action="store_true",
        default=False,
        dest="no_multi_source",
        help="Only load *.instructions.md (disable CLAUDE.md, copilot-instructions.md, AGENTS.md).",
    )
    syn_cmd.add_argument(
        "--instructions",
        type=Path,
        default=None,
        help=("Shared instructions directory to use for all repos (overrides per-repo .github/instructions/)."),
    )
    syn_cmd.add_argument(
        "--source",
        choices=["local", "origin"],
        default="local",
        help="Audit local working trees (default) or origin/<default-branch> via the gh API.",
    )
    syn_cmd.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to repos.yml. Required when --source origin.",
    )
    syn_cmd.add_argument(
        "--shared-standards",
        type=Path,
        default=None,
        dest="shared_standards",
        help="Path to a shared-standards checkout (canonical STANDARDS + LICENSE template); required for origin.",
    )
    syn_cmd.add_argument(
        "--category",
        choices=["all", "distribution"],
        default="all",
        help="Restrict origin audit to a check category (default: all = distribution).",
    )
    syn_cmd.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Open one PR per repo to remediate fixable distribution drift (origin source only). Never merges.",
    )
    syn_cmd.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        help="With --fix: print the PRs that would be opened without creating them.",
    )

    # ── web subcommand ───────────────────────────────────────────────────────
    web_cmd = sub.add_parser(
        "web",
        help="Launch the FastAPI compliance dashboard (requires the 'web' extra).",
    )
    web_cmd.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Directory to scan and serve results for (default: current directory).",
    )
    web_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1, loopback only).",
    )
    web_cmd.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080).",
    )
    web_cmd.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on code changes (development only).",
    )

    # ── central subcommand ───────────────────────────────────────────────────
    central_cmd = sub.add_parser(
        "central",
        help="Launch the central aggregation server (requires the 'web' extra).",
    )
    central_cmd.add_argument(
        "--store",
        type=Path,
        default=Path("./central-store"),
        help="Directory backing the report store (default: ./central-store).",
    )
    central_cmd.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1, loopback only).",
    )
    central_cmd.add_argument(
        "--port",
        type=int,
        default=8090,
        help="Port to listen on (default: 8090).",
    )
    central_cmd.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on code changes (development only).",
    )

    # ── push subcommand ──────────────────────────────────────────────────────
    push_cmd = sub.add_parser(
        "push",
        help="Push a JSON compliance report to a central server.",
    )
    push_cmd.add_argument(
        "--server",
        required=True,
        help="Base URL of the central server, e.g. https://guidelines.example.com",
    )
    push_cmd.add_argument(
        "--report",
        type=Path,
        default=Path("guideline-report.json"),
        help="Path to the JSON report produced by 'check --json' (default: guideline-report.json).",
    )
    push_cmd.add_argument(
        "--repo",
        default=None,
        help="Repo identifier ([A-Za-z0-9._-]). Defaults to the git remote / directory name.",
    )
    push_cmd.add_argument(
        "--commit",
        default=None,
        help="Commit SHA to record (default: current git HEAD, if available).",
    )
    push_cmd.add_argument(
        "--branch",
        default=None,
        help="Branch name to record (default: current git branch, if available).",
    )
    push_cmd.add_argument(
        "--api-key",
        default=None,
        help="API key sent as X-Api-Key (default: API_KEY env var).",
    )

    return parser


def _get_diff_files(root: Path) -> list[Path] | None:
    """Return list of files modified vs HEAD (staged + unstaged).

    Returns None if git is unavailable or the directory is not a git repo.
    """
    git_path = shutil.which("git")
    if git_path is None:
        return None
    try:
        result = subprocess.run(
            [git_path, "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=10,
        )
        if result.returncode != 0:
            # Fallback: list staged files only (new repo with no HEAD)
            result = subprocess.run(
                [git_path, "diff", "--name-only", "--cached"],
                capture_output=True,
                text=True,
                cwd=root,
                timeout=10,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    return [root / line for line in result.stdout.splitlines() if line.strip()]


def _resolve_diff_files(args: argparse.Namespace, root: Path) -> list[Path] | None:
    """Resolve the list of diff files from ``--diff``; returns None to check all files."""
    if not args.diff:
        return None
    diff_files = _get_diff_files(root)
    if diff_files is None:
        print(
            "[guideline-checker] --diff: git not available or not a git repo — checking all files.",
            file=sys.stderr,
        )
        return None
    if not diff_files:
        print("[guideline-checker] --diff: no modified files found, nothing to check.")
        return []
    print(f"[guideline-checker] --diff: checking {len(diff_files)} modified file(s).")
    return diff_files


def _run_linters_for_check(args: argparse.Namespace, root: Path) -> list:  # type: ignore[type-arg]
    """Run linters when ``--linters`` is passed; log each result."""
    if args.linters is None:
        return []
    from guideline_checker.linters import run_linters

    linter_names: list[str] | None = args.linters if args.linters else None
    print(
        "[guideline-checker] Running linters"
        + (f": {', '.join(linter_names)}" if linter_names else " (auto-detect)")
        + " ..."
    )
    linter_results = run_linters(root, linters=linter_names)
    for lr in linter_results:
        if not lr.available:
            print(
                f"[guideline-checker] Linter '{lr.linter}' unavailable: {lr.error}",
                file=sys.stderr,
            )
        elif lr.error:
            print(
                f"[guideline-checker] Linter '{lr.linter}' error: {lr.error}",
                file=sys.stderr,
            )
        else:
            print(f"[guideline-checker] Linter '{lr.linter}': {len(lr.violations)} violation(s).")
    return linter_results


def _write_extra_reports(args: argparse.Namespace, results: list, root: Path) -> None:  # type: ignore[type-arg]
    """Write JSON / SARIF / Markdown reports when the corresponding flags are set."""
    if args.json:
        from guideline_checker.reporters.json_reporter import JsonReporter

        JsonReporter().write(results=results, output_path=args.json, root=root)
        print(f"[guideline-checker] JSON report written to: {args.json}")

    if args.sarif:
        from guideline_checker.reporters.sarif import SarifReporter

        SarifReporter().write(results=results, output_path=args.sarif, root=root)
        print(f"[guideline-checker] SARIF report written to: {args.sarif}")

    if args.markdown:
        from guideline_checker.reporters.markdown import MarkdownReporter

        MarkdownReporter().write(results=results, output_path=args.markdown, root=root)
        print(f"[guideline-checker] Markdown report written to: {args.markdown}")


def _exit_code_for_check(args: argparse.Namespace, error_count: int, warning_count: int) -> int:
    """Return the process exit code based on ``--fail-on`` and the violation counts."""
    if args.fail_on == "never":
        return 0
    if args.fail_on == "error" and error_count > 0:
        return 1
    if args.fail_on == "warning" and (error_count + warning_count) > 0:
        return 1
    return 0


def _add_fix_subcommand(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``fix`` subcommand — check + autofix, minus the reporting surface."""
    fix_cmd = sub.add_parser(
        "fix",
        help="Apply autofixes to the working tree for violations on rules that declare a fix.",
    )
    fix_cmd.add_argument("--root", type=Path, default=Path("."), help="Project root (default: current directory).")
    fix_cmd.add_argument("--instructions", type=Path, default=None, help="Instructions directory override.")
    fix_cmd.add_argument(
        "--no-multi-source",
        action="store_true",
        default=False,
        dest="no_multi_source",
        help="Only load *.instructions.md from --instructions.",
    )
    fix_cmd.add_argument(
        "--exclude", action="append", default=None, metavar="GLOB", help="Skip files matching this glob."
    )
    fix_cmd.add_argument(
        "--max-file-size",
        type=int,
        default=None,
        dest="max_file_size",
        metavar="BYTES",
        help="Max scannable file size.",
    )
    fix_cmd.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        dest="fix_dry_run",
        help="Print a unified diff of the changes and write nothing.",
    )
    # Reporting/gating knobs the check flow reads but the fix path does not surface.
    fix_cmd.set_defaults(fix=True, fail_on=None, diff=False, baseline=None, write_baseline=None, linters=None)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        from guideline_checker.init_cmd import run_init

        root: Path = args.root.resolve()
        instructions_dir: Path | None = args.instructions
        return run_init(root=root, instructions_dir=instructions_dir, force=args.force)

    if args.command == "synthesize":
        return _cmd_synthesize(args)

    if args.command == "web":
        return _cmd_web(args)

    if args.command == "central":
        return _cmd_central(args)

    if args.command == "push":
        return _cmd_push(args)

    if args.command not in ("check", "fix"):
        parser.print_help()
        return 0

    return _cmd_check(args)


def _cmd_check(args: argparse.Namespace) -> int:
    _apply_config(args)
    root: Path = args.root.resolve()
    use_all_sources = not args.no_multi_source
    instructions_dir: Path = args.instructions or root / ".github" / "instructions"

    if args.no_multi_source and not instructions_dir.exists():
        print(
            f"[guideline-checker] Instructions directory not found: {instructions_dir}",
            file=sys.stderr,
        )
        return 1

    if use_all_sources:
        print(
            "[guideline-checker] Multi-source mode: loading Copilot instructions, CLAUDE.md, AGENTS.md, "
            "and the guidelines/ YAML referential.",
        )

    diff_files = _resolve_diff_files(args, root)
    if diff_files is not None and len(diff_files) == 0:
        return 0

    results = run_checks(
        root=root,
        instructions_dir=instructions_dir,
        diff_files=diff_files,
        all_sources=use_all_sources,
        exclude=args.exclude,
        max_file_size=args.max_file_size,
    )

    if getattr(args, "fix", False):
        return _run_autofix(args, results, root)
    if args.write_baseline is not None:
        return _write_baseline_and_exit(args, results, root)
    if args.baseline is not None:
        results = _apply_baseline(args, results, root)

    return _report_and_gate(args, results, root)


def _scan(args: argparse.Namespace, root: Path) -> list[RuleResult]:
    """Run a full (non-diff) scan with the current args — used to re-check after autofix."""
    return run_checks(
        root=root,
        instructions_dir=args.instructions or root / ".github" / "instructions",
        all_sources=not args.no_multi_source,
        exclude=args.exclude,
        max_file_size=args.max_file_size,
    )


def _severity_counts(results: list[RuleResult]) -> tuple[int, int]:
    errors = sum(sum(v.severity == "error" for v in r.violations) for r in results)
    warnings = sum(sum(v.severity == "warning" for v in r.violations) for r in results)
    return errors, warnings


def _run_autofix(args: argparse.Namespace, results: list[RuleResult], root: Path) -> int:
    """Apply (or preview) local autofixes, then gate on the post-fix state (ADR D-0007)."""
    from guideline_checker.autofix import apply_local_fixes
    from guideline_checker.guidelines import load_yaml_guidelines

    rule_fixes = {rule: fix for instr in load_yaml_guidelines(root) for rule, fix in instr.rule_fixes.items()}
    report = apply_local_fixes(results, root, rule_fixes, dry_run=args.fix_dry_run)

    if args.fix_dry_run:
        if report.diff:
            print(report.diff, end="")
        else:
            print("[guideline-checker] No autofixable violations to preview.")
        return _exit_code_for_check(args, *_severity_counts(results))

    print(
        f"[guideline-checker] Autofix: fixed {report.fixed_count} violation(s) "
        f"across {len(report.changed_files)} file(s)."
    )
    remaining = _scan(args, root)
    errors, warnings = _severity_counts(remaining)
    print(f"[guideline-checker] {errors + warnings} violation(s) remain after autofix.")
    return _exit_code_for_check(args, errors, warnings)


def _apply_config(args: argparse.Namespace) -> None:
    """Fill unset check args from [tool.guideline-checker]; CLI > env > config > default."""
    from guideline_checker.config import load_config

    root = args.root.resolve()
    config = load_config(root)
    for warning in config.warnings:
        print(f"[guideline-checker] config: {warning} ignored.", file=sys.stderr)

    values = config.values
    if args.fail_on is None:
        args.fail_on = values.get("fail_on", "error")
    if args.exclude is None and "exclude" in values:
        args.exclude = values["exclude"]
    if args.linters is None and "linters" in values:
        args.linters = values["linters"]
    if args.baseline is None and "baseline" in values:
        base = Path(str(values["baseline"]))
        args.baseline = base if base.is_absolute() else root / base
    if args.max_file_size is None:
        args.max_file_size = _env_or_config_max_size(values)


def _env_or_config_max_size(values: dict[str, object]) -> int | None:
    """Resolve max file size from the env var (wins) then the config value."""
    env = os.environ.get("GUIDELINE_MAX_FILE_SIZE")
    if env is not None and env.isdigit():
        return int(env)
    raw = values.get("max_file_size")
    return raw if isinstance(raw, int) else None


def _apply_baseline(args: argparse.Namespace, results: list[RuleResult], root: Path) -> list[RuleResult]:
    """Drop baselined violations; report how many were suppressed vs newly introduced."""
    from guideline_checker.baseline import apply_baseline, load_baseline

    outcome = apply_baseline(results, load_baseline(args.baseline), root)
    print(f"[guideline-checker] Baseline: {outcome.baselined_count} violation(s) suppressed, {outcome.new_count} new.")
    return outcome.results


def _report_and_gate(args: argparse.Namespace, results: list[RuleResult], root: Path) -> int:
    """Write reports for ``results`` and return the exit code per ``--fail-on``."""
    linter_results = _run_linters_for_check(args, root)

    reporter = HtmlReporter()
    report_path: Path = args.output
    reporter.write(
        results=results,
        output_path=report_path,
        root=root,
        linter_results=linter_results,
    )
    print(f"[guideline-checker] Report written to: {report_path}")

    _write_extra_reports(args, results, root)

    violation_count = sum(len(r.violations) for r in results)
    error_count = sum(sum(1 for v in r.violations if v.severity == "error") for r in results)
    warning_count = sum(sum(1 for v in r.violations if v.severity == "warning") for r in results)

    print(
        f"[guideline-checker] {violation_count} violation(s) found"
        f" ({error_count} error(s), {warning_count} warning(s))."
    )

    return _exit_code_for_check(args, error_count, warning_count)


def _write_baseline_and_exit(args: argparse.Namespace, results: list[RuleResult], root: Path) -> int:
    """Snapshot current violations to the baseline file and exit without gating."""
    from guideline_checker.baseline import write_baseline

    count = write_baseline(results, root, args.write_baseline)
    print(f"[guideline-checker] Baseline written to {args.write_baseline} ({count} fingerprint(s)).")
    return 0


# ─── synthesize command ───────────────────────────────────────────────────────


def _cmd_synthesize(args: argparse.Namespace) -> int:
    """Run checks on each repo in a workspace and produce a synthesis report."""
    from guideline_checker.reporters.synthesis_html import SynthesisHtmlReporter

    if getattr(args, "source", "local") == "origin":
        return _cmd_synthesize_origin(args)

    workspace: Path = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"[guideline-checker] Workspace not found: {workspace}", file=sys.stderr)
        return 1

    output: Path = args.output or workspace / "guideline-synthesis.html"
    use_all_sources = not args.no_multi_source
    shared_instructions: Path | None = args.instructions

    # Discover repos
    if args.repos:
        repo_names: list[str] = args.repos
    else:
        repo_names = sorted(d.name for d in workspace.iterdir() if d.is_dir() and not d.name.startswith("."))

    print(f"[guideline-checker] Synthesizing {len(repo_names)} repo(s) in {workspace} ...")

    linter_names: list[str] | None = None
    run_linters_flag = args.linters is not None
    if run_linters_flag and args.linters:
        linter_names = args.linters

    repo_entries = []
    for name in repo_names:
        repo_path = workspace / name
        if not repo_path.is_dir():
            print(f"[guideline-checker]   SKIP {name} (not a directory)")
            repo_entries.append(
                {
                    "name": name,
                    "path": repo_path,
                    "skipped": True,
                    "reason": "not a directory",
                }
            )
            continue

        instructions_dir = shared_instructions or (repo_path / ".github" / "instructions")

        print(f"[guideline-checker]   Checking {name} ...", end=" ", flush=True)
        try:
            results = run_checks(
                root=repo_path,
                instructions_dir=instructions_dir,
                all_sources=use_all_sources,
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            repo_entries.append({"name": name, "path": repo_path, "skipped": True, "reason": str(exc)})
            continue

        linter_results = []
        if run_linters_flag:
            from guideline_checker.linters import run_linters

            linter_results = run_linters(repo_path, linters=linter_names)

        # Write per-repo HTML
        per_repo_report = repo_path / "guideline-report.html"
        HtmlReporter().write(
            results=results,
            output_path=per_repo_report,
            root=repo_path,
            linter_results=linter_results,
        )

        errors = sum(sum(1 for v in r.violations if v.severity == "error") for r in results)
        warnings = sum(sum(1 for v in r.violations if v.severity == "warning") for r in results)
        linter_errors = sum(sum(1 for v in lr.violations if v.severity == "error") for lr in linter_results)
        linter_warnings = sum(sum(1 for v in lr.violations if v.severity == "warning") for lr in linter_results)
        print(f"errors={errors + linter_errors} warnings={warnings + linter_warnings}")
        repo_entries.append(
            {
                "name": name,
                "path": repo_path,
                "skipped": False,
                "results": results,
                "linter_results": linter_results,
                "report_path": per_repo_report,
                "errors": errors + linter_errors,
                "warnings": warnings + linter_warnings,
            }
        )

    SynthesisHtmlReporter().write(
        workspace=workspace,
        repo_entries=repo_entries,
        output_path=output,
    )
    print(f"[guideline-checker] Synthesis report written to: {output}")
    return 0


def _cmd_synthesize_origin(args: argparse.Namespace) -> int:
    """Audit origin/<default> for every dev repo in the manifest; write a synthesis report."""
    from guideline_checker.distribution import load_expectations
    from guideline_checker.manifest import load_manifest
    from guideline_checker.origin_audit import run_origin_audit
    from guideline_checker.reporters.synthesis_html import SynthesisHtmlReporter

    if args.manifest is None or args.shared_standards is None:
        print(
            "[guideline-checker] --source origin requires --manifest and --shared-standards",
            file=sys.stderr,
        )
        return 2
    client = GhClient()
    if not client.available():
        print(
            "[guideline-checker] gh CLI not found — required for --source origin",
            file=sys.stderr,
        )
        return 2

    targets = load_manifest(args.manifest)
    expected = load_expectations(args.shared_standards)
    print(f"[guideline-checker] Auditing {len(targets)} dev repo(s) on origin ...")
    audited = run_origin_audit(targets, expected, client)

    if getattr(args, "fix", False):
        from guideline_checker.fixers import apply_fix

        for r in audited:
            if r.fetch_failed:
                continue
            url = apply_fix("chrysa", r.name, r.results[0], expected, client, args.dry_run)
            if url == "DRY-RUN":
                print(f"[guideline-checker]   {r.name}: would open a distribution-fix PR")
            elif url:
                print(f"[guideline-checker]   {r.name}: PR {url}")

    workspace: Path = args.workspace.resolve()
    output: Path = args.output or workspace / "guideline-synthesis.html"
    repo_entries = [
        {
            "name": r.name,
            "path": workspace / r.name,
            "skipped": False,
            "results": r.results,
            "linter_results": [],
            "report_path": None,
            "errors": r.errors,
            "warnings": r.warnings,
        }
        for r in audited
    ]
    SynthesisHtmlReporter().write(workspace=workspace, repo_entries=repo_entries, output_path=output)
    total_errors = sum(r.errors for r in audited)
    print(f"[guideline-checker] Origin synthesis written to: {output} (errors={total_errors})")
    return 0


# ─── web command ──────────────────────────────────────────────────────────────

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _auth_is_open() -> bool:
    """Return True when the dashboard API is effectively unauthenticated.

    Mirrors the env contract honoured by guideline_checker.web.auth at request time.
    """
    if os.environ.get("AUTH_ENABLED", "true").lower() == "false":
        return True
    mode = os.environ.get("AUTH_MODE", "api_key").lower()
    if mode == "disabled":
        return True
    return mode == "api_key" and not os.environ.get("API_KEY", "")


def _cmd_web(args: argparse.Namespace) -> int:
    """Launch the FastAPI dashboard, scanning ``--root``."""
    # web.app reads SCAN_ROOT at import time, so it must be set before any import.
    root: Path = args.root.resolve()
    os.environ["SCAN_ROOT"] = str(root)

    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError:
        print(
            "[guideline-checker] The web dashboard needs the 'web' extra. "
            "Install it with: pip install 'guideline-checker[web]'",
            file=sys.stderr,
        )
        return 1

    if args.host not in _LOOPBACK_HOSTS and _auth_is_open():
        print(
            f"[guideline-checker] WARNING: binding {args.host} with authentication disabled — "
            "the dashboard API is exposed without protection. Set AUTH_MODE/API_KEY (see .env.example).",
            file=sys.stderr,
        )

    print(f"[guideline-checker] Serving dashboard for {root} at http://{args.host}:{args.port} ...")
    if args.reload:
        # reload needs an import string so the worker subprocess re-imports the app.
        uvicorn.run("guideline_checker.web.app:app", host=args.host, port=args.port, reload=True)
    else:
        app = importlib.import_module("guideline_checker.web.app").app
        uvicorn.run(app, host=args.host, port=args.port)
    return 0


# ─── central command ──────────────────────────────────────────────────────────


def _cmd_central(args: argparse.Namespace) -> int:
    """Launch the central aggregation server, persisting reports under ``--store``."""
    store: Path = args.store.resolve()
    os.environ["CENTRAL_STORE"] = str(store)

    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError:
        print(
            "[guideline-checker] The central server needs the 'web' extra. "
            "Install it with: pip install 'guideline-checker[web]'",
            file=sys.stderr,
        )
        return 1

    if args.host not in _LOOPBACK_HOSTS and _auth_is_open():
        print(
            f"[guideline-checker] WARNING: binding {args.host} with authentication disabled — "
            "the ingest API is exposed without protection. Set AUTH_MODE/API_KEY (see .env.example).",
            file=sys.stderr,
        )

    print(f"[guideline-checker] Serving central server (store: {store}) at http://{args.host}:{args.port} ...")
    if args.reload:
        uvicorn.run(
            "guideline_checker.web.central:central_app",
            host=args.host,
            port=args.port,
            reload=True,
        )
    else:
        central_app = importlib.import_module("guideline_checker.web.central").central_app
        uvicorn.run(central_app, host=args.host, port=args.port)
    return 0


# ─── push command ─────────────────────────────────────────────────────────────

_REPO_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _git_output(git_args: list[str]) -> str | None:
    """Return stripped stdout of a git command, or None if git/repo is unavailable."""
    git_path = shutil.which("git")
    if git_path is None:
        return None
    try:
        proc = subprocess.run(
            [git_path, *git_args],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _default_repo_name() -> str:
    """Infer a repo name from the git origin remote, falling back to the cwd name."""
    url = _git_output(["remote", "get-url", "origin"])
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    return Path.cwd().name


def _slug_repo(name: str) -> str:
    """Reduce a repo name to the central server's allowed charset."""
    return _REPO_SLUG_RE.sub("-", name).strip("-")


def _cmd_push(args: argparse.Namespace) -> int:
    """Push a ``check --json`` report to a central server's ingest endpoint."""
    import json
    import urllib.error
    import urllib.request

    report_path: Path = args.report
    if not report_path.is_file():
        print(f"[guideline-checker] Report not found: {report_path}", file=sys.stderr)
        return 1
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[guideline-checker] Cannot read report: {exc}", file=sys.stderr)
        return 1

    summary = report.get("summary") if isinstance(report, dict) else None
    if not isinstance(summary, dict):
        print(
            "[guideline-checker] Report has no 'summary' block — is this a 'check --json' output?",
            file=sys.stderr,
        )
        return 1

    repo = _slug_repo(args.repo or _default_repo_name())
    if not repo:
        print(
            "[guideline-checker] Could not determine a valid repo name; pass --repo.",
            file=sys.stderr,
        )
        return 1

    url = args.server.rstrip("/") + "/api/ingest"
    if not url.startswith(("http://", "https://")):
        print("[guideline-checker] --server must be an http(s) URL.", file=sys.stderr)
        return 1

    payload = {
        "repo": repo,
        "summary": summary,
        "commit": args.commit or _git_output(["rev-parse", "HEAD"]),
        "branch": args.branch or _git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
        "generated_at": report.get("generated_at"),
        "report": report,
    }
    headers = {"Content-Type": "application/json"}
    api_key = args.api_key or os.environ.get("API_KEY", "")
    if api_key:
        headers["X-Api-Key"] = api_key

    request = urllib.request.Request(  # noqa: S310 — scheme validated to http(s) above
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310 — scheme validated above
            status_code = resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        print(
            f"[guideline-checker] Push rejected (HTTP {exc.code}): {detail}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(f"[guideline-checker] Push failed: {exc.reason}", file=sys.stderr)
        return 1

    print(f"[guideline-checker] Pushed report for '{repo}' to {url} (HTTP {status_code}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
