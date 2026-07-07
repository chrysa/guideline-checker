"""Project configuration file support (L2.3).

Read committed, reproducible settings from ``[tool.guideline-checker]`` in
``pyproject.toml`` (fallback: a top-level or ``[tool.guideline-checker]`` table in
``.guideline-checker.toml``). The CLI resolves effective values with precedence
**CLI flag > env var > config file > built-in default**, so a repo can pin its
gate behaviour in version control while a run may still override per-invocation.

Only the keys in :data:`KNOWN_KEYS` are honoured; unknown keys and values of the
wrong type are dropped with a warning rather than crashing the run.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_FAIL_ON_CHOICES = frozenset({"error", "warning", "never"})

KNOWN_KEYS = frozenset({"fail_on", "exclude", "max_file_size", "linters", "baseline"})


@dataclass
class ProjectConfig:
    """Cleaned, typed config values plus human-readable warnings for what was dropped."""

    values: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def load_config(root: Path) -> ProjectConfig:
    """Load and validate the ``[tool.guideline-checker]`` config under ``root``."""
    config = ProjectConfig()
    for key, raw in _read_raw(root).items():
        if key not in KNOWN_KEYS:
            config.warnings.append(f"unknown key '{key}'")
            continue
        cleaned = _VALIDATORS[key](raw)
        if cleaned is None:
            config.warnings.append(f"invalid value for '{key}': {raw!r}")
        else:
            config.values[key] = cleaned
    return config


def _as_str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _as_int(value: object) -> int | None:
    # bool is an int subclass — reject it so `max_file_size = true` is not silently 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_fail_on(value: object) -> str | None:
    return value if value in _FAIL_ON_CHOICES else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


_VALIDATORS = {
    "fail_on": _as_fail_on,
    "exclude": _as_str_list,
    "max_file_size": _as_int,
    "linters": _as_str_list,
    "baseline": _as_str,
}


def _extract_table(data: dict[str, object]) -> dict[str, object] | None:
    tool = data.get("tool")
    if isinstance(tool, dict):
        section = tool.get("guideline-checker")
        if isinstance(section, dict):
            return section
    return None


def _read_raw(root: Path) -> dict[str, object]:
    """Return the raw config mapping: pyproject's tool table, else the dedicated TOML."""
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        table = _extract_table(tomllib.loads(pyproject.read_text(encoding="utf-8")))
        if table is not None:
            return table

    dedicated = root / ".guideline-checker.toml"
    if dedicated.is_file():
        data = tomllib.loads(dedicated.read_text(encoding="utf-8"))
        table = _extract_table(data)
        return table if table is not None else data

    return {}
