"""ADR D-0016 validation gate (b): the tool ships MECHANISMS, never VALUES.

No numeric standard (file/function length, coverage, cyclomatic complexity) may
appear as a literal in the engine's own source. Those values belong to the host
and are read from its prose/config at runtime — never baked into
``guideline_checker/**``. This is the code-side grep gate the target
architecture requires; ``guidelines/*.yml`` is exempt because it is a per-repo
derived cache, not tool code.
"""

from __future__ import annotations

import re
from pathlib import Path

import guideline_checker

# A threshold *value* = a standards keyword immediately followed by a 2+ digit
# number. Deliberately narrow to stay zero-false-positive: a bare ``500_000``
# byte guard or ``range(50)`` carries no keyword and never matches; an
# incidental "Python 3.12 compat" comment is a version reference, not a
# threshold, and is out of scope for this gate.
_THRESHOLD_LITERAL = re.compile(
    r"(?i)(file length|lines? per (?:file|function)|max (?:file|function)"
    r"|coverage|cyclomatic|complexity)[^A-Za-z0-9]{0,12}[0-9]{2,}"
)

_PACKAGE_DIR = Path(guideline_checker.__file__).parent


def _source_files() -> list[Path]:
    return [p for p in _PACKAGE_DIR.rglob("*.py") if "__pycache__" not in p.parts]


class TestNoEmbeddedThresholds:
    def test_scan_finds_the_package(self) -> None:
        # Guard the guard: an empty file list would make the gate vacuously pass.
        assert len(_source_files()) >= 10

    def test_no_threshold_literal_in_engine_source(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path in _source_files():
            hits = _THRESHOLD_LITERAL.findall(path.read_text(encoding="utf-8"))
            if hits:
                offenders[path.name] = hits
        assert not offenders, (
            "ADR D-0016 gate (b): threshold value baked into engine source "
            f"(read it from the host instead): {offenders}"
        )
