"""Load and cross-validate every ``config/fleet-scan/*.yaml`` file.

All problems are collected before failing, so one ``validate-config`` run lists every
defect at once (:class:`ConfigValidationError.problems`). YAML is read with
``yaml.safe_load`` only. Fingerprints are SHA-256 over a canonical JSON rendering of the
effective configuration; the token *value* is never part of the configuration at all
(only the name of the environment variable that holds it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from guideline_checker.fleet.scan.errors import ConfigValidationError
from guideline_checker.fleet.scan.models import (
    IMPLICIT_PROFILE,
    METHOD_TYPES,
    ConformanceConfig,
    GithubConfig,
    Project,
    RepoRef,
    ReportersConfig,
    ReposConfig,
)

GITHUB_FILE = "github.yaml"
CONFORMANCE_FILE = "conformance.yaml"
REPOS_FILE = "repos.yaml"
REPORTERS_FILE = "reporters.yaml"
STANDARDS_MAPPING_FILE = "standards-mapping.yaml"
SKILLS_MAPPING_FILE = "skills-mapping.yaml"

# Keys whose values name rule ids / profiles inside the (loosely-shaped) mapping files.
_RULE_KEYS = frozenset({"rule", "rule_id", "rules", "rule_ids", "related_rule_ids"})
_PROFILE_KEYS = frozenset({"profile", "profiles", "applies_to_profiles"})

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class CatalogEntry:
    """A repository of the catalog together with the project that owns it."""

    project: Project
    repo: RepoRef


@dataclass(frozen=True)
class EffectiveConfig:
    """The validated configuration of one scan, plus its derived fingerprints."""

    config_dir: Path
    github: GithubConfig
    conformance: ConformanceConfig
    repos: ReposConfig
    reporters: ReportersConfig
    standards_mapping: Any = None
    skills_mapping: Any = None
    warnings: tuple[str, ...] = ()
    catalog: dict[str, CatalogEntry] = field(default_factory=dict)

    @property
    def config_fingerprint(self) -> str:
        """SHA-256 of everything that can change *results*.

        From ``github.yaml`` only ``api_url``, ``pagination`` and ``content`` qualify:
        concurrency, timeouts, retries, rate limits, cache and logging change how evidence
        is fetched, never what a rule concludes. Reporters are excluded for the same reason.
        """
        github = self.github.model_dump(mode="json", include={"api_url", "pagination", "content"})
        payload = {
            "github": github,
            "conformance": self.conformance.model_dump(mode="json"),
            "repos": self.repos.model_dump(mode="json"),
            "standards_mapping": self.standards_mapping,
            "skills_mapping": self.skills_mapping,
        }
        return sha256_canonical(payload)

    @property
    def rules_sha256(self) -> str:
        return sha256_canonical(self.conformance.model_dump(mode="json"))

    def lookup(self, full_name: str) -> CatalogEntry | None:
        return self.catalog.get(full_name.lower())


def sha256_canonical(payload: Any) -> str:  # noqa: ANN401 - any JSON-able structure
    """SHA-256 of a canonical JSON rendering (sorted keys, no whitespace)."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_config(config_dir: Path) -> EffectiveConfig:
    """Load + validate the whole config dir; raise :class:`ConfigValidationError` listing every problem."""
    return ConfigLoader(config_dir=config_dir).load()


class ConfigLoader:
    """Reads the config dir, validates each file, then cross-validates them together."""

    def __init__(self, *, config_dir: Path) -> None:
        self._dir = config_dir
        self._problems: list[str] = []
        self._warnings: list[str] = []

    def load(self) -> EffectiveConfig:
        if not self._dir.is_dir():
            raise ConfigValidationError([f"config dir not found: {self._dir}"])
        github = self._model(filename=GITHUB_FILE, model=GithubConfig, required=False)
        conformance = self._model(filename=CONFORMANCE_FILE, model=ConformanceConfig, required=True)
        repos = self._model(filename=REPOS_FILE, model=ReposConfig, required=True)
        reporters = self._model(filename=REPORTERS_FILE, model=ReportersConfig, required=False)
        standards = self._raw(filename=STANDARDS_MAPPING_FILE, required=False)
        skills = self._raw(filename=SKILLS_MAPPING_FILE, required=False)

        catalog: dict[str, CatalogEntry] = {}
        if conformance is not None:
            self._check_conformance(conformance)
            self._check_mapping(filename=STANDARDS_MAPPING_FILE, doc=standards, conformance=conformance)
            self._check_mapping(filename=SKILLS_MAPPING_FILE, doc=skills, conformance=conformance)
        if repos is not None:
            catalog = self._check_repos(repos=repos, conformance=conformance)
        if self._problems or conformance is None or repos is None:
            raise ConfigValidationError(self._problems)
        return EffectiveConfig(
            config_dir=self._dir,
            github=github or GithubConfig(),
            conformance=conformance,
            repos=repos,
            reporters=reporters or ReportersConfig(),
            standards_mapping=standards,
            skills_mapping=skills,
            warnings=tuple(self._warnings),
            catalog=catalog,
        )

    # ── file loading ──────────────────────────────────────────────────────────

    def _raw(self, *, filename: str, required: bool) -> Any:  # noqa: ANN401 - raw YAML document
        path = self._dir / filename
        if not path.is_file():
            if required:
                self._problems.append(f"{filename}: missing (required)")
            else:
                self._warnings.append(f"{filename}: absent, defaults/no cross-check used")
            return None
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
            self._problems.append(f"{filename}: cannot be parsed: {exc}")
            return None

    def _model(self, *, filename: str, model: type[ModelT], required: bool) -> ModelT | None:
        doc = self._raw(filename=filename, required=required)
        if doc is None:
            return None
        if not isinstance(doc, Mapping):
            self._problems.append(f"{filename}: top level must be a mapping")
            return None
        try:
            return model.model_validate(doc)
        except ValidationError as exc:
            self._problems.extend(_format_validation_error(filename=filename, doc=doc, exc=exc))
            return None

    # ── cross-validation ──────────────────────────────────────────────────────

    def _check_conformance(self, conformance: ConformanceConfig) -> None:
        known = {*conformance.profiles, IMPLICIT_PROFILE}
        if IMPLICIT_PROFILE in conformance.profiles:
            self._problems.append(
                f"{CONFORMANCE_FILE}: profile '{IMPLICIT_PROFILE}' is implicit and must not be declared"
            )
        seen: set[str] = set()
        for rule in conformance.rules:
            if rule.id in seen:
                self._problems.append(f"{CONFORMANCE_FILE}: duplicate rule id {rule.id}")
            seen.add(rule.id)
            for name in rule.profiles:
                if name not in known:
                    self._problems.append(f"{CONFORMANCE_FILE}: rule {rule.id} references unknown profile '{name}'")
        for name, profile in conformance.profiles.items():
            for ref in profile.detect.none_of_profiles or []:
                if ref not in conformance.profiles:
                    self._problems.append(f"{CONFORMANCE_FILE}: profile '{name}' none_of_profiles unknown '{ref}'")
                elif conformance.profiles[ref].detect.none_of_profiles:
                    self._problems.append(
                        f"{CONFORMANCE_FILE}: profile '{name}' none_of_profiles '{ref}' "
                        "is itself negative (no chaining)"
                    )

    def _check_mapping(self, *, filename: str, doc: Any, conformance: ConformanceConfig) -> None:  # noqa: ANN401
        if doc is None:
            return
        rule_ids = {rule.id for rule in conformance.rules}
        profiles = {*conformance.profiles, IMPLICIT_PROFILE}
        for kind, value in _walk_references(doc):
            if kind == "rule" and value not in rule_ids:
                self._problems.append(f"{filename}: references unknown rule id '{value}'")
            elif kind == "profile" and value not in profiles:
                self._problems.append(f"{filename}: references unknown profile '{value}'")

    def _check_repos(self, *, repos: ReposConfig, conformance: ConformanceConfig | None) -> dict[str, CatalogEntry]:
        catalog = self._index_catalog(repos)
        if conformance is not None:
            profiles = {*conformance.profiles, IMPLICIT_PROFILE}
            for entry in catalog.values():
                explicit = entry.repo.scan_profile if isinstance(entry.repo.scan_profile, list) else []
                for name in explicit:
                    if name not in profiles:
                        self._problems.append(
                            f"{REPOS_FILE}: repo {entry.repo.full_name} scan_profile unknown '{name}'"
                        )
        self._check_subsets(repos=repos, catalog=catalog)
        return catalog

    def _index_catalog(self, repos: ReposConfig) -> dict[str, CatalogEntry]:
        """Case-insensitive ``full_name`` index; duplicate projects/repos are problems."""
        catalog: dict[str, CatalogEntry] = {}
        project_ids: set[str] = set()
        for project in repos.projects:
            if project.id in project_ids:
                self._problems.append(f"{REPOS_FILE}: duplicate project id '{project.id}'")
            project_ids.add(project.id)
            for repo in project.repos:
                key = repo.full_name.lower()
                if key in catalog:
                    other = catalog[key].project.id
                    self._problems.append(
                        f"{REPOS_FILE}: repo {repo.full_name} listed twice (projects '{other}' and '{project.id}')"
                    )
                    continue
                catalog[key] = CatalogEntry(project=project, repo=repo)
        return catalog

    def _check_subsets(self, *, repos: ReposConfig, catalog: dict[str, CatalogEntry]) -> None:
        for subset, members in repos.subsets.items():
            for member in members:
                if member.lower() not in catalog:
                    self._problems.append(f"{REPOS_FILE}: subset '{subset}' references {member}, absent from catalog")
            if len({m.lower() for m in members}) != len(members):
                self._problems.append(f"{REPOS_FILE}: subset '{subset}' contains duplicates")


def _walk_references(node: Any) -> Iterator[tuple[str, str]]:  # noqa: ANN401 - raw YAML node
    """Yield ``("rule", id)`` / ``("profile", name)`` for every reference in a mapping document."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in _RULE_KEYS:
                yield from (("rule", ref) for ref in _names(value))
            elif key in _PROFILE_KEYS:
                yield from (("profile", ref) for ref in _names(value))
            if isinstance(value, Mapping | list):
                yield from _walk_references(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_references(item)


def _names(value: Any) -> Iterator[str]:  # noqa: ANN401 - raw YAML node
    """Names in a reference value: a string, a list of strings, or a list of ``{id: ...}`` mappings."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item
            elif isinstance(item, Mapping):
                ref = item.get("id", item.get("rule_id"))
                if isinstance(ref, str):
                    yield ref


def _format_validation_error(*, filename: str, doc: Mapping[str, Any], exc: ValidationError) -> list[str]:
    """One readable line per pydantic error, naming the rule id instead of its list index."""
    lines: list[str] = []
    for error in exc.errors():
        loc = list(error["loc"])
        where = _locate(doc=doc, loc=loc)
        if error["type"] == "union_tag_invalid":
            tag = error.get("ctx", {}).get("tag", error.get("input"))
            lines.append(f"{filename}: {where}: unknown method type {tag!r}; allowed: {', '.join(METHOD_TYPES)}")
        else:
            lines.append(f"{filename}: {where}: {error['msg']}")
    return lines


def _locate(*, doc: Mapping[str, Any], loc: list[int | str]) -> str:
    """Render a pydantic ``loc``; ``rules[3]`` becomes ``rules[DOC-001]`` when the id is readable."""
    parts: list[str] = []
    node: Any = doc
    for step in loc:
        label = str(step)
        if isinstance(step, int) and isinstance(node, list) and step < len(node):
            item = node[step]
            ident = item.get("id", item.get("full_name")) if isinstance(item, Mapping) else None
            label = f"[{ident}]" if isinstance(ident, str) else f"[{step}]"
            node = item
        else:
            node = node.get(step) if isinstance(node, Mapping) else None
            label = f".{label}" if parts else label
        parts.append(label)
    return "".join(parts) or "<root>"
