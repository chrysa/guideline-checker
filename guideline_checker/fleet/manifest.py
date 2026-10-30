"""Load the chrysa fleet manifest (``repos.yml``) into audit targets.

Only ``status: dev`` repos are audited. Per-repo applicability is declared in an
optional ``distribution:`` mapping (opt-out: ``license/standards/precommit: false``);
absent keys default to applicable. Legacy ``public``/``runtime`` are intentionally
not reused (their semantics differ — see DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RepoTarget:
    name: str
    owner: str = "chrysa"
    license_applicable: bool = True
    standards_applicable: bool = True
    precommit_applicable: bool = True


def _flag(dist: dict[str, object], key: str) -> bool:
    value = dist.get(key, True)
    return value is not False


def load_manifest(path: Path, owner: str = "chrysa") -> list[RepoTarget]:
    text = path.read_text(encoding="utf-8")  # raises FileNotFoundError when absent
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("repos"), list):
        raise ValueError(f"Malformed manifest (expected a top-level 'repos' list): {path}")
    targets: list[RepoTarget] = []
    for entry in data["repos"]:
        if not isinstance(entry, dict) or entry.get("status") != "dev":
            continue
        dist = entry.get("distribution") or {}
        if not isinstance(dist, dict):
            dist = {}
        targets.append(
            RepoTarget(
                name=str(entry["name"]),
                owner=owner,
                license_applicable=_flag(dist, "license"),
                standards_applicable=_flag(dist, "standards"),
                precommit_applicable=_flag(dist, "precommit"),
            )
        )
    return targets
