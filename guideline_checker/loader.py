"""Load and parse .instructions.md files from the instructions directory."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_APPLY_TO_RE = re.compile(r"^applyTo:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^description:\s*[\"']?(.+?)[\"']?\s*$", re.MULTILINE)


@dataclass
class InstructionFile:
    path: Path
    apply_to: str
    description: str
    content: str
    rules: list[str] = field(default_factory=list)


def load_instructions(instructions_dir: Path) -> list[InstructionFile]:
    """Load all *.instructions.md files from a directory."""
    result: list[InstructionFile] = []
    for path in sorted(instructions_dir.glob("*.instructions.md")):
        instruction = _parse_instruction_file(path)
        if instruction is not None:
            result.append(instruction)
    return result


def _parse_instruction_file(path: Path) -> InstructionFile | None:
    """Parse a single .instructions.md file."""
    raw = path.read_text(encoding="utf-8")

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
        rules=rules,
    )


def _extract_rules(content: str) -> list[str]:
    """Extract rule lines (lines starting with - or * that appear to be constraints)."""
    rules: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- **", "* **", "- ", "* ")):
            rule_text = stripped.lstrip("-* ").strip()
            if len(rule_text) > 10:
                rules.append(rule_text)
    return rules
