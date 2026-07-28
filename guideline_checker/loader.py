"""Load and parse instruction/guideline files from multiple sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_APPLY_TO_RE = re.compile(r"^applyTo:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^description:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\d+\.\s+")
_TABLE_SEP_RE = re.compile(r"^[-:]+$")
_CONSTRAINT_KEYWORDS = frozenset(("must", "never", "always", "forbidden", "required", "non-negotiable", "mandatory"))


class SourceType(StrEnum):
    """Origin of an instruction/constraint file."""

    COPILOT_INSTRUCTION = "copilot-instruction"  # .github/instructions/*.instructions.md
    COPILOT_GLOBAL = "copilot"  # .github/copilot-instructions.md
    CLAUDE = "claude"  # CLAUDE.md, .claude/CLAUDE.md
    AGENTS = "agents"  # AGENTS.md, .claude/agents/*.md
    GUIDELINES_YAML = "guidelines"  # guidelines/<dimension>/*.yml structured referential


@dataclass(frozen=True)
class CrossReference:
    """A claim in one file that must be backed by a definition in another.

    Every other mechanism reads one file in isolation, so a whole family of
    defects was invisible: documentation that cites a command nobody defined, a
    CSS variable used but never declared. What they share is that neither file is
    wrong on its own — the defect lives in the gap between them.

    ``cite`` captures a name where the claim is made; ``define_as`` is the shape
    that name must take in ``define_in`` (``{name}`` is substituted with the
    captured text, escaped). ``define_in`` is a path relative to the scanned root,
    or ``"@self"`` to look the definition up in the citing file itself.
    """

    cite: str
    define_in: str
    define_as: str


@dataclass(frozen=True)
class RuleDetector:
    """A declarative detector a structured rule carries inline.

    Lets a YAML rule say *how* it is detected instead of relying on the
    checker recognising its prose. Lives in the loader (not the checker) so
    both ``guidelines`` and ``checker`` can reference it without a cycle. The
    detector inherits the rule's own severity — it carries none of its own.
    """

    forbid: tuple[str, ...] = ()  # per-line, case-insensitive substring
    forbid_regex: tuple[str, ...] = ()  # per-line, case-insensitive regex
    file_regex: tuple[str, ...] = ()  # whole-file regex (MULTILINE | IGNORECASE)
    require_regex: tuple[str, ...] = ()  # whole-file regex that MUST match — absence is the violation
    ast_checks: tuple[str, ...] = ()  # named Python AST checks (see ast_python.VALID_AST_CHECKS)
    scan_checks: tuple[str, ...] = ()  # named content scanners (see scanners.VALID_SCANS)
    cross_reference: CrossReference | None = None  # a citation here, its definition elsewhere
    match_in_comments: bool = False  # applies to forbid / forbid_regex


@dataclass(frozen=True)
class RuleFix:
    """A declarative, mechanical autofix a rule carries inline (the ``fix:`` block).

    Anchored to a violation's line: ``remove_line`` drops the whole line, while
    ``replace`` / ``regex_replace`` rewrite it. Deterministic and idempotent — no
    semantic or LLM rewriting. See ADR D-0017.
    """

    op: str  # "remove_line" | "replace" | "regex_replace"
    # replace: literal from -> to. regex_replace: pattern -> replacement. remove_line: unused.
    search: str = ""
    replacement: str = ""


@dataclass
class InstructionFile:
    path: Path
    apply_to: str
    description: str
    content: str
    source_type: SourceType = SourceType.COPILOT_INSTRUCTION
    rules: list[str] = field(default_factory=list)
    # Maps a rule statement to its explicit severity ("error"/"warning"/"info").
    # Populated only by structured sources (YAML referential); empty for markdown
    # sources, where severity stays pattern-derived in the checker.
    rule_severity: dict[str, str] = field(default_factory=dict)
    # Maps a rule statement to its declarative detector. Populated only by YAML
    # rules that carry a ``detect:`` block; empty otherwise, so phrase-derived
    # detection stays the sole path for markdown sources.
    rule_detectors: dict[str, RuleDetector] = field(default_factory=dict)
    # Maps a rule statement to its declarative autofix. Populated only by YAML rules
    # that carry a ``fix:`` block; a rule with no entry here is detect-only (ADR D-0017).
    rule_fixes: dict[str, RuleFix] = field(default_factory=dict)
    # Maps a rule statement to the host prose sentence it was derived from (ADR
    # D-0016). Populated only by YAML rules carrying a ``provenance:`` field; a
    # rule with no entry was hand-authored and traces to nothing in host prose.
    rule_provenance: dict[str, str] = field(default_factory=dict)


def load_instructions(instructions_dir: Path) -> list[InstructionFile]:
    """Load all *.instructions.md files from a directory (backward compatible)."""
    result: list[InstructionFile] = []
    for path in sorted(instructions_dir.glob("*.instructions.md")):
        instruction = _parse_instruction_file(path)
        if instruction is not None:
            result.append(instruction)
    return result


def load_all_sources(root: Path) -> list[InstructionFile]:
    """Discover and load all instruction sources from a project root.

    Searches for:

    - ``.github/instructions/*.instructions.md``  → :attr:`SourceType.COPILOT_INSTRUCTION`
    - ``.github/copilot-instructions.md``         → :attr:`SourceType.COPILOT_GLOBAL`
    - ``CLAUDE.md``, ``.claude/CLAUDE.md``        → :attr:`SourceType.CLAUDE`
    - ``AGENTS.md``, ``.claude/agents/*.md``      → :attr:`SourceType.AGENTS`
    - ``guidelines/<dimension>/*.yml``            → :attr:`SourceType.GUIDELINES_YAML`
    """
    sources: list[InstructionFile] = []

    # 1. Copilot per-pattern instruction files
    instructions_dir = root / ".github" / "instructions"
    if instructions_dir.is_dir():
        sources.extend(load_instructions(instructions_dir))

    # 2. Global Copilot instructions
    copilot_global = root / ".github" / "copilot-instructions.md"
    if copilot_global.is_file():
        inst = _parse_markdown_file(
            copilot_global,
            source_type=SourceType.COPILOT_GLOBAL,
            description="GitHub Copilot — global instructions",
        )
        if inst:
            sources.append(inst)

    # 3. Claude instruction files
    for claude_path in _find_claude_files(root):
        inst = _parse_markdown_file(
            claude_path,
            source_type=SourceType.CLAUDE,
            description=f"Claude — {claude_path.name}",
        )
        if inst:
            sources.append(inst)

    # 4. Agent definition files
    for agents_path in _find_agents_files(root):
        inst = _parse_markdown_file(
            agents_path,
            source_type=SourceType.AGENTS,
            description=f"Agents — {agents_path.name}",
        )
        if inst:
            sources.append(inst)

    # 5. Structured YAML rule referential (guidelines/<dimension>/*.yml)
    guidelines_dir = root / "guidelines"
    if guidelines_dir.is_dir():
        # Imported lazily so the markdown-only path never imports PyYAML.
        from guideline_checker.guidelines import load_yaml_guidelines

        sources.extend(load_yaml_guidelines(root))

    return sources


def _find_claude_files(root: Path) -> list[Path]:
    """Return CLAUDE.md and .claude/CLAUDE.md if they exist."""
    candidates = [
        root / "CLAUDE.md",
        root / ".claude" / "CLAUDE.md",
    ]
    return [p for p in candidates if p.is_file()]


def _find_agents_files(root: Path) -> list[Path]:
    """Return AGENTS.md and all .claude/agents/*.md files."""
    result: list[Path] = []
    agents_md = root / "AGENTS.md"
    if agents_md.is_file():
        result.append(agents_md)
    agents_dir = root / ".claude" / "agents"
    if agents_dir.is_dir():
        result.extend(sorted(agents_dir.glob("*.md")))
    return result


def _parse_instruction_file(path: Path) -> InstructionFile | None:
    """Parse a single .instructions.md file (with optional YAML frontmatter)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None

    apply_to = "**/*"
    description = path.stem

    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        frontmatter = fm_match.group(1)
        apply_match = _APPLY_TO_RE.search(frontmatter)
        if apply_match:
            apply_to = apply_match.group(1).strip()
        desc_match = _DESCRIPTION_RE.search(frontmatter)
        if desc_match:
            description = desc_match.group(1).strip()
        content = raw[fm_match.end() :]
    else:
        content = raw
        apply_match = _APPLY_TO_RE.search(raw)
        if apply_match:
            apply_to = apply_match.group(1).strip()
        desc_match = _DESCRIPTION_RE.search(raw)
        if desc_match:
            description = desc_match.group(1).strip()

    rules = _extract_rules(content)

    return InstructionFile(
        path=path,
        apply_to=apply_to,
        description=description,
        content=content,
        source_type=SourceType.COPILOT_INSTRUCTION,
        rules=rules,
    )


def _parse_markdown_file(
    path: Path,
    *,
    source_type: SourceType,
    description: str,
) -> InstructionFile | None:
    """Parse a generic markdown instruction file (no frontmatter, no applyTo)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None

    rules = _extract_rules(content)

    return InstructionFile(
        path=path,
        apply_to="**/*",
        description=description,
        content=content,
        source_type=source_type,
        rules=rules,
    )


def _strip_markdown(text: str) -> str:
    """Remove inline markdown bold/italic/code markers."""
    return re.sub(r"\*{1,2}|_{1,2}|`", "", text).strip()


def _rule_text_from_line(stripped: str) -> str | None:
    """Extract a rule text from a single stripped markdown line, or return None."""
    if stripped.startswith(("- ", "* ")):
        return stripped[2:].strip()
    if _NUMBERED_RE.match(stripped):
        return _NUMBERED_RE.sub("", stripped).strip()
    if stripped.startswith("|") and stripped.endswith("|"):
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        joined = " — ".join(c for c in cells if c and not _TABLE_SEP_RE.match(c))
        if any(kw in joined.lower() for kw in _CONSTRAINT_KEYWORDS):
            return joined
    return None


def _extract_rules(content: str) -> list[str]:
    """Extract rule statements from markdown content.

    Detects:

    - Bullet list items (``- text`` or ``* text``)
    - Numbered list items (``1. text``)
    - Table rows that contain at least one constraint keyword
      (must / never / always / forbidden / required / non-negotiable / mandatory)
    """
    rules: list[str] = []
    seen: set[str] = set()

    for line in content.splitlines():
        rule_text = _rule_text_from_line(line.strip())
        if not rule_text:
            continue
        clean = _strip_markdown(rule_text)
        if len(clean) > 10 and clean not in seen:
            seen.add(clean)
            rules.append(clean)

    return rules
