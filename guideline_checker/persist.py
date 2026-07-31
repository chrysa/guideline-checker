"""Persist a validated detector onto a YAML rule — the workshop's write step.

Once a proposal is proven in the sandbox and the user validates it, the detector
is written onto its rule in ``guidelines/<dim>/*.yml``. ``dry_run`` returns the
unified diff and writes nothing; applying it re-parses through the real loader on
the next scan, so a rule armed here is enforced for real. Leading ``#`` comments
on the target file are preserved across the YAML round-trip.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from guideline_checker.loader import RuleDetector

if TYPE_CHECKING:
    from guideline_checker.interpret import DerivedRule

# Interpret-once writes its output here: a per-repo derived cache (ADR D-0016),
# regenerable from host prose, versioned in the host repo — not shipped content.
_DERIVED_PATH = ("guidelines", "derived", "derived.yml")
_DERIVED_HEADER = (
    "# Derived cache — interpret-once output (ADR D-0016). Each rule was proposed\n"
    "# from this repo's own prose, proven in the sandbox, and classified into a kind.\n"
    "# Regenerable from the workshop; do not hand-edit — re-run interpret to refresh.\n"
)
# kind → an existing category in categories.yml (the derived cache reuses the
# shared category vocabulary; the loader rejects an unknown one).
_KIND_CATEGORY = {"content-scan": "security", "ast-structure": "correctness"}

_PATTERN_FIELDS = (
    ("forbid", "forbid"),
    ("forbid_regex", "forbid_regex"),
    ("file_regex", "file_regex"),
    ("require_regex", "require_regex"),
    ("ast_checks", "ast"),
    ("scan_checks", "scan"),
)


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of arming a rule: which file, the diff, and whether it was written."""

    rule_id: str
    file: Path
    diff: str
    written: bool


def apply_detector(
    root: Path,
    rule_id: str,
    detector: RuleDetector,
    *,
    dry_run: bool = True,
    provenance: str | None = None,
) -> ApplyResult:
    """Write ``detector`` onto rule ``rule_id`` in its referential file.

    Raises ``KeyError`` if no referential rule carries ``rule_id``. With
    ``dry_run`` the file is untouched and only the diff is returned. When
    ``provenance`` is given (ADR D-0016), it is stamped onto the rule as the
    host prose sentence the detector was derived from, so the referential stays
    a cache traceable back to the host's own instructions.
    """
    target = _find_rule_file(root, rule_id)
    if target is None:
        raise KeyError(f"No referential rule with id {rule_id!r} under {root / 'guidelines'}")

    before = target.read_text(encoding="utf-8")
    header = _leading_comments(before)
    data = yaml.safe_load(before)
    for rule in data.get("rules", []):
        if isinstance(rule, dict) and rule.get("id") == rule_id:
            rule["detect"] = detector_to_detect(detector)
            if provenance:
                rule["provenance"] = provenance
            break

    after = header + yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
        )
    )

    if not dry_run:
        target.write_text(after, encoding="utf-8")

    return ApplyResult(rule_id=rule_id, file=target, diff=diff, written=not dry_run)


def _derived_id(rule: str, taken: set[str]) -> str:
    """A stable, unique, slug id for a derived rule (``derived-<slug>``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", rule.lower()).strip("-")[:48] or "rule"
    base = f"derived-{slug}"
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    taken.add(candidate)
    return candidate


def write_derived_ruleset(root: Path, derived: list[DerivedRule], *, dry_run: bool = True) -> ApplyResult:
    """Write an interpret-once ruleset into the per-repo derived cache (ADR D-0016).

    Each :class:`~guideline_checker.interpret.DerivedRule` becomes a transverse
    YAML rule under ``guidelines/derived/derived.yml`` — id, kind-mapped category,
    the proven detector, and the host sentence as both ``rule`` and ``provenance``.
    The file is rewritten wholesale (it is a regenerable cache, not hand-authored),
    so a re-run replaces stale derivations. ``dry_run`` returns the diff only.
    """
    target = root.joinpath(*_DERIVED_PATH)
    taken: set[str] = set()
    rules = [
        {
            "id": _derived_id(d.rule, taken),
            "category": _KIND_CATEGORY.get(d.kind, "correctness"),
            "severity": "warning",
            "rule": d.rule,
            "provenance": d.rule,
            "detect": detector_to_detect(d.detector),
        }
        for d in derived
    ]
    doc: dict[str, Any] = {"language_target": "*", "apply_to_glob": "**/*", "rules": rules}
    after = _DERIVED_HEADER + yaml.safe_dump(doc, sort_keys=True, allow_unicode=True)
    before = target.read_text(encoding="utf-8") if target.exists() else ""
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=str(target),
            tofile=str(target),
        )
    )
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
    return ApplyResult(rule_id=f"{len(rules)} derived rule(s)", file=target, diff=diff, written=not dry_run)


def detector_to_detect(detector: RuleDetector) -> dict[str, Any]:
    """Map a ``RuleDetector`` to a ``detect:`` block with only its non-empty fields."""
    detect: dict[str, Any] = {}
    for attr, key in _PATTERN_FIELDS:
        values = getattr(detector, attr)
        if values:
            detect[key] = list(values)
    if detector.stale_after_days is not None:
        detect["stale_after_days"] = detector.stale_after_days
    if detector.match_in_comments:
        detect["match_in_comments"] = True
    return detect


def find_rule_id_for_text(root: Path, rule_text: str) -> str | None:
    """Return the id of the YAML rule whose ``rule:`` text matches, or ``None``.

    The web health payload keys rules by their prose (ids are dropped at the
    ``InstructionFile`` boundary), so one-click resolution recovers the id here
    to know which referential rule to arm. Markdown-sourced rules have no YAML
    entry and yield ``None``.
    """
    guidelines = root / "guidelines"
    if not guidelines.is_dir():
        return None
    for path in sorted(guidelines.rglob("*.yml")):
        if path.name == "categories.yml":
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for rule in data.get("rules", []):
            if isinstance(rule, dict) and str(rule.get("rule", "")).strip() == rule_text.strip():
                rule_id = rule.get("id")
                return str(rule_id) if rule_id is not None else None
    return None


def _find_rule_file(root: Path, rule_id: str) -> Path | None:
    """Return the referential file that declares ``rule_id``, or ``None``."""
    guidelines = root / "guidelines"
    if not guidelines.is_dir():
        return None
    for path in sorted(guidelines.rglob("*.yml")):
        if path.name == "categories.yml":
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for rule in data.get("rules", []):
            if isinstance(rule, dict) and rule.get("id") == rule_id:
                return path
    return None


def _leading_comments(text: str) -> str:
    """Capture the file's leading comment/blank lines so a dump can re-prepend them."""
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("#") or not line.strip():
            kept.append(line)
        else:
            break
    return "".join(kept)
