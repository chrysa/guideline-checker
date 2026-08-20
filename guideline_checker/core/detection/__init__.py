"""Detection orchestrator: run every rule from an :class:`~guideline_checker.loader.InstructionFile`
against a repo's files and return the collected :class:`RuleResult` per instruction.

Absorbs the file-collection/exclusion machinery, the per-rule dispatch across every
detection mechanism (ADR D-0020), and the mechanism helpers that are themselves
orchestration (AST/scanner/credential fan-out) rather than a self-contained
mechanism module. The self-contained mechanisms live beside this file: phrase- and
pattern-derived checks in :mod:`.pattern`, cross-reference in :mod:`.crossref`,
numeric thresholds and length checks in :mod:`.numeric`, and whole-file
presence/freshness in :mod:`.presence`.
"""

from __future__ import annotations

import functools
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from pathlib import Path

import yaml

from guideline_checker.core.detection.ast_javascript import JS_SUFFIXES, run_js_ast_checks
from guideline_checker.core.detection.ast_python import run_ast_checks
from guideline_checker.core.detection.kinds import KIND_MEASURES, CheckKind, kind_of_detector, kind_of_phrase
from guideline_checker.core.detection.scanners import run_scans
from guideline_checker.loader import InstructionFile, RuleDetector, load_all_sources, load_instructions

__all__ = [
    "KIND_MEASURES",
    "CheckKind",
    "RuleResult",
    "Violation",
    "kind_of_detector",
    "kind_of_phrase",
    "run_checks",
]

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".eggs",
    "*.egg-info",
    "coverage",
    # graphify output directory — generated docs, not source
    "graphify-out",
    ".tox",
    "htmlcov",
    "reports",
    # IDE / editor generated dirs — never contain project source
    ".vscode",
    ".idea",
    ".fleet",
    # git worktree copies (e.g. .claude/worktrees/) — full repo duplicates, not source
    "worktrees",
}


# Only scan text-based source/config files — skip binaries, images, archives, etc.
_TEXT_EXTENSIONS = {
    # Python / general scripting
    ".py",
    ".pyi",
    # Web / JS / TS
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",
    # Config / data
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".xml",
    ".properties",
    # Shell / infra
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".tf",
    ".tfvars",
    ".dockerfile",
    "",  # no extension = likely script/Makefile
    # Docs / prose
    ".md",
    ".rst",
    ".txt",
    # Java / other compiled (common in monorepos)
    ".java",
    ".kt",
    ".scala",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".cs",
    # CI / misc
    ".sql",
    ".graphql",
    ".proto",
}


# Default maximum size for scanned files — larger files are generated/compiled artefacts.
# Override at runtime with the ``--max-file-size`` CLI flag or the ``GUIDELINE_MAX_FILE_SIZE``
# env var (both in bytes); a non-positive or non-numeric value falls back to this default.
_DEFAULT_MAX_FILE_SIZE: int = 200 * 1024


_MAX_FILE_SIZE_ENV = "GUIDELINE_MAX_FILE_SIZE"


def _resolve_max_file_size(override: int | None = None) -> int:
    """Resolve the max scannable file size: explicit override > env var > default.

    A non-positive override (e.g. ``--max-file-size -1``) is rejected and falls
    through to the env var / default, mirroring the env-var handling below — a
    negative cap would otherwise disable the size filter entirely.
    """
    if override is not None and override > 0:
        return override
    raw = os.environ.get(_MAX_FILE_SIZE_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return _DEFAULT_MAX_FILE_SIZE
        if value > 0:
            return value
    return _DEFAULT_MAX_FILE_SIZE


def _is_text_file(path: Path, max_file_size: int) -> bool:
    """Return True if the file should be scanned (text-based extension and within size limit)."""
    try:
        if path.stat().st_size > max_file_size:
            return False
    except OSError:
        return False
    suffix = path.suffix.lower()
    return suffix in _TEXT_EXTENSIONS or (suffix == "" and path.stat().st_size < 512_000)


@dataclass
class Violation:
    file: Path
    line_number: int
    line_content: str
    rule: str
    severity: str = "warning"


@dataclass
class RuleResult:
    instruction: InstructionFile
    violations: list[Violation] = field(default_factory=list)
    files_checked: int = 0


from guideline_checker.core.detection.numeric import _check_length_rules
from guideline_checker.core.detection.pattern import (
    DISABLE_COMMENT,
    _is_excluded,
    _matches_pattern,
    _pattern_check_violations,
    _split_patterns,
)


def run_checks(
    root: Path,
    instructions_dir: Path | None = None,
    diff_files: list[Path] | None = None,
    all_sources: bool = True,
    exclude: list[str] | None = None,
    max_file_size: int | None = None,
    instructions: list[InstructionFile] | None = None,
) -> list[RuleResult]:
    """Check all files in root against instruction files.

    Args:
        root: Project root directory.
        instructions_dir: Directory containing *.instructions.md files.
            When ``all_sources`` is True (default), this is ignored and all
            sources are discovered automatically.
        diff_files: When provided, restrict file scanning to this explicit list
            (used by the ``--diff`` CLI flag to check only git-modified files).
        all_sources: When True (default), use :func:`load_all_sources` to
            discover all instruction sources (Copilot, Claude, Agents).
            When False, load only ``*.instructions.md`` from ``instructions_dir``.
        exclude: Glob patterns (relative to ``root``) whose matching files are
            skipped. Each entry may be comma-separated; a bare directory name
            excludes everything beneath it. Applies to ``--diff`` files too.
            Patterns from a ``<root>/.guidelineignore`` file are merged in.
        max_file_size: Maximum size in bytes for a file to be scanned. When None,
            resolved from the ``GUIDELINE_MAX_FILE_SIZE`` env var, else the 200 KiB
            default. Files above the limit are skipped as generated/compiled artefacts.
            Ignored when ``diff_files`` is provided (the diff list is checked as-is).
        instructions: Pre-loaded instructions to use as-is, skipping this
            function's own discovery. Lets a caller run :func:`resolve_rule_detectors`
            (the generation loop's cache-first primary-detector pre-pass, spec §3.3)
            over the loaded set before checking, and reuse that same resolved list
            for reporting (e.g. rule-health). When ``None`` (default), loads
            per ``all_sources``/``instructions_dir`` as before.
    """
    if instructions is not None:
        pass
    elif all_sources:
        instructions = load_all_sources(root)
    elif instructions_dir is not None:
        instructions = load_instructions(instructions_dir)
    else:
        instructions = load_all_sources(root)
    all_files = diff_files if diff_files is not None else _collect_files(root, max_file_size)

    exclude_patterns = [p for raw in (exclude or []) for p in _split_patterns(raw)]
    exclude_patterns.extend(_read_ignore_file(root))
    if exclude_patterns:
        all_files = [f for f in all_files if not _is_excluded(f, root, exclude_patterns)]

    # Narrow apply_to for instructions whose filename hints at a specific language
    instructions = [_narrow_apply_to(instr) for instr in instructions]

    # Exclude instruction source files from being scanned — they define the rules,
    # not the code under review.  Scanning them produces spurious violations because
    # they contain rule *examples* (e.g. bare assert statements in docs).
    instruction_paths = frozenset(instr.path for instr in instructions)
    all_files = [f for f in all_files if f not in instruction_paths]

    cpu_count = min(len(instructions), os.cpu_count() or 1, 8)
    if cpu_count > 1:
        with ProcessPoolExecutor(max_workers=cpu_count) as executor:
            futures = [executor.submit(_instruction_worker, root, instr, all_files) for instr in instructions]
            results = [f.result() for f in as_completed(futures)]
    else:
        results = [_instruction_worker(root, instr, all_files) for instr in instructions]

    return results


def resolve_rule_detectors(
    root: Path, instructions: list[InstructionFile], engine_version: str
) -> list[InstructionFile]:
    """Fill in each instruction's missing *primary* detectors, cache-first (spec §3.3).

    For every rule with no declared ``detect:`` block (absent from
    ``rule_detectors``), resolves a detector cache-first: a cache hit is reused
    as-is, a cache miss falls back to :func:`core.derive.seed.derive_seed_rules`
    and, when that recognises the prose, stores the result for reuse. Prose
    neither the cache nor the derivation recognises is left out of
    ``rule_detectors`` — the rule stays advisory (spec §3.3 step 4).

    This is the primary-detector-filling pass. It is distinct from, and does
    not replace, ``_evaluate_rule``'s always-on *supplementary* seed check
    below, which never touches ``rule_detectors`` and keeps firing even once
    this pass has filled a rule in (Task 6 controller ruling).
    """
    # Imported here, not at module level: core.derive.seed imports PatternCheck
    # from core.detection.pattern, and core.detection (this package) is pattern's
    # parent — a module-level import here would form a genuine load-order cycle.
    from guideline_checker.core.derive.cache import load, prose_hash, store
    from guideline_checker.core.derive.seed import derive_seed_rules

    resolved: list[InstructionFile] = []
    for instruction in instructions:
        missing = [rule for rule in instruction.rules if rule not in instruction.rule_detectors]
        if not missing:
            resolved.append(instruction)
            continue

        new_detectors = dict(instruction.rule_detectors)
        for rule in missing:
            key = prose_hash(rule, engine_version)
            cached = load(root, key)
            if cached is not None:
                new_detectors[rule] = cached
                continue
            derived = derive_seed_rules(rule)
            if derived is not None:
                store(root, key, derived)
                new_detectors[rule] = derived
        resolved.append(dataclass_replace(instruction, rule_detectors=new_detectors))

    return resolved


# Keywords that indicate an instruction targets Python source files only.
_PYTHON_INSTR_KEYWORDS = frozenset({"python", "django", "ruff", "decorator", "drf", "pep", "mypy"})


# Keywords that indicate an instruction targets test files only.
_TEST_INSTR_KEYWORDS = frozenset({"test", "pytest", "fixture"})


# Keywords that indicate an instruction targets Makefile-like files only.
_MAKEFILE_INSTR_KEYWORDS = frozenset({"makefile"})


def _narrow_apply_to(instruction: InstructionFile) -> InstructionFile:
    """Narrow a generic ``apply_to='**/*'`` pattern based on instruction filename.

    When an instruction's filename clearly targets a specific language/file type,
    restricting ``apply_to`` avoids running (e.g.) 450 Django rules against
    1 158 JSON files that can never trigger them.
    """
    if instruction.apply_to != "**/*":
        return instruction  # respect explicit setting

    name = instruction.path.stem.lower()

    if any(kw in name for kw in _PYTHON_INSTR_KEYWORDS):
        return dataclass_replace(instruction, apply_to="**/*.py")

    if any(kw in name for kw in _TEST_INSTR_KEYWORDS):
        return dataclass_replace(
            instruction,
            apply_to="**/tests/**/*.py, **/test_*.py, **/conftest.py",
        )

    if any(kw in name for kw in _MAKEFILE_INSTR_KEYWORDS):
        return dataclass_replace(instruction, apply_to="**/Makefile*, **/*.mk")

    return instruction


def _file_batch_worker(
    root: Path,
    instr_data: list[tuple[str, list[str], str, str, str, str]],
    file_batch: list[Path],
) -> list[tuple[int, int, list[Violation]]]:
    """Worker: process a file batch against all instructions.

    Each worker handles ~1/N of the files but runs all instructions, so CPU
    load is balanced regardless of rule-count differences between sources.
    Returns list of ``(instruction_idx, matched_count, violations)``.
    """
    from guideline_checker.loader import InstructionFile, SourceType

    batch_results: list[tuple[int, int, list[Violation]]] = []
    for idx, (path_str, rules, apply_to, description, content, source_type) in enumerate(instr_data):
        instr = InstructionFile(
            path=Path(path_str),
            rules=rules,
            apply_to=apply_to,
            description=description,
            content=content,
            source_type=SourceType(source_type),
        )
        matched = [f for f in file_batch if _matches_pattern(f, root, apply_to)]
        violations: list[Violation] = []
        for fp in matched:
            violations.extend(_check_file(fp, instr, root=root))
        batch_results.append((idx, len(matched), violations))
    return batch_results


def _instruction_worker(
    root: Path,
    instruction: InstructionFile,
    all_files: list[Path],
) -> RuleResult:
    """Fallback single-process worker: check one instruction against matching files."""
    result = RuleResult(instruction=instruction)
    matched_files = [f for f in all_files if _matches_pattern(f, root, instruction.apply_to)]
    result.files_checked = len(matched_files)
    for file_path in matched_files:
        violations = _check_file(file_path, instruction, root=root)
        result.violations.extend(violations)
    return result


# Generated output files produced by guideline-checker itself — scanning them
# causes spurious violations because they contain quoted code from the project.
IGNORE_FILES = {
    "guideline-report.html",
    "guideline-report.json",
    "guideline-report.md",
    "guideline-synthesis.html",
}


def _collect_files(root: Path, max_file_size: int | None = None) -> list[Path]:
    """Recursively collect text-based source files, ignoring known irrelevant directories."""
    limit = _resolve_max_file_size(max_file_size)
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in IGNORE_DIRS or part.endswith(".egg-info") for part in path.parts)
        and path.name not in IGNORE_FILES
        and _is_text_file(path, limit)
    ]


IGNORE_FILE_NAME = ".guidelineignore"


def _read_ignore_file(root: Path) -> list[str]:
    """Read ``<root>/.guidelineignore`` — one glob per line, ``#`` comments and blanks ignored.

    Same pattern semantics as ``--exclude`` (see :func:`_is_excluded`). Absent or
    unreadable file yields no patterns, so the feature is purely opt-in.
    """
    ignore_path = root / IGNORE_FILE_NAME
    try:
        text = ignore_path.read_text(encoding="utf-8")
    except OSError:
        return []
    patterns: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            patterns.append(stripped)
    return patterns


SECRETS_ALLOWLIST_FILE = ".secrets-allowlist"


@functools.cache
def _load_secrets_allowlist(root: Path) -> tuple[tuple[str, ...], frozenset[str]]:
    """Read ``<root>/.secrets-allowlist`` → ``(path globs, value substrings)``.

    A YAML file with optional ``paths`` and ``values`` lists. Files on an
    allowlisted path are exempted from the secret scan (but still scanned for
    every other rule); listed value substrings never count as a secret. Absent,
    unreadable, or malformed file yields no allowances, so the feature is opt-in.
    """
    try:
        text = (root / SECRETS_ALLOWLIST_FILE).read_text(encoding="utf-8")
    except OSError:
        return (), frozenset()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return (), frozenset()
    if not isinstance(data, dict):
        return (), frozenset()
    paths = tuple(p for p in data.get("paths", []) if isinstance(p, str) and p.strip())
    values = frozenset(v for v in data.get("values", []) if isinstance(v, str) and v.strip())
    return paths, values


def _check_file(
    file_path: Path,
    instruction: InstructionFile,
    *,
    cached_lines: list[str] | None = None,
    root: Path | None = None,
) -> list[Violation]:
    """Check a single file against an instruction's rules."""
    violations: list[Violation] = []
    if cached_lines is not None:
        lines = cached_lines
    else:
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return violations

    file_content = "\n".join(lines).lower()

    for rule in instruction.rules:
        # Whole-file checks (presence/absence)
        rule_violations = _check_presence_rules(file_path, lines, file_content, rule)
        # Per-line checks (phrase-derived + any declarative detector the rule carries)
        rule_violations.extend(_evaluate_rule(file_path, lines, rule, instruction.rule_detectors.get(rule), root))

        # Structured sources (YAML referential) carry an explicit severity that
        # overrides the phrasing-derived default. Markdown sources leave
        # rule_severity empty, so this is a no-op for them.
        override = instruction.rule_severity.get(rule)
        if override is not None:
            for violation in rule_violations:
                violation.severity = override

        violations.extend(rule_violations)

    return violations


def _evaluate_rule(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector | None = None,
    root: Path | None = None,
) -> list[Violation]:
    """Evaluate a rule against file lines: a seed-derived detector from its prose
    plus, when the rule carries one, its declarative detector. Both paths can
    fire."""
    violations: list[Violation] = []
    rule_lower = rule.lower()

    # Length-based rules are handled separately (need the full file). A declared
    # detector still runs even for a length rule, so collect it before returning.
    length_violations = _check_length_rules(file_path, lines, rule_lower)
    if length_violations:
        if detector is not None:
            length_violations.extend(_declared_violations(file_path, lines, rule, detector, root))
        return length_violations

    # Supplementary seed check — always runs alongside instruction.detector
    # (whether YAML-declared or filled in by resolve_rule_detectors' cache-first
    # pre-pass), never replaced by it (Task 6 controller ruling). See
    # tests/test_guidelines.py::TestRuleInheritance::test_abstract_scalar_only_template,
    # which depends on the declared detector and this seed check firing together.
    #
    # Evaluated as independent per-pattern checks (own severity/match_in_comments
    # each), not merged into one RuleDetector: merging would collapse per-pattern
    # fidelity a single RuleDetector cannot represent (e.g. a rule arming both a
    # comment-scoped pattern like TODO and a code-only pattern like `assert`
    # would let `assert` match inside comments too).
    #
    # Imported here, not at module level: core.derive.seed imports PatternCheck
    # from core.detection.pattern, and core.detection (this package) is pattern's
    # parent — a module-level import here would form a genuine load-order cycle.
    from guideline_checker.core.derive.seed import derive_seed_pattern_checks

    seed_checks = derive_seed_pattern_checks(rule)
    if seed_checks:
        violations.extend(_pattern_check_violations(file_path, lines, rule, seed_checks))

    violations.extend(_credential_scan_violations(file_path, lines, rule, root))

    if detector is not None:
        violations.extend(_declared_violations(file_path, lines, rule, detector, root))

    return violations


def _ast_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Run AST checks (Python or JS/TS) and return matching violations."""
    if not detector.ast_checks:
        return []
    violations: list[Violation] = []
    joined = "\n".join(lines)
    if file_path.suffix == ".py":
        ast_findings = run_ast_checks(detector.ast_checks, joined)
    elif file_path.suffix in JS_SUFFIXES:
        ast_findings = run_js_ast_checks(detector.ast_checks, joined, file_path.suffix)
    else:
        ast_findings = []
    for lineno, snippet in ast_findings:
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if DISABLE_COMMENT in line:
            continue
        violations.append(
            Violation(
                file=file_path,
                line_number=lineno,
                line_content=(line.strip() or snippet)[:120],
                rule=rule,
                severity="warning",
            ),
        )
    return violations


def _scan_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
    root: Path | None,
) -> list[Violation]:
    """Run named content scanners (e.g. entropy-based secret detection); return violations."""
    if not detector.scan_checks:
        return []
    allow_paths, allow_values = _load_secrets_allowlist(root) if root is not None else ((), frozenset())
    if root is not None and bool(allow_paths) and _is_excluded(file_path, root, list(allow_paths)):
        return []
    violations: list[Violation] = []
    content = "\n".join(lines)
    for lineno, snippet in run_scans(detector.scan_checks, content, allow_values):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if DISABLE_COMMENT in line:
            continue
        violations.append(
            Violation(
                file=file_path,
                line_number=lineno,
                line_content=(line.strip() or snippet)[:120],
                rule=rule,
                severity="warning",
            ),
        )
    return violations


from guideline_checker.core.detection.presence import _check_presence_rules, _declared_violations


def _credential_scan_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    root: Path | None,
) -> list[Violation]:
    """Detect hardcoded credentials via the entropy scanner, not naive substrings.

    A bare ``token =`` / ``password =`` substring flags every variable whose name
    contains a secret keyword — the bulk of them reads from a call, env lookups,
    empty strings, or short placeholders. Routing the rule to ``secret-assignment``
    fires only on a high-entropy quoted literal, so the rule is usable in a
    blocking CI (596 -> ~6 findings on a real repo). See ADR D-0008.
    """
    from guideline_checker.core.derive.seed import _is_hardcoded_credential_rule

    if not _is_hardcoded_credential_rule(rule.lower()):
        return []
    allow_paths, allow_values = _load_secrets_allowlist(root) if root is not None else ((), frozenset())
    if root is not None and bool(allow_paths) and _is_excluded(file_path, root, list(allow_paths)):
        return []
    content = "\n".join(lines)
    violations: list[Violation] = []
    for lineno, snippet in run_scans(("secret-assignment",), content, allow_values):
        line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
        if DISABLE_COMMENT in line:
            continue
        violations.append(
            Violation(
                file=file_path,
                line_number=lineno,
                line_content=(line.strip() or snippet)[:120],
                rule=rule,
                severity="error",
            ),
        )
    return violations
