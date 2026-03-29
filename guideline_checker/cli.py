"""CLI entry point for guideline-checker."""
from __future__ import annotations

import argparse
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "check":
        parser.print_help()
        return 0

    root: Path = args.root.resolve()
    instructions_dir: Path = args.instructions or root / ".github" / "instructions"

    if not instructions_dir.exists():
        print(f"[guideline-checker] Instructions directory not found: {instructions_dir}", file=sys.stderr)
        return 1

    results = run_checks(root=root, instructions_dir=instructions_dir)

    reporter = HtmlReporter()
    report_path: Path = args.output
    reporter.write(results=results, output_path=report_path, root=root)
    print(f"[guideline-checker] Report written to: {report_path}")

    if args.json:
        from guideline_checker.reporters.json_reporter import JsonReporter
        JsonReporter().write(results=results, output_path=args.json, root=root)
        print(f"[guideline-checker] JSON report written to: {args.json}")

    violation_count = sum(len(r.violations) for r in results)
    error_count = sum(
        sum(1 for v in r.violations if v.severity == "error") for r in results
    )
    warning_count = sum(
        sum(1 for v in r.violations if v.severity == "warning") for r in results
    )

    print(f"[guideline-checker] {violation_count} violation(s) found ({error_count} error(s), {warning_count} warning(s)).")

    if args.fail_on == "never":
        return 0
    if args.fail_on == "error" and error_count > 0:
        return 1
    if args.fail_on == "warning" and (error_count + warning_count) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
