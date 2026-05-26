"""CLI entry point for guideline-checker."""

from __future__ import annotations

import argparse
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
    return parser


def _get_diff_files(root: Path) -> list[Path] | None:
    """Return list of files modified vs HEAD (staged + unstaged).

    Returns None if git is unavailable or the directory is not a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=root,
            timeout=10,
        )
        if result.returncode != 0:
            # Fallback: list staged files only (new repo with no HEAD)
            result = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
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
        print("[guideline-checker] Multi-source mode: loading Copilot instructions, CLAUDE.md, AGENTS.md.")

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

    reporter = HtmlReporter()
    report_path: Path = args.output
    reporter.write(results=results, output_path=report_path, root=root)
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


if __name__ == "__main__":
    sys.exit(main())
