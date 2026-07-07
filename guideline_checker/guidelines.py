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
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from guideline_checker.ast_javascript import VALID_JS_AST_CHECKS, unknown_js_checks
from guideline_checker.ast_python import VALID_AST_CHECKS, unknown_checks
from guideline_checker.loader import InstructionFile, RuleDetector, RuleFix, SourceType
from guideline_checker.scanners import VALID_SCANS, unknown_scans

logger = logging.getLogger(__name__)

# Every named AST check, Python (stdlib ast) and JS/TS (tree-sitter). Names are disjoint;
# dispatch is by file suffix at scan time, so a ``detect.ast`` list may name either engine.
_ALL_AST_CHECKS: frozenset[str] = VALID_AST_CHECKS | VALID_JS_AST_CHECKS


def _unknown_ast_checks(names: Sequence[str]) -> list[str]:
    """Names that are not a registered check in either AST engine."""
    return [n for n in unknown_checks(names) if n in unknown_js_checks(names)]


# A dimension file declares its target via a "<dim>_target" key (model_target,
# language_target, framework_target, …). The loader reads whichever one is present.
_TARGET_FIELD_RE = re.compile(r"^[a-z_]+_target$")

_ALL_FILES_GLOB = "**/*"
_WILDCARD_TARGET = "*"

_VALID_SEVERITIES = frozenset({"error", "warning", "info"})

_CATEGORIES_FILE = "categories.yml"
_APPLY_TO_GLOB_FIELD = "apply_to_glob"
_EXTENDS_FIELD = "extends"
_ABSTRACT_FIELD = "abstract"
_DETECT_FIELD = "detect"
# The list-of-pattern keys a ``detect:`` block may carry (all optional).
_DETECT_PATTERN_KEYS = ("forbid", "forbid_regex", "file_regex")
# Named AST checks (validated against _ALL_AST_CHECKS: Python + JS/TS engines).
_DETECT_AST_KEY = "ast"
# Named content scanners (validated against scanners.VALID_SCANS).
_DETECT_SCAN_KEY = "scan"

_FIX_FIELD = "fix"
_FIX_OPS = frozenset({"remove_line", "replace", "regex_replace"})

# Cross-file inheritance + rule packs (D-0008). Files under guidelines/packs/ are
# parsed (so their bases are extends-available) but emitted only where included.
_INCLUDE_FIELD = "include"
_PACKS_DIR = "packs"


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
    fix: RuleFix | None = None


@dataclass
class _DimensionFile:
    """A parsed referential file plus the rules that survived id de-duplication."""

    path: Path
    dimension: str
    apply_to_glob: str = _ALL_FILES_GLOB
    rules: list[GuidelineRule] = field(default_factory=list)


@dataclass
class _ParsedFile:
    """One referential file after pass 1 — raw rules only, before global resolution (D-0008)."""

    path: Path
    dimension: str
    file_target: str
    apply_to_glob: str
    order: list[str]
    raw_by_id: dict[str, _RawRule]
    includes: list[Path]  # absolute pack paths this file pulls in


@dataclass
class _RawRule:
    """A rule before ``extends:`` resolution; inheritable fields may be ``None``."""

    id: str
    path: Path
    extends: str | None
    abstract: bool
    target: str | None
    category: str | None
    severity: str | None
    rule: str | None
    rationale: str
    detect: RuleDetector | None
    fix: RuleFix | None
    # The declaring file's target — anchors the target fallback when a rule in
    # another file inherits this one via cross-file extends (D-0008).
    file_target: str = _WILDCARD_TARGET


def load_yaml_guidelines(root: Path) -> list[InstructionFile]:
    """Load ``guidelines/<dimension>/*.yml`` into :class:`InstructionFile` objects.

    Behaviour (per the chrysa referential spec):

    1. Scan every sub-directory of ``guidelines/`` for ``*.yml`` files
       (``packs/`` is skipped by the auto-scan — pack files load only via ``include:``).
    2. Read each file's own ``*_target`` field, overridable per rule;
       ``"*"`` / ``_common.yml`` provide transverse rules.
    3. Parse every file into one global id→rule registry, then resolve ``extends:``
       against it, so a base may live in another file or an included pack (D-0008);
       ``abstract: true`` bases are not emitted.
    4. De-duplicate ``id``: a duplicate **within one file** is an authoring bug
       and raises; a duplicate **across files** is an intentional transverse
       override (``_common.yml`` parsed first wins) — first kept, collision logged.
    5. Map the effective target to an ``apply_to`` glob and emit one
       ``InstructionFile`` per ``(file, target)`` group.
    """
    guidelines_dir = root / "guidelines"
    if not guidelines_dir.is_dir():
        return []

    categories = _load_categories(guidelines_dir)
    # Parse every referential file (dimensions + packs) into one registry. Files are
    # sorted so "_common.yml" (transverse) parses first and wins id ties.
    parsed_by_path: dict[Path, _ParsedFile] = {}
    for dim_dir in sorted(p for p in guidelines_dir.iterdir() if p.is_dir()):
        for yml_path in sorted(dim_dir.glob("*.yml")):
            parsed_by_path[yml_path] = _parse_file(yml_path, dim_dir.name, guidelines_dir, categories)

    global_raw: dict[str, _RawRule] = {}
    for parsed in parsed_by_path.values():
        for rid in parsed.order:
            global_raw.setdefault(rid, parsed.raw_by_id[rid])

    emit_paths = _emit_order(parsed_by_path)
    seen_ids: set[str] = set()
    instruction_files: list[InstructionFile] = []
    for path in emit_paths:
        parsed = parsed_by_path[path]
        rules = _emit_resolved_rules(path, parsed.order, global_raw, categories, seen_ids)
        df = _DimensionFile(path=path, dimension=parsed.dimension, apply_to_glob=parsed.apply_to_glob, rules=rules)
        instruction_files.extend(_to_instruction_files(df))
    return instruction_files


def _emit_order(parsed_by_path: dict[Path, _ParsedFile]) -> list[Path]:
    """Emit every auto-scanned dimension file, plus each pack pulled in via ``include:``."""
    emit: list[Path] = []
    included: list[Path] = []
    for path, parsed in parsed_by_path.items():
        if parsed.dimension == _PACKS_DIR:
            continue  # packs emit only when included
        emit.append(path)
        for inc in parsed.includes:
            if inc not in parsed_by_path:
                raise GuidelineError(f"{path}: include target {inc} was not found under guidelines/.")
            if inc not in included and inc not in emit:
                included.append(inc)
    return emit + included


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


def _parse_dimension_file_header(path: Path, data: dict[str, object]) -> tuple[str, str]:
    """Extract (file_target, apply_to_glob) from the top-level mapping, raising on errors."""
    target_field = _discover_target_field(path, data)
    if target_field is None:
        file_target = _WILDCARD_TARGET
    else:
        raw_target = data.get(target_field, _WILDCARD_TARGET)
        if not isinstance(raw_target, str):
            raise GuidelineError(f"{path}: '{target_field}' must be a string.")
        file_target = raw_target

    apply_to_glob = data.get(_APPLY_TO_GLOB_FIELD, _ALL_FILES_GLOB)
    if not isinstance(apply_to_glob, str) or not apply_to_glob.strip():
        raise GuidelineError(f"{path}: '{_APPLY_TO_GLOB_FIELD}' must be a non-empty string.")

    return str(file_target), str(apply_to_glob)


def _collect_raw_rules(
    path: Path,
    raw_rules: list[object],
    target_field: str | None,
    categories: set[str],
    file_target: str,
) -> tuple[dict[str, _RawRule], list[str]]:
    """Pass 1 — parse every raw rule entry; reject intra-file duplicate ids."""
    raw_by_id: dict[str, _RawRule] = {}
    order: list[str] = []
    for raw in raw_rules:
        rr = _parse_raw_rule(path, raw, target_field, categories, file_target)
        if rr.id in raw_by_id:
            raise GuidelineError(
                f"{path}: duplicate rule id {rr.id!r} within the file — "
                f"each rule id must be unique inside a single referential file.",
            )
        raw_by_id[rr.id] = rr
        order.append(rr.id)
    return raw_by_id, order


def _emit_resolved_rules(
    path: Path,
    order: list[str],
    raw_by_id: dict[str, _RawRule],
    categories: set[str],
    seen_ids: set[str],
) -> list[GuidelineRule]:
    """Pass 2 — resolve inheritance (against the global registry) and emit rules."""
    resolved: dict[str, GuidelineRule] = {}
    rules: list[GuidelineRule] = []
    for rid in order:
        rule = _resolve_rule(rid, raw_by_id, resolved, categories, ())
        if raw_by_id[rid].abstract:
            continue
        if rid in seen_ids:
            logger.warning("guidelines: duplicate rule id %r in %s — keeping first, skipping this one", rid, path)
            continue
        seen_ids.add(rid)
        rules.append(rule)
    return rules


def _parse_file(
    path: Path,
    dimension: str,
    guidelines_dir: Path,
    categories: set[str],
) -> _ParsedFile:
    """Pass 1 — parse one referential file's header, ``include:`` list, and raw rules.

    Resolution is deferred to :func:`_emit_resolved_rules` against the global registry,
    so a child may extend a base declared in another file or an included pack (D-0008).
    """
    data = _safe_load(path)
    if not isinstance(data, dict):
        raise GuidelineError(f"{path}: expected a mapping at the top level.")

    target_field = _discover_target_field(path, data)
    file_target, apply_to_glob = _parse_dimension_file_header(path, data)

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise GuidelineError(f"{path}: 'rules' must be a list.")

    raw_by_id, order = _collect_raw_rules(path, raw_rules, target_field, categories, file_target)
    includes = _parse_includes(path, data, guidelines_dir)
    return _ParsedFile(
        path=path,
        dimension=dimension,
        file_target=file_target,
        apply_to_glob=apply_to_glob,
        order=order,
        raw_by_id=raw_by_id,
        includes=includes,
    )


def _parse_includes(path: Path, data: dict[str, object], guidelines_dir: Path) -> list[Path]:
    """Parse the top-level ``include:`` list into absolute pack paths under ``guidelines/``."""
    raw = data.get(_INCLUDE_FIELD)
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise GuidelineError(f"{path}: '{_INCLUDE_FIELD}' must be a list of non-empty path strings.")
    includes: list[Path] = []
    for entry in raw:
        target = guidelines_dir / entry  # unresolved, to match the auto-scan's path keys
        if guidelines_dir.resolve() not in target.resolve().parents:
            raise GuidelineError(f"{path}: include {entry!r} must resolve to a path under guidelines/.")
        includes.append(target)
    return includes


def _validate_raw_rule_id(path: Path, raw: dict[str, object]) -> str:
    """Extract and validate the rule 'id' field; raise on missing or non-string."""
    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise GuidelineError(f"{path}: rule is missing a non-empty string 'id'.")
    return rule_id


def _validate_raw_rule_category(path: Path, rule_id: str, raw: dict[str, object], categories: set[str]) -> str | None:
    """Validate and return the optional 'category' field against known categories."""
    category = _optional_str(path, rule_id, "category", raw)
    if category is not None and category not in categories:
        raise GuidelineError(
            f"{path}: rule {rule_id!r} uses unknown category {category!r} "
            f"(known categories: {sorted(categories)}) — add it to {_CATEGORIES_FILE} or fix the typo.",
        )
    return category


def _validate_raw_rule_severity(path: Path, rule_id: str, raw: dict[str, object]) -> str | None:
    """Validate and return the optional 'severity' field against allowed values."""
    severity = _optional_str(path, rule_id, "severity", raw)
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise GuidelineError(
            f"{path}: rule {rule_id!r} has invalid severity {severity!r} "
            f"(expected one of {sorted(_VALID_SEVERITIES)}).",
        )
    return severity


def _extract_raw_rule_target(path: Path, rule_id: str, raw: dict[str, object], target_field: str | None) -> str | None:
    """Extract the per-rule target override when the file declares a target field."""
    if target_field is None or target_field not in raw:
        return None
    candidate = raw[target_field]
    if not isinstance(candidate, str):
        raise GuidelineError(f"{path}: rule {rule_id!r} '{target_field}' must be a string.")
    return candidate


def _parse_raw_rule(
    path: Path,
    raw: object,
    target_field: str | None,
    categories: set[str],
    file_target: str = _WILDCARD_TARGET,
) -> _RawRule:
    """Parse a raw rule into a :class:`_RawRule`; required-field presence is enforced
    later in :func:`_resolve_rule`, once any ``extends`` base has been merged in.
    """
    if not isinstance(raw, dict):
        raise GuidelineError(f"{path}: each rule must be a mapping, got {type(raw).__name__}.")
    rule_id = _validate_raw_rule_id(path, raw)
    extends = raw.get(_EXTENDS_FIELD)
    if extends is not None and (not isinstance(extends, str) or not extends.strip()):
        raise GuidelineError(f"{path}: rule {rule_id!r} '{_EXTENDS_FIELD}' must be a non-empty string.")
    abstract = raw.get(_ABSTRACT_FIELD, False)
    if not isinstance(abstract, bool):
        raise GuidelineError(f"{path}: rule {rule_id!r} '{_ABSTRACT_FIELD}' must be a boolean.")
    category = _validate_raw_rule_category(path, rule_id, raw, categories)
    severity = _validate_raw_rule_severity(path, rule_id, raw)
    rule_text = _optional_str(path, rule_id, "rule", raw)
    target = _extract_raw_rule_target(path, rule_id, raw, target_field)
    return _RawRule(
        id=rule_id,
        path=path,
        extends=extends,
        abstract=abstract,
        target=target,
        category=category,
        severity=severity,
        rule=rule_text.strip() if rule_text is not None else None,
        rationale=str(raw.get("rationale", "")).strip(),
        detect=_build_detector(path, raw),
        fix=_build_fix(path, rule_id, raw),
        file_target=file_target,
    )


def _optional_str(path: Path, rule_id: str, key: str, raw: dict[str, object]) -> str | None:
    """Return ``raw[key]`` as a non-empty string, or ``None`` when the key is absent."""
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise GuidelineError(f"{path}: rule {rule_id!r} '{key}' must be a non-empty string.")
    return value


def _resolve_base(
    rr: _RawRule,
    rule_id: str,
    raw_by_id: dict[str, _RawRule],
    resolved: dict[str, GuidelineRule],
    categories: set[str],
    stack: tuple[str, ...],
) -> GuidelineRule | None:
    """Recursively resolve the base rule pointed to by ``extends``, or return None."""
    if rr.extends is None:
        return None
    if rr.extends not in raw_by_id:
        raise GuidelineError(
            f"{rr.path}: rule {rule_id!r} extends unknown base {rr.extends!r} — "
            f"declare the base in this file or an included pack (D-0008).",
        )
    return _resolve_rule(rr.extends, raw_by_id, resolved, categories, (*stack, rule_id))


def _merge_rr_with_base(
    rr: _RawRule,
    rule_id: str,
    base: GuidelineRule | None,
) -> GuidelineRule:
    """Merge a raw rule with its resolved base into a :class:`GuidelineRule`.

    Validates that required fields (category, severity, rule text) are present
    after inheritance.
    """
    category = rr.category or (base.category if base else None)
    severity = rr.severity or (base.severity if base else None)
    rule_text = rr.rule or (base.rule if base else None)
    rationale = rr.rationale or (base.rationale if base else "")
    target = rr.target or (base.target if base else None) or rr.file_target
    detect = _merge_detectors(base.detect if base else None, rr.detect)
    fix = rr.fix or (base.fix if base else None)

    if category is None:
        raise GuidelineError(f"{rr.path}: rule {rule_id!r} is missing a 'category' (none declared or inherited).")
    if severity is None:
        raise GuidelineError(f"{rr.path}: rule {rule_id!r} is missing a 'severity' (none declared or inherited).")
    if not rule_text:
        raise GuidelineError(f"{rr.path}: rule {rule_id!r} is missing a 'rule' (none declared or inherited).")
    return GuidelineRule(
        id=rule_id,
        target=target,
        category=category,
        severity=severity,
        rule=rule_text,
        rationale=rationale,
        detect=detect,
        fix=fix,
    )


def _resolve_rule(
    rule_id: str,
    raw_by_id: dict[str, _RawRule],
    resolved: dict[str, GuidelineRule],
    categories: set[str],
    stack: tuple[str, ...],
) -> GuidelineRule:
    """Resolve a rule's ``extends`` chain into a merged :class:`GuidelineRule`.

    ``raw_by_id`` is the global registry, so a base may live in another file or an
    included pack. Scalar fields take the child's value when present, else the base's;
    ``detect`` patterns are unioned. Cross-file cycles and unknown bases are hard failures.
    """
    if rule_id in resolved:
        return resolved[rule_id]
    rr = raw_by_id[rule_id]
    if rule_id in stack:
        chain = " -> ".join((*stack, rule_id))
        raise GuidelineError(f"{rr.path}: 'extends' cycle detected: {chain}.")
    base = _resolve_base(rr, rule_id, raw_by_id, resolved, categories, stack)
    rule = _merge_rr_with_base(rr, rule_id, base)
    resolved[rule_id] = rule
    return rule


def _merge_detectors(base: RuleDetector | None, child: RuleDetector | None) -> RuleDetector | None:
    """Union two detectors: child patterns appended to the base's, order-preserving."""
    if base is None:
        return child
    if child is None:
        return base
    return RuleDetector(
        forbid=_union(base.forbid, child.forbid),
        forbid_regex=_union(base.forbid_regex, child.forbid_regex),
        file_regex=_union(base.file_regex, child.file_regex),
        ast_checks=_union(base.ast_checks, child.ast_checks),
        match_in_comments=base.match_in_comments or child.match_in_comments,
    )


def _union(base: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    """Concatenate two tuples, dropping duplicates while preserving first-seen order."""
    return tuple(dict.fromkeys((*base, *extra)))


def _build_fix(path: Path, rule_id: str, raw: dict[str, object]) -> RuleFix | None:
    """Validate a rule's optional ``fix:`` block into a :class:`RuleFix` (ADR D-0007)."""
    if _FIX_FIELD not in raw:
        return None
    block = raw[_FIX_FIELD]
    if not isinstance(block, dict):
        raise GuidelineError(f"{path}: rule {rule_id!r} 'fix' must be a mapping.")
    op = block.get("op")
    if op not in _FIX_OPS:
        raise GuidelineError(f"{path}: rule {rule_id!r} 'fix.op' must be one of {sorted(_FIX_OPS)}.")
    if op == "remove_line":
        return RuleFix(op=op)
    search_key, repl_key = ("from", "to") if op == "replace" else ("pattern", "replacement")
    search = block.get(search_key)
    replacement = block.get(repl_key)
    if not isinstance(search, str) or not search:
        raise GuidelineError(f"{path}: rule {rule_id!r} 'fix.{search_key}' must be a non-empty string.")
    if not isinstance(replacement, str):
        raise GuidelineError(f"{path}: rule {rule_id!r} 'fix.{repl_key}' must be a string.")
    return RuleFix(op=op, search=search, replacement=replacement)


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

    allowed = {*_DETECT_PATTERN_KEYS, _DETECT_AST_KEY, _DETECT_SCAN_KEY, "match_in_comments"}
    unknown = set(block) - allowed
    if unknown:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect' has unknown key(s) {sorted(unknown)} (allowed: {sorted(allowed)}).",
        )

    patterns: dict[str, tuple[str, ...]] = {}
    for key in _DETECT_PATTERN_KEYS:
        patterns[key] = _coerce_pattern_list(path, raw["id"], key, block.get(key, []))

    ast_checks = _coerce_pattern_list(path, raw["id"], _DETECT_AST_KEY, block.get(_DETECT_AST_KEY, []))
    bad = _unknown_ast_checks(ast_checks)
    if bad:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect.ast' has unknown check(s) {bad} "
            f"(available: {sorted(_ALL_AST_CHECKS)}).",
        )

    scan_checks = _coerce_pattern_list(path, raw["id"], _DETECT_SCAN_KEY, block.get(_DETECT_SCAN_KEY, []))
    bad_scans = unknown_scans(scan_checks)
    if bad_scans:
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect.scan' has unknown scanner(s) {bad_scans} "
            f"(available: {sorted(VALID_SCANS)}).",
        )

    match_in_comments = block.get("match_in_comments", False)
    if not isinstance(match_in_comments, bool):
        raise GuidelineError(f"{path}: rule {raw['id']!r} 'detect.match_in_comments' must be a boolean.")

    if not any(patterns.values()) and not ast_checks and not scan_checks:
        detect_keys = sorted((*_DETECT_PATTERN_KEYS, _DETECT_AST_KEY, _DETECT_SCAN_KEY))
        raise GuidelineError(
            f"{path}: rule {raw['id']!r} 'detect' declares no patterns — "
            f"add at least one of {detect_keys} or drop the block.",
        )

    return RuleDetector(
        forbid=patterns["forbid"],
        forbid_regex=patterns["forbid_regex"],
        file_regex=patterns["file_regex"],
        ast_checks=ast_checks,
        scan_checks=scan_checks,
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
                rule_fixes={r.rule: r.fix for r in rules if r.fix is not None},
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
