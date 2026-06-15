"""Core checker: match files against instruction rules."""

from __future__ import annotations

import fnmatch
import functools
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from guideline_checker.ast_javascript import JS_SUFFIXES, run_js_ast_checks
from guideline_checker.ast_python import run_ast_checks
from guideline_checker.loader import InstructionFile, RuleDetector, load_all_sources, load_instructions

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

# Maximum size for scanned files — larger files are generated/compiled artefacts
_MAX_FILE_SIZE: int = 200 * 1024


def _is_text_file(path: Path) -> bool:
    """Return True if the file should be scanned (text-based extension and within size limit)."""
    try:
        if path.stat().st_size > _MAX_FILE_SIZE:
            return False
    except OSError:
        return False
    suffix = path.suffix.lower()
    return suffix in _TEXT_EXTENSIONS or (suffix == "" and path.stat().st_size < 512_000)


# Inline suppression marker — add this comment on any line to skip all rule checks
DISABLE_COMMENT = "guideline: disable"


class PatternCheck(NamedTuple):
    """A single pattern check derived from a rule sentence."""

    pattern: str
    severity: str
    match_in_comments: bool = False


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


def run_checks(
    root: Path,
    instructions_dir: Path | None = None,
    diff_files: list[Path] | None = None,
    all_sources: bool = True,
    exclude: list[str] | None = None,
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
    """
    if all_sources:
        instructions = load_all_sources(root)
    elif instructions_dir is not None:
        instructions = load_instructions(instructions_dir)
    else:
        instructions = load_all_sources(root)
    all_files = diff_files if diff_files is not None else _collect_files(root)

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
            violations.extend(_check_file(fp, instr))
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
        violations = _check_file(file_path, instruction)
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


def _collect_files(root: Path) -> list[Path]:
    """Recursively collect text-based source files, ignoring known irrelevant directories."""
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in IGNORE_DIRS or part.endswith(".egg-info") for part in path.parts)
        and path.name not in IGNORE_FILES
        and _is_text_file(path)
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


def _is_excluded(file_path: Path, root: Path, patterns: list[str]) -> bool:
    """Return True if ``file_path`` (relative to ``root``) matches any exclude pattern.

    A pattern matches when the relative POSIX path equals it, lives beneath it
    (bare-directory exclusion, e.g. ``tests`` skips ``tests/a/b.py``), or matches
    it as a recursive glob (``**`` supported via :meth:`PurePath.full_match`,
    e.g. ``scripts/**/*.py`` or ``**/*.md``).
    """
    try:
        rel = file_path.relative_to(root).as_posix()
    except ValueError:
        return False
    rel_path = PurePosixPath(rel)
    for raw in patterns:
        pat = raw.strip().rstrip("/")
        if not pat:
            continue
        if rel == pat or rel.startswith(pat + "/"):
            return True
        if rel_path.full_match(pat):
            return True
    return False


def _split_patterns(pattern: str) -> list[str]:
    """Split comma-separated glob patterns, respecting brace groups ``{a,b}``.

    A plain ``split(",")`` would break patterns like ``{api,adminzone}/**/*.py``
    by splitting inside the braces.  This function only splits on commas that
    are *not* nested inside ``{…}``.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in pattern:
        if ch == "{":
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _expand_brace_pattern(pattern: str) -> list[str]:
    """Expand ``{a,b}/foo`` into ``[a/foo, b/foo]`` recursively.

    Only the innermost (non-nested) brace group is expanded per call;
    recursion handles nested braces and multiple brace groups.
    """
    m = re.search(r"\{([^{}]+)\}", pattern)
    if not m:
        return [pattern]
    before = pattern[: m.start()]
    after = pattern[m.end() :]
    expanded: list[str] = []
    for alt in m.group(1).split(","):
        expanded.extend(_expand_brace_pattern(before + alt.strip() + after))
    return expanded


def _matches_pattern(file_path: Path, root: Path, pattern: str) -> bool:
    """Check if a file path matches a glob pattern (relative to root).

    Supports ``**`` recursive wildcards via :meth:`pathlib.PurePath.match`
    with a fallback for root-level files (Python 3.12 compat).
    Comma-separated patterns are treated as alternatives (match any).
    Brace expansion ``{a,b}`` is supported (e.g. ``{api,adminzone}/**/*.py``).
    """
    try:
        relative = file_path.relative_to(root)
    except ValueError:
        return False

    # Split on commas NOT inside braces, then expand brace alternatives.
    raw_patterns = _split_patterns(pattern)
    patterns: list[str] = []
    for p in raw_patterns:
        patterns.extend(_expand_brace_pattern(p))

    for pat in patterns:
        if relative.match(pat):
            return True
        # Python 3.12: PurePath.match("**/*.ext") won't match root-level
        # files. Strip the leading **/ and try matching the filename.
        if pat.startswith("**/") and fnmatch.fnmatch(file_path.name, pat[3:]):
            return True
    return False


def _check_file(
    file_path: Path,
    instruction: InstructionFile,
    *,
    cached_lines: list[str] | None = None,
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
        rule_violations.extend(_evaluate_rule(file_path, lines, rule, instruction.rule_detectors.get(rule)))

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
) -> list[Violation]:
    """Evaluate a rule against file lines: phrase-derived checks plus, when the
    rule carries one, its declarative detector. Both paths can fire."""
    violations: list[Violation] = []
    rule_lower = rule.lower()

    # Length-based rules are handled separately (need the full file). A declared
    # detector still runs even for a length rule, so collect it before returning.
    length_violations = _check_length_rules(file_path, lines, rule_lower)
    if length_violations:
        if detector is not None:
            length_violations.extend(_declared_violations(file_path, lines, rule, detector))
        return length_violations

    # Detect common anti-patterns based on rule text
    checks = _build_checks(rule_lower)

    for lineno, line in enumerate(lines, start=1):
        # Inline suppression: skip lines marked with the disable comment
        if DISABLE_COMMENT in line:
            continue
        for check in checks:
            if _line_matches(line, check.pattern, match_in_comments=check.match_in_comments):
                violations.append(
                    Violation(
                        file=file_path,
                        line_number=lineno,
                        line_content=line.strip()[:120],
                        rule=rule,
                        severity=check.severity,
                    ),
                )
                break  # one violation per line per rule

    if detector is not None:
        violations.extend(_declared_violations(file_path, lines, rule, detector))

    return violations


def _declared_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
) -> list[Violation]:
    """Run a rule's declarative detector. Severity is left as ``"warning"`` and
    overridden by the rule's own severity in :func:`_check_file`."""
    violations: list[Violation] = []

    # Per-line patterns: substrings (forbid) and regexes (forbid_regex).
    regexes = tuple(_compile_regex(p) for p in detector.forbid_regex)
    for lineno, line in enumerate(lines, start=1):
        if DISABLE_COMMENT in line:
            continue
        matched = any(
            _line_matches(line, pat, match_in_comments=detector.match_in_comments) for pat in detector.forbid
        ) or _line_passes_regex(line, regexes, match_in_comments=detector.match_in_comments)
        if matched:
            violations.append(
                Violation(
                    file=file_path,
                    line_number=lineno,
                    line_content=line.strip()[:120],
                    rule=rule,
                    severity="warning",
                ),
            )

    # Whole-file (structural / multiline) patterns.
    if detector.file_regex:
        content = "\n".join(lines)
        for pattern in detector.file_regex:
            for match in _compile_regex(pattern).finditer(content):
                lineno = content.count("\n", 0, match.start()) + 1
                line = lines[lineno - 1] if 0 < lineno <= len(lines) else ""
                if DISABLE_COMMENT in line:
                    continue
                violations.append(
                    Violation(
                        file=file_path,
                        line_number=lineno,
                        line_content=line.strip()[:120],
                        rule=rule,
                        severity="warning",
                    ),
                )

    # Precise AST checks. Python uses the stdlib ``ast`` engine, JS/TS the tree-sitter
    # engine; each parses once and an unmatched suffix / syntax error yields nothing.
    # Findings carry the AST snippet but keep the file's actual line.
    if detector.ast_checks:
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


def _line_passes_regex(line: str, regexes: tuple[re.Pattern[str], ...], *, match_in_comments: bool) -> bool:
    """Return True if any compiled regex matches the line (comment rules as for substrings)."""
    if not regexes:
        return False
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return any(rx.search(line) for rx in regexes)


@functools.cache
def _compile_regex(pattern: str) -> re.Pattern[str]:
    """Compile (and cache) a declarative-detector regex, case-insensitive and multiline."""
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


@functools.cache
def _build_checks(rule_lower: str) -> tuple[PatternCheck, ...]:
    """Build anti-pattern checks from rule text.

    Cached so that identical rules across multiple instruction sources are
    only compiled once — eliminates O(rules x files) redundant work.
    """
    checks: list[PatternCheck] = []
    checks.extend(_debug_output_checks(rule_lower))
    checks.extend(_exception_checks(rule_lower))
    checks.extend(_dangerous_builtin_checks(rule_lower))
    checks.extend(_import_checks(rule_lower))
    checks.extend(_annotation_checks(rule_lower))
    checks.extend(_hygiene_checks(rule_lower))
    checks.extend(_credential_checks(rule_lower))
    checks.extend(_typescript_checks(rule_lower))
    checks.extend(_python_strict_checks(rule_lower))
    checks.extend(_security_checks(rule_lower))
    checks.extend(_docker_checks(rule_lower))
    checks.extend(_django_checks(rule_lower))
    return tuple(checks)


def _check_presence_rules(file_path: Path, lines: list[str], file_content: str, rule: str) -> list[Violation]:
    """Check whole-file presence requirements (must include X in every file)."""
    violations: list[Violation] = []
    rule_lower = rule.lower()
    suffix = file_path.suffix

    # "from __future__ import annotations in every file" (Python only)
    if (
        suffix == ".py"
        and "from __future__ import annotations" in rule_lower
        and "from __future__ import annotations" not in file_content
    ):
        violations.append(
            Violation(
                file=file_path,
                line_number=1,
                line_content="Missing: from __future__ import annotations",
                rule=rule,
                severity="warning",
            )
        )

    # "health endpoint is mandatory" / "/health endpoint" (Python/TS API files)
    if (
        suffix in (".py", ".ts")
        and "/health" in rule_lower
        and "mandatory" in rule_lower
        and '"/ health"' not in file_content
        and "'/health'" not in file_content
        and "@app" in file_content
    ):
        violations.append(
            Violation(
                file=file_path,
                line_number=1,
                line_content="Missing: /health endpoint (mandatory)",
                rule=rule,
                severity="warning",
            )
        )

    # Max function/method length
    match = re.search(r"max\s+function\s+length[:\s]+(\d+)", rule_lower) or re.search(
        r"max\s+(\d+)\s+lines?\s+(?:per\s+)?function", rule_lower
    )
    if match and suffix == ".py":
        limit = int(match.group(1))
        violations.extend(_check_function_lengths(file_path, lines, limit))

    return violations


def _check_function_lengths(file_path: Path, lines: list[str], limit: int) -> list[Violation]:
    """Flag Python functions that exceed a line-count limit."""
    violations: list[Violation] = []
    func_start: int | None = None
    func_name = ""

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if re.match(r"(async\s+)?def\s+\w+", stripped):
            if func_start is not None:
                length = lineno - func_start
                if length > limit:
                    violations.append(
                        Violation(
                            file=file_path,
                            line_number=func_start,
                            line_content=f"Function '{func_name}' has {length} lines (limit: {limit})",
                            rule=f"max function length: {limit}",
                            severity="warning",
                        )
                    )
            func_start = lineno
            match = re.search(r"def\s+(\w+)", stripped)
            func_name = match.group(1) if match else "<anonymous>"

    # Check last function
    if func_start is not None:
        length = len(lines) - func_start + 1
        if length > limit:
            violations.append(
                Violation(
                    file=file_path,
                    line_number=func_start,
                    line_content=f"Function '{func_name}' has {length} lines (limit: {limit})",
                    rule=f"max function length: {limit}",
                    severity="warning",
                )
            )

    return violations


def _check_length_rules(file_path: Path, lines: list[str], rule_lower: str) -> list[Violation]:
    """Check file/function length rules that operate on the whole file."""
    violations: list[Violation] = []

    # Max file length: "max file length: N" or "max N lines per file"
    match = re.search(r"max(?:imum)?\s+file\s+length[:\s]+(\d+)", rule_lower) or re.search(
        r"max\s+(\d+)\s+lines?\s+per\s+file", rule_lower
    )
    if match:
        limit = int(match.group(1))
        if len(lines) > limit:
            violations.append(
                Violation(
                    file=file_path,
                    line_number=1,
                    line_content=f"File has {len(lines)} lines (limit: {limit})",
                    rule=f"max file length: {limit}",
                    severity="warning",
                )
            )

    return violations


def _debug_output_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if "no print" in rule_lower or "print()" in rule_lower or "never use print" in rule_lower:
        checks.append(PatternCheck("print(", "warning"))
    if "no pprint" in rule_lower or "pprint()" in rule_lower:
        checks.append(PatternCheck("pprint(", "warning"))
    if any(phrase in rule_lower for phrase in ("no console.log", "no `console.log`", "never console.log")):
        checks.append(PatternCheck("console.log(", "warning"))
    if any(phrase in rule_lower for phrase in ("no console.debug", "no `console.debug`")):
        checks.append(PatternCheck("console.debug(", "warning"))
    if any(phrase in rule_lower for phrase in ("no debugger", "no `debugger`")):
        checks.append(PatternCheck("debugger", "warning"))
    return checks


def _exception_checks(rule_lower: str) -> list[PatternCheck]:
    if "no bare except" in rule_lower or "bare `except`" in rule_lower:
        return [PatternCheck("except:", "error")]
    return []


def _dangerous_builtin_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if "no eval" in rule_lower:
        checks.append(PatternCheck("eval(", "error"))
    if "no exec" in rule_lower:
        checks.append(PatternCheck("exec(", "error"))
    return checks


def _import_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no import *", "no wildcard import", "no star import")):
        checks.append(PatternCheck("import *", "error"))
    if any(phrase in rule_lower for phrase in ("no relative import", "absolute import")):
        checks.append(PatternCheck("from . import", "warning"))
        checks.append(PatternCheck("from .. import", "warning"))
    return checks


def _annotation_checks(_rule_lower: str) -> list[PatternCheck]:
    # Handled by _check_presence_rules (whole-file absence check) — no per-line pattern needed.
    return []


def _hygiene_checks(rule_lower: str) -> list[PatternCheck]:
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("no todo", "no todos", "avoid todo")):
        checks.append(PatternCheck("TODO", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no fixme", "avoid fixme")):
        checks.append(PatternCheck("FIXME", "warning", match_in_comments=True))
    if any(phrase in rule_lower for phrase in ("no hack", "avoid hack")):
        checks.append(PatternCheck("HACK", "warning", match_in_comments=True))
    if "no assert" in rule_lower and "test" not in rule_lower:
        checks.append(PatternCheck("assert ", "warning"))
    if any(phrase in rule_lower for phrase in ("no magic number", "no magic string", "magic number")):
        # Cannot detect statically without full AST, but flag obvious cases
        pass
    return checks


def _docker_checks(rule_lower: str) -> list[PatternCheck]:
    """Docker / container checks."""
    checks: list[PatternCheck] = []
    if any(phrase in rule_lower for phrase in ("run as non-root", "non-root user", "no root")):
        checks.append(PatternCheck("USER root", "error"))
    if "no latest tag" in rule_lower or "never use :latest" in rule_lower:
        checks.append(PatternCheck(":latest", "warning"))
    if "no add instruction" in rule_lower or "use copy not add" in rule_lower:
        checks.append(PatternCheck("\nADD ", "warning"))
    return checks


def _credential_checks(rule_lower: str) -> list[PatternCheck]:
    _secret_keywords = ("secret", "password", "credential", "key", "token", "api key", "api_key")
    is_hardcoded_check = any(
        phrase in rule_lower
        for phrase in ("no hardcoded", "hardcoded api", "hardcoded secret", "never hardcode", "all via env")
    )
    if not is_hardcoded_check or not any(kw in rule_lower for kw in _secret_keywords):
        return []
    return [
        PatternCheck(kw, "error")
        for kw in ("password =", "password=", "secret =", "secret=", "api_key =", "api_key=", "token =", "token=")
    ]


def _typescript_checks(rule_lower: str) -> list[PatternCheck]:
    """TypeScript / React anti-pattern checks."""
    checks: list[PatternCheck] = []
    if "no any" in rule_lower or "no `any`" in rule_lower or "avoid any" in rule_lower:
        checks.append(PatternCheck(": any", "error"))
        checks.append(PatternCheck("as any", "error"))
    if "no ts-ignore" in rule_lower or "no @ts-ignore" in rule_lower:
        checks.append(PatternCheck("@ts-ignore", "error", match_in_comments=True))
    if "no ts-nocheck" in rule_lower or "no @ts-nocheck" in rule_lower:
        checks.append(PatternCheck("@ts-nocheck", "error", match_in_comments=True))
    if "no console.log" in rule_lower:
        checks.append(PatternCheck("console.log(", "warning"))
    if "no console.debug" in rule_lower:
        checks.append(PatternCheck("console.debug(", "warning"))
    if "no console.warn" in rule_lower:
        checks.append(PatternCheck("console.warn(", "warning"))
    if "no inline style" in rule_lower or "no inline styles" in rule_lower:
        checks.append(PatternCheck("style={{", "warning"))
    return checks


def _python_strict_checks(rule_lower: str) -> list[PatternCheck]:
    """Strict Python quality checks."""
    checks: list[PatternCheck] = []
    if "no global" in rule_lower and "global statement" in rule_lower:
        checks.append(PatternCheck("global ", "error"))
    if "no pass in except" in rule_lower or "no silent exception" in rule_lower:
        checks.append(PatternCheck("except:", "error"))
    if "no mutable default" in rule_lower:
        checks.append(PatternCheck("=[]", "warning"))
        checks.append(PatternCheck("={}", "warning"))
    if "no type: ignore" in rule_lower or "no type:ignore" in rule_lower:
        checks.append(PatternCheck("type: ignore", "error", match_in_comments=True))
        checks.append(PatternCheck("type:ignore", "error", match_in_comments=True))
    return checks


def _security_checks(rule_lower: str) -> list[PatternCheck]:
    """Security-oriented checks (OWASP-aligned)."""
    checks: list[PatternCheck] = []
    if "no hardcoded url" in rule_lower or "no hardcoded urls" in rule_lower:
        checks.append(PatternCheck("http://", "warning"))
        checks.append(PatternCheck("https://", "info"))
    if "no hardcoded ip" in rule_lower:
        checks.append(PatternCheck("127.0.0.1", "warning"))
        checks.append(PatternCheck("0.0.0.0", "warning"))
    if "no shell=true" in rule_lower or "no shell injection" in rule_lower:
        checks.append(PatternCheck("shell=True", "error"))
    if "no pickle" in rule_lower:
        checks.append(PatternCheck("import pickle", "error"))
        checks.append(PatternCheck("pickle.load", "error"))
    return checks


def _django_checks(rule_lower: str) -> list[PatternCheck]:
    """Django / DRF anti-pattern checks (settings hardening + ORM safety).

    The markdown rule loader strips underscores from rule text (``ALLOWED_HOSTS``
    becomes ``allowedhosts``), so trigger phrases are matched against an
    underscore-stripped copy. The emitted patterns keep underscores because they
    are matched against source lines, which retain them.
    """
    checks: list[PatternCheck] = []
    compact = rule_lower.replace("_", "")
    if "no debug = true" in compact or "debug must be false" in compact:
        checks.append(PatternCheck("debug = true", "error"))
        checks.append(PatternCheck("debug=true", "error"))
    if "no wildcard allowedhosts" in compact or "allowedhosts wildcard" in compact:
        checks.append(PatternCheck('allowed_hosts = ["*"', "error"))
        checks.append(PatternCheck("allowed_hosts = ['*'", "error"))
        checks.append(PatternCheck('allowed_hosts=["*"', "error"))
        checks.append(PatternCheck("allowed_hosts=['*'", "error"))
    if "no corsallowall" in compact or "corsallowallorigins" in compact:
        checks.append(PatternCheck("cors_allow_all_origins = true", "error"))
        checks.append(PatternCheck("cors_allow_all_origins=true", "error"))
    if "no raw sql" in compact or "no .raw(" in compact or "no queryset.raw" in compact:
        checks.append(PatternCheck(".raw(", "warning"))
        checks.append(PatternCheck(".extra(", "warning"))
    if "no hardcoded secretkey" in compact or "secretkey from env" in compact:
        checks.append(PatternCheck('secret_key = "', "error"))
        checks.append(PatternCheck("secret_key = '", "error"))
    return checks


def _line_matches(line: str, pattern: str, *, match_in_comments: bool = False) -> bool:
    """Check if a line contains a pattern (case-insensitive, ignoring comments by default)."""
    stripped = line.strip()
    if not match_in_comments and stripped.startswith(("#", "//", "*", "'")):
        return False
    return pattern.lower() in stripped.lower()
