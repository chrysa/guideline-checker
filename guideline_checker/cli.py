"""CLI entry point for guideline-checker."""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from guideline_checker.checker import run_checks
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
        default="error",
        dest="fail_on",
        help="Exit with code 1 if violations at this level or above are found.",
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

    if args.command != "check":
        parser.print_help()
        return 0

    root = args.root.resolve()
    use_all_sources = not args.no_multi_source
    instructions_dir = args.instructions or root / ".github" / "instructions"

    if args.no_multi_source and not instructions_dir.exists():
        print(f"[guideline-checker] Instructions directory not found: {instructions_dir}", file=sys.stderr)
        return 1

    if use_all_sources:
        print(
            "[guideline-checker] Multi-source mode: loading Copilot instructions, CLAUDE.md, AGENTS.md, "
            "and the guidelines/ YAML referential.",
        )

    # --diff: restrict to git-modified files
    diff_files: list[Path] | None = None
    if args.diff:
        diff_files = _get_diff_files(root)
        if diff_files is None:
            print(
                "[guideline-checker] --diff: git not available or not a git repo — checking all files.", file=sys.stderr
            )
        elif not diff_files:
            print("[guideline-checker] --diff: no modified files found, nothing to check.")
            return 0
        else:
            print(f"[guideline-checker] --diff: checking {len(diff_files)} modified file(s).")

    results = run_checks(
        root=root,
        instructions_dir=instructions_dir,
        diff_files=diff_files,
        all_sources=use_all_sources,
    )

    # ── Optional linter integration ──────────────────────────────────────────
    linter_results = []
    if args.linters is not None:
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
                print(f"[guideline-checker] Linter '{lr.linter}' unavailable: {lr.error}", file=sys.stderr)
            elif lr.error:
                print(f"[guideline-checker] Linter '{lr.linter}' error: {lr.error}", file=sys.stderr)
            else:
                print(f"[guideline-checker] Linter '{lr.linter}': {len(lr.violations)} violation(s).")

    reporter = HtmlReporter()
    report_path: Path = args.output
    reporter.write(results=results, output_path=report_path, root=root, linter_results=linter_results)
    print(f"[guideline-checker] Report written to: {report_path}")

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

    violation_count = sum(len(r.violations) for r in results)
    error_count = sum(sum(1 for v in r.violations if v.severity == "error") for r in results)
    warning_count = sum(sum(1 for v in r.violations if v.severity == "warning") for r in results)

    print(
        f"[guideline-checker] {violation_count} violation(s) found"
        f" ({error_count} error(s), {warning_count} warning(s))."
    )

    if args.fail_on == "never":
        return 0
    if args.fail_on == "error" and error_count > 0:
        return 1
    if args.fail_on == "warning" and (error_count + warning_count) > 0:
        return 1
    return 0


# ─── synthesize command ───────────────────────────────────────────────────────


def _cmd_synthesize(args: argparse.Namespace) -> int:
    """Run checks on each repo in a workspace and produce a synthesis report."""
    from guideline_checker.reporters.synthesis_html import SynthesisHtmlReporter

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
            repo_entries.append({"name": name, "path": repo_path, "skipped": True, "reason": "not a directory"})
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


if __name__ == "__main__":
    sys.exit(main())
