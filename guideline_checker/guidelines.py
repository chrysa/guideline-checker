"""Load the structured multi-dimension YAML rule referential (``guidelines/``).

The referential is 100 % filesystem-driven and Notion-agnostic: rules live in
``guidelines/<dimension>/*.yml`` and are discovered by folder convention. Two
dimensions are recognised:

- ``ai-models/``  → rules keyed by ``model_target`` (``claude``, ``gpt``, …)
- ``languages/``  → rules keyed by ``language_target`` (``python``, ``typescript``, …)

A shared ``guidelines/categories.yml`` registry constrains every rule's
``category`` (unknown category → hard failure). Each rule carries an explicit
``severity`` that overrides the checker's phrasing-derived default.

Rules flow into the existing engine as :class:`~guideline_checker.loader.InstructionFile`
objects, so no checker rewrite is needed — pattern *detection* stays shared,
only the reported severity is taken from the YAML.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from guideline_checker.loader import InstructionFile, SourceType

logger = logging.getLogger(__name__)

# Dimension directory name -> the target field its files/rules use.
_DIMENSIONS: dict[str, str] = {
    "ai-models": "model_target",
    "languages": "language_target",
}

# Language target -> apply_to glob the checker scopes the rule set with.
# Model targets and the "*" wildcard fall through to _ALL_FILES_GLOB.
_LANGUAGE_GLOBS: dict[str, str] = {
    "python": "**/*.py",
    "typescript": "**/*.ts,**/*.tsx",
    "react": "**/*.tsx,**/*.jsx",
}
_ALL_FILES_GLOB = "**/*"
_WILDCARD_TARGET = "*"

_VALID_SEVERITIES = frozenset({"error", "warning", "info"})

_CATEGORIES_FILE = "categories.yml"


class GuidelineError(ValueError):
    """Raised when the YAML referential is structurally invalid."""


@dataclass
class GuidelineRule:
    """A single structured rule from the YAML referential."""

    id: str
    target: str
    category: str
    severity: str
    rule: str
    rationale: str = ""


@dataclass
class _DimensionFile:
    """A parsed referential file plus the rules that survived id de-duplication."""

    path: Path
    dimension: str
    rules: list[GuidelineRule] = field(default_factory=list)


def load_yaml_guidelines(root: Path) -> list[InstructionFile]:
    """Load ``guidelines/<dimension>/*.yml`` into :class:`InstructionFile` objects.

    Behaviour (per the chrysa referential spec):

    1. Scan each known dimension directory for ``*.yml`` files.
    2. Read the dimension's target field per file, overridable per rule;
       ``"*"`` / ``_common.yml`` provide transverse rules.
    3. Validate every ``category`` against ``guidelines/categories.yml``.
    4. Merge rules with *first-match-wins* on ``id`` — collisions are logged,
       not silently swallowed.
    5. Map the effective target to an ``apply_to`` glob and emit one
       ``InstructionFile`` per ``(file, target)`` group.
    """
    guidelines_dir = root / "guidelines"
    if not guidelines_dir.is_dir():
        return []

    categories = _load_categories(guidelines_dir)

    seen_ids: set[str] = set()
    parsed: list[_DimensionFile] = []

    for dimension, target_field in _DIMENSIONS.items():
        dim_dir = guidelines_dir / dimension
        if not dim_dir.is_dir():
            continue
        # Sorted so "_common.yml" (transverse) is parsed first and wins id ties.
        for yml_path in sorted(dim_dir.glob("*.yml")):
            rules = _parse_dimension_file(yml_path, target_field, categories, seen_ids)
            parsed.append(_DimensionFile(path=yml_path, dimension=dimension, rules=rules))

    return [instr for df in parsed for instr in _to_instruction_files(df)]


def _load_categories(guidelines_dir: Path) -> set[str]:
    """Return the set of registered category ids from ``categories.yml``."""
    categories_path = guidelines_dir / _CATEGORIES_FILE
    if not categories_path.is_file():
        raise GuidelineError(
            f"{categories_path} is missing — the shared category registry is required to validate guideline rules.",
        )
    data = _safe_load(categories_path)
    raw = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise GuidelineError(f"{categories_path}: expected a top-level 'categories' list.")

    ids: set[str] = set()
    for entry in raw:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            ids.add(entry["id"])
        else:
            raise GuidelineError(f"{categories_path}: every category needs a string 'id'.")
    return ids


def _parse_dimension_file(
    path: Path,
    target_field: str,
    categories: set[str],
    seen_ids: set[str],
) -> list[GuidelineRule]:
    """Parse one dimension file, validating and de-duplicating its rules."""
    data = _safe_load(path)
    if not isinstance(data, dict):
        raise GuidelineError(f"{path}: expected a mapping at the top level.")

    file_target = data.get(target_field, _WILDCARD_TARGET)
    if not isinstance(file_target, str):
        raise GuidelineError(f"{path}: '{target_field}' must be a string.")

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise GuidelineError(f"{path}: 'rules' must be a list.")

    rules: list[GuidelineRule] = []
    for raw in raw_rules:
        rule = _build_rule(path, raw, target_field, file_target, categories)
        if rule.id in seen_ids:
            logger.warning("guidelines: duplicate rule id %r in %s — keeping first, skipping this one", rule.id, path)
            continue
        seen_ids.add(rule.id)
        rules.append(rule)
    return rules


def _build_rule(
    path: Path,
    raw: object,
    target_field: str,
    file_target: str,
    categories: set[str],
) -> GuidelineRule:
    """Validate a single raw rule mapping into a :class:`GuidelineRule`."""
    if not isinstance(raw, dict):
        raise GuidelineError(f"{path}: each rule must be a mapping, got {type(raw).__name__}.")

    for required in ("id", "category", "severity", "rule"):
        if not isinstance(raw.get(required), str) or not raw[required].strip():
            raise GuidelineError(f"{path}: rule is missing a non-empty string '{required}'.")

    category = raw["category"]
    if category not in categories:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} uses unknown category {category!r} — "
            f"add it to {_CATEGORIES_FILE} or fix the typo.",
        )

    severity = raw["severity"]
    if severity not in _VALID_SEVERITIES:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} has invalid severity {severity!r} "
            f"(expected one of {sorted(_VALID_SEVERITIES)}).",
        )

    target = raw.get(target_field, file_target)
    if not isinstance(target, str):
        raise GuidelineError(f"{path}: rule {raw['id']!r} '{target_field}' must be a string.")

    return GuidelineRule(
        id=raw["id"],
        target=target,
        category=category,
        severity=severity,
        rule=raw["rule"].strip(),
        rationale=str(raw.get("rationale", "")).strip(),
    )


def _to_instruction_files(df: _DimensionFile) -> list[InstructionFile]:
    """Group a file's rules by effective target and emit one InstructionFile each.

    Grouping by target lets a rule that overrides its target (e.g. a ``"*"``
    transverse rule living in a language-specific file) get the correct
    ``apply_to`` glob, since ``InstructionFile.apply_to`` is single-valued.
    """
    by_target: dict[str, list[GuidelineRule]] = defaultdict(list)
    for rule in df.rules:
        by_target[rule.target].append(rule)

    instruction_files: list[InstructionFile] = []
    for target, rules in by_target.items():
        instruction_files.append(
            InstructionFile(
                path=df.path,
                apply_to=_target_to_glob(target),
                description=f"Guidelines — {df.dimension}/{df.path.stem} [{target}]",
                content="",
                source_type=SourceType.GUIDELINES_YAML,
                rules=[r.rule for r in rules],
                rule_severity={r.rule: r.severity for r in rules},
            ),
        )
    return instruction_files


def _target_to_glob(target: str) -> str:
    """Map a rule target to the file glob the checker scopes it with."""
    if target == _WILDCARD_TARGET:
        return _ALL_FILES_GLOB
    return _LANGUAGE_GLOBS.get(target, _ALL_FILES_GLOB)


def _safe_load(path: Path) -> object:
    """Read and YAML-parse a file, wrapping parse errors with file context."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GuidelineError(f"{path}: cannot read file ({exc}).") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GuidelineError(f"{path}: invalid YAML ({exc}).") from exc
