"""Load the structured multi-dimension YAML rule referential (``guidelines/``).

The referential is 100 % filesystem-driven and Notion-agnostic: rules live in
``guidelines/<dimension>/*.yml`` and are discovered purely by folder convention.
The loader is **generic** — adding a model, a language, or a whole new dimension
is just dropping a file, never a code change:

- Every sub-directory of ``guidelines/`` is a dimension (``ai-models/``,
  ``languages/``, …). The directory name is free-form.
- Each file declares its own target field by the ``<dim>_target`` convention —
  ``model_target`` in ``ai-models/``, ``language_target`` in ``languages/``,
  ``framework_target`` in a hypothetical ``frameworks/``. The loader reads
  whichever single ``*_target`` key the file carries; ``"*"`` / ``_common.yml``
  provide transverse rules.
- A file may declare a file-level ``apply_to_glob`` to scope its rules to a file
  pattern (e.g. ``**/*.py``); absent, rules apply to every file.

A shared ``guidelines/categories.yml`` registry constrains every rule's
``category`` (unknown category → hard failure). Each rule carries an explicit
``severity`` that overrides the checker's phrasing-derived default.

Rules flow into the existing engine as :class:`~guideline_checker.loader.InstructionFile`
objects, so no checker rewrite is needed — pattern *detection* stays shared,
only the reported severity is taken from the YAML.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from guideline_checker.ast_python import VALID_AST_CHECKS, unknown_checks
from guideline_checker.loader import InstructionFile, RuleDetector, SourceType

logger = logging.getLogger(__name__)

# A dimension file declares its target via a "<dim>_target" key (model_target,
# language_target, framework_target, …). The loader reads whichever one is present.
_TARGET_FIELD_RE = re.compile(r"^[a-z_]+_target$")

_ALL_FILES_GLOB = "**/*"
_WILDCARD_TARGET = "*"

_VALID_SEVERITIES = frozenset({"error", "warning", "info"})

_CATEGORIES_FILE = "categories.yml"
_APPLY_TO_GLOB_FIELD = "apply_to_glob"
_DETECT_FIELD = "detect"
# The list-of-pattern keys a ``detect:`` block may carry (all optional).
_DETECT_PATTERN_KEYS = ("forbid", "forbid_regex", "file_regex")
# Named Python AST checks (validated against ast_python.VALID_AST_CHECKS).
_DETECT_AST_KEY = "ast"


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
    detect: RuleDetector | None = None


@dataclass
class _DimensionFile:
    """A parsed referential file plus the rules that survived id de-duplication."""

    path: Path
    dimension: str
    apply_to_glob: str = _ALL_FILES_GLOB
    rules: list[GuidelineRule] = field(default_factory=list)


def load_yaml_guidelines(root: Path) -> list[InstructionFile]:
    """Load ``guidelines/<dimension>/*.yml`` into :class:`InstructionFile` objects.

    Behaviour (per the chrysa referential spec):

    1. Scan every sub-directory of ``guidelines/`` for ``*.yml`` files.
    2. Read each file's own ``*_target`` field, overridable per rule;
       ``"*"`` / ``_common.yml`` provide transverse rules.
    3. Validate every ``category`` against ``guidelines/categories.yml``.
    4. Merge rules with *first-match-wins* on ``id`` — collisions are logged,
       not silently swallowed.
    5. Map the effective target to an ``apply_to`` glob (the file's
       ``apply_to_glob``, or ``**/*`` for the wildcard target) and emit one
       ``InstructionFile`` per ``(file, target)`` group.
    """
    guidelines_dir = root / "guidelines"
    if not guidelines_dir.is_dir():
        return []

    categories = _load_categories(guidelines_dir)

    seen_ids: set[str] = set()
    # Files are sorted so "_common.yml" (transverse) is parsed first and wins id ties;
    # the comprehension preserves that left-to-right order while seen_ids accumulates.
    parsed = [
        _parse_dimension_file(yml_path, dim_dir.name, categories, seen_ids)
        for dim_dir in sorted(p for p in guidelines_dir.iterdir() if p.is_dir())
        for yml_path in sorted(dim_dir.glob("*.yml"))
    ]

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


def _discover_target_field(path: Path, data: dict[str, object]) -> str | None:
    """Return the file's ``<dim>_target`` key, or ``None`` for a transverse file.

    A file declares exactly one target field (``model_target``,
    ``language_target``, …). Zero is allowed (the file is transverse, all rules
    default to ``"*"``); more than one is ambiguous and rejected.
    """
    fields = [k for k in data if isinstance(k, str) and _TARGET_FIELD_RE.match(k)]
    if len(fields) > 1:
        raise GuidelineError(
            f"{path}: multiple target fields {sorted(fields)} — a file declares exactly one '<dim>_target'.",
        )
    return fields[0] if fields else None


def _parse_dimension_file(
    path: Path,
    dimension: str,
    categories: set[str],
    seen_ids: set[str],
) -> _DimensionFile:
    """Parse one dimension file, validating and de-duplicating its rules."""
    data = _safe_load(path)
    if not isinstance(data, dict):
        raise GuidelineError(f"{path}: expected a mapping at the top level.")

    target_field = _discover_target_field(path, data)
    if target_field is None:
        file_target = _WILDCARD_TARGET
    else:
        file_target = data.get(target_field, _WILDCARD_TARGET)
        if not isinstance(file_target, str):
            raise GuidelineError(f"{path}: '{target_field}' must be a string.")

    apply_to_glob = data.get(_APPLY_TO_GLOB_FIELD, _ALL_FILES_GLOB)
    if not isinstance(apply_to_glob, str) or not apply_to_glob.strip():
        raise GuidelineError(f"{path}: '{_APPLY_TO_GLOB_FIELD}' must be a non-empty string.")

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
    return _DimensionFile(path=path, dimension=dimension, apply_to_glob=apply_to_glob, rules=rules)


def _build_rule(
    path: Path,
    raw: object,
    target_field: str | None,
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

    # A rule may override its target only when the file declares a target field.
    if target_field is None:
        target = file_target
    else:
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
        detect=_build_detector(path, raw),
    )


def _build_detector(path: Path, raw: dict[str, object]) -> RuleDetector | None:
    """Validate a rule's optional ``detect:`` block into a :class:`RuleDetector`.

    All keys are optional; an absent or empty block returns ``None`` so the rule
    falls back to phrase-derived detection. Pattern keys must be lists of
    non-empty strings; ``match_in_comments`` must be a bool.
    """
    if _DETECT_FIELD not in raw:
        return None
    block = raw[_DETECT_FIELD]
    if not isinstance(block, dict):
        raise GuidelineError(f"{path}: rule {raw['id']!r} 'detect' must be a mapping.")

    allowed = {*_DETECT_PATTERN_KEYS, _DETECT_AST_KEY, "match_in_comments"}
    unknown = set(block) - allowed
    if unknown:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect' has unknown key(s) {sorted(unknown)} (allowed: {sorted(allowed)}).",
        )

    patterns: dict[str, tuple[str, ...]] = {}
    for key in _DETECT_PATTERN_KEYS:
        patterns[key] = _coerce_pattern_list(path, raw["id"], key, block.get(key, []))

    ast_checks = _coerce_pattern_list(path, raw["id"], _DETECT_AST_KEY, block.get(_DETECT_AST_KEY, []))
    bad = unknown_checks(ast_checks)
    if bad:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect.ast' has unknown check(s) {bad} "
            f"(available: {sorted(VALID_AST_CHECKS)}).",
        )

    match_in_comments = block.get("match_in_comments", False)
    if not isinstance(match_in_comments, bool):
        raise GuidelineError(f"{path}: rule {raw['id']!r} 'detect.match_in_comments' must be a boolean.")

    if not any(patterns.values()) and not ast_checks:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect' declares no patterns — "
            f"add at least one of {sorted((*_DETECT_PATTERN_KEYS, _DETECT_AST_KEY))} or drop the block.",
        )

    return RuleDetector(
        forbid=patterns["forbid"],
        forbid_regex=patterns["forbid_regex"],
        file_regex=patterns["file_regex"],
        ast_checks=ast_checks,
        match_in_comments=match_in_comments,
    )


def _coerce_pattern_list(path: Path, rule_id: object, key: str, value: object) -> tuple[str, ...]:
    """Validate a ``detect`` pattern key into a tuple of non-empty strings."""
    if not isinstance(value, list):
        raise GuidelineError(f"{path}: rule {rule_id!r} 'detect.{key}' must be a list.")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise GuidelineError(f"{path}: rule {rule_id!r} 'detect.{key}' entries must be non-empty strings.")
        out.append(entry)
    return tuple(out)


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
                apply_to=_target_to_glob(target, df.apply_to_glob),
                description=f"Guidelines — {df.dimension}/{df.path.stem} [{target}]",
                content="",
                source_type=SourceType.GUIDELINES_YAML,
                rules=[r.rule for r in rules],
                rule_severity={r.rule: r.severity for r in rules},
                rule_detectors={r.rule: r.detect for r in rules if r.detect is not None},
            ),
        )
    return instruction_files


def _target_to_glob(target: str, file_glob: str) -> str:
    """Map a rule target to the file glob the checker scopes it with.

    The wildcard target always applies to every file — so a transverse ``"*"``
    rule living inside a glob-scoped file (e.g. ``python.yml``) is *not* narrowed
    to that file's glob. Any concrete target uses the file's ``apply_to_glob``.
    """
    if target == _WILDCARD_TARGET:
        return _ALL_FILES_GLOB
    return file_glob or _ALL_FILES_GLOB


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
