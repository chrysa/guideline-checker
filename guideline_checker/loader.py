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
# A definition entry: a short code-or-bold *term*, a dash, then its meaning
# ("- `exempt:config` — no executable runtime"). Two or more in one list is a
# glossary — the bullets define the allowed *values* of a field, they impose
# nothing. ADR D-0016: a value read as a mechanism is a constraint no code change
# can satisfy, which is issue #255. The length cap is what separates a term from a
# sentence that merely happens to open with inline code.
_DEFINITION_TERM_MAX = 40
# The separator is spelled with escapes, not literals: an em/en dash is visually
# indistinguishable from a hyphen in source, and ruff RUF001 flags the ambiguity.
_DEFINITION_DASHES = "\\u2014\\u2013-"  # em dash, en dash, hyphen
_DEFINITION_RE = re.compile(
    rf"^(?:`[^`]{{1,{_DEFINITION_TERM_MAX}}}`|\*\*[^*]{{1,{_DEFINITION_TERM_MAX}}}\*\*)"
    rf"\s*[{_DEFINITION_DASHES}]\s+\S"
)
# Below this, a run of definition-shaped bullets reads as an ordinary rule list.
_GLOSSARY_MIN_ENTRIES = 2


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
    captured text, escaped). ``define_in`` is one or more paths relative to the
    scanned root (a single string in YAML becomes a one-element tuple), or the
    sentinel ``"@self"`` to look the definition up in the citing file itself. A
    citation resolves when **any** of the listed files carries the definition —
    the CSS-variable case, where a custom property may be declared in any of
    several stylesheets.
    """

    cite: str
    define_in: tuple[str, ...]
    define_as: str


@dataclass(frozen=True)
class NumericThreshold:
    """A metric to measure and the bound it must not cross.

    The engine owns the *measuring* (see :mod:`guideline_checker.metrics`); this
    carries the host's chosen metric name and bound, and nothing else. Keeping the
    number here — read from ``guidelines/*.yml`` — rather than in engine code is
    ADR D-0016's line: mechanisms in the tool, values in the host.

    ``max_value`` is spelled out because the YAML key is ``max``, which shadows a
    builtin. This validator is the only place the two names meet.
    """

    metric: str
    max_value: int


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
    stale_after_days: int | None = None  # a matching file older than this is stale (file-freshness kind)
    numeric_threshold: NumericThreshold | None = None  # a measured metric vs a bound (numeric-threshold kind)
    exclude: tuple[str, ...] = ()  # paths this detector must not judge (see checker._is_excluded)
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
    # Maps a rule statement to the chrysa standards rule id it mechanises (e.g.
    # "FE-070"), for GV-012 traceability. Populated only by YAML rules carrying a
    # ``standard:`` field; a rule with no entry enforces a socle-prose rule or a
    # generic idiom with no single owning annexe id.
    rule_standard: dict[str, str] = field(default_factory=dict)


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


def _is_glossary(block: list[str]) -> bool:
    """True for a run of bullets that defines the allowed values of a field.

    An imperative anywhere in the block disqualifies it: a list that both defines
    and demands is still read, because dropping it would lose a real constraint.
    """
    if len(block) < _GLOSSARY_MIN_ENTRIES:
        return False
    if any(kw in text.lower() for text in block for kw in _CONSTRAINT_KEYWORDS):
        return False
    return sum(1 for text in block if _DEFINITION_RE.match(text)) >= _GLOSSARY_MIN_ENTRIES


def _list_blocks(content: str) -> list[list[str]]:
    """Split content into maximal runs of consecutive list items.

    A glossary is a *list*, not a line, so the definition test needs the whole run
    to judge: one dash-bullet among prose is an ordinary rule.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        rule_text = _rule_text_from_line(line.strip())
        if rule_text:
            current.append(rule_text)
            continue
        if current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _extract_rules(content: str) -> list[str]:
    """Extract rule statements from markdown content.

    Detects:

    - Bullet list items (``- text`` or ``* text``)
    - Numbered list items (``1. text``)
    - Table rows that contain at least one constraint keyword
      (must / never / always / forbidden / required / non-negotiable / mandatory)

    Skips **definition lists** — a run of two or more ``term — meaning`` entries
    carrying no imperative. Those define a field's allowed values; reading one as a
    constraint produces a finding no code change can satisfy (see :func:`_is_glossary`).
    """
    rules: list[str] = []
    seen: set[str] = set()

    for block in _list_blocks(content):
        if _is_glossary(block):
            continue
        for rule_text in block:
            clean = _strip_markdown(rule_text)
            if len(clean) > 10 and clean not in seen:
                seen.add(clean)
                rules.append(clean)

    return rules
