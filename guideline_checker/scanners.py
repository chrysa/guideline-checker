"""Named, language-agnostic content scanners for declarative ``detect.scan`` checks.

Mirrors the AST-check registries (``ast_python`` / ``ast_javascript``): a YAML rule
names a scanner in ``detect.scan``, :mod:`guidelines` validates it against
:data:`VALID_SCANS`, and the checker runs it over a file's content. Scanners are
stdlib-only and never raise — an unmatched name simply yields nothing.

The shipped ``secret-assignment`` scanner finds ``<key> = "<value>"`` assignments
whose key names a secret and whose value is high-entropy — random API keys and
tokens sit around 4-5 bits/char, whereas dictionary-word placeholders (``changeme``)
sit around 2-3, so a Shannon-entropy gate cuts the bulk of the false positives that
a plain ``forbid``/regex detector produces. Environment lookups and allowlisted
values are skipped outright.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

# Key tokens that name a secret-bearing assignment target.
_SECRET_KEY_RE = re.compile(
    r"(secret|password|passwd|pwd|token|api[_-]?key|secret[_-]?key|access[_-]?key|"
    r"auth[_-]?token|private[_-]?key|client[_-]?secret|credential)",
    re.IGNORECASE,
)
# ``<key> <assign> <quote><value><quote>`` — assign is ``=`` or ``:`` (py / ts / yaml / json).
_ASSIGNMENT_RE = re.compile(
    r"""(?P<key>[A-Za-z_][\w.\-]*)\s*[:=]\s*(?P<q>['"])(?P<value>[^'"\n]{8,})(?P=q)""",
)
# Values that are clearly environment-driven references or templated placeholders.
_ENV_REF_RE = re.compile(r"(os\.environ|getenv|process\.env|\$\{?[A-Za-z_]|<[^>]+>|\{\{)", re.IGNORECASE)

# Shannon bits/char gate and minimum value length for a value to count as a secret.
_MIN_ENTROPY = 3.5
_MIN_VALUE_LEN = 12

VALID_SCANS: frozenset[str] = frozenset({"secret-assignment"})


def unknown_scans(names: Sequence[str]) -> list[str]:
    """Names that are not a registered content scanner."""
    return [n for n in names if n not in VALID_SCANS]


def run_scans(
    names: Sequence[str],
    content: str,
    allowed_values: frozenset[str] = frozenset(),
) -> list[tuple[int, str]]:
    """Run the named scanners over ``content``; return ``(line_number, snippet)`` findings."""
    findings: list[tuple[int, str]] = []
    for name in names:
        if name == "secret-assignment":
            findings.extend(_scan_secret_assignment(content, allowed_values))
    return findings


def shannon_entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character."""
    if not value:
        return 0.0
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in (value.count(char) for char in set(value)))


def _scan_secret_assignment(content: str, allowed_values: frozenset[str]) -> list[tuple[int, str]]:
    """Flag high-entropy secret assignments, skipping env lookups and allowlisted values."""
    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        match = _ASSIGNMENT_RE.search(line)
        if match is None:
            continue
        key, value = match.group("key"), match.group("value")
        if not _SECRET_KEY_RE.search(key):
            continue
        if value in allowed_values:
            continue
        if _ENV_REF_RE.search(value):
            continue
        if len(value) < _MIN_VALUE_LEN or shannon_entropy(value) < _MIN_ENTROPY:
            continue
        findings.append((lineno, line.strip()[:120]))
    return findings
