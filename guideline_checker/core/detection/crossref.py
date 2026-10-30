"""The cross-reference mechanism: a name cited in one file must have a
matching definition in another (ADR D-0020)."""

from __future__ import annotations

import re
from pathlib import Path

from guideline_checker.core.detection import Violation
from guideline_checker.core.detection.pattern import _compile_regex
from guideline_checker.loader import RuleDetector

# ``define_in`` value asking the lookup to happen inside the citing file itself.
_SELF_REFERENCE = "@self"


def _cross_reference_violations(
    file_path: Path,
    lines: list[str],
    rule: str,
    detector: RuleDetector,
    root: Path | None,
) -> list[Violation]:
    """Flag a citation whose definition cannot be found where it should be.

    Neither file is wrong on its own: the documentation reads fine, and so does
    the file it points at. The defect lives in the gap, which is why every
    single-file mechanism was blind to it.
    """
    reference = detector.cross_reference
    if reference is None:
        return []
    definitions = _definition_text(file_path, lines, reference.define_in, root)
    if definitions is None:
        return []  # nothing to check against: a missing target file is a different defect
    where = ", ".join(reference.define_in)
    violations: list[Violation] = []
    for lineno, line in enumerate(lines, start=1):
        for match in _compile_regex(reference.cite).finditer(line):
            name = match.group(1) if match.groups() else match.group(0)
            shape = reference.define_as.replace("{name}", re.escape(name))
            if _compile_regex(shape).search(definitions):
                continue
            violations.append(
                Violation(
                    file=file_path,
                    line_number=lineno,
                    line_content=f"{name} — not defined in {where}"[:120],
                    rule=rule,
                    severity="warning",
                ),
            )
    return violations


def _definition_text(file_path: Path, lines: list[str], define_in: tuple[str, ...], root: Path | None) -> str | None:
    """Concatenate every resolvable definition source, or ``None`` if none resolve.

    A citation resolves when **any** listed file carries the definition, so the
    sources are joined into one haystack. ``"@self"`` contributes the citing file
    itself. Returning ``None`` only when *nothing* resolves keeps a wholly-missing
    target a separate defect, not a false "undefined" for every mention.
    """
    base = root if root is not None else file_path.parent
    texts: list[str] = []
    for entry in define_in:
        if entry == _SELF_REFERENCE:
            texts.append("\n".join(lines))
            continue
        try:
            texts.append((base / entry).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue  # this source is unavailable; another in the set may still resolve
    return "\n".join(texts) if texts else None
