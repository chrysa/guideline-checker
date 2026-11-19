"""argparse declaration of ``fleet-scan`` — standard library only, importable without the ``scan`` extra."""

from __future__ import annotations

import argparse


def add_fleet_scan_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="scan_command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config-dir", default="config/fleet-scan", help="Config directory (default: config/fleet-scan)."
    )
    common.add_argument("--output-dir", default=None, help="Run output directory (default: reporters.yaml output_dir).")
    sub.add_parser("validate-config", parents=[common], help="Validate every config/fleet-scan/*.yaml file.")
    run = sub.add_parser("run", parents=[common], help="Scan a subset / explicit repos, or resume a run.")
    run.add_argument("--subset", default=None, help="Named subset from repos.yaml.")
    run.add_argument("--repos", nargs="+", default=None, help="Explicit owner/name list (space or comma separated).")
    run.add_argument("--resume", default=None, metavar="RUN_ID", help="Resume an interrupted run.")
    run.add_argument(
        "--force-config-change",
        action="store_true",
        default=False,
        help="Resume even though the config fingerprint changed (recorded in the report).",
    )
    report = sub.add_parser("report", parents=[common], help="Regenerate reports from a saved run.")
    report.add_argument("--run-id", required=True, metavar="RUN_ID", help="Run to report on.")
