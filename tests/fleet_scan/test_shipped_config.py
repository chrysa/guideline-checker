"""The configuration shipped in ``config/fleet-scan`` loads and keeps its rule semantics."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from guideline_checker.fleet.scan.config import EffectiveConfig, load_config
from guideline_checker.fleet.scan.models import CommitMessagesAbsentMethod

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "fleet-scan"

# Attribution strings are assembled from parts so this file carries none of them verbatim.
ASSISTANT = "Cl" + "aude"
CO_AUTHOR = "Co-Authored-" + "By: " + ASSISTANT + " <noreply@" + "anthropic.com>"
SESSION = ASSISTANT + "-Session: https://claude" + ".ai/code/session_01ABC"


@pytest.fixture(scope="module")
def shipped(tmp_path_factory: pytest.TempPathFactory) -> EffectiveConfig:
    # The real catalog (repos.yaml) is private and git-ignored: load the shipped rules
    # with the public example catalog in its place.
    config_dir = tmp_path_factory.mktemp("shipped") / "fleet-scan"
    shutil.copytree(SHIPPED, config_dir, ignore=shutil.ignore_patterns("repos.yaml"))
    shutil.copyfile(SHIPPED / "repos.example.yaml", config_dir / "repos.yaml")
    return load_config(config_dir)


def test_private_catalog_is_not_shipped() -> None:
    ignored = (SHIPPED.parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "config/fleet-scan/repos.yaml" in ignored


def _attribution_pattern(config: EffectiveConfig) -> re.Pattern[str]:
    rule = next(r for r in config.conformance.rules if r.id == "CONF-010")
    assert isinstance(rule.method, CommitMessagesAbsentMethod)
    return re.compile(rule.method.params.pattern)


def test_shipped_catalog_holds_no_archived_repo(shipped: EffectiveConfig) -> None:
    assert shipped.catalog
    assert not any(entry.repo.archived for entry in shipped.catalog.values())


@pytest.mark.parametrize(
    "message",
    [
        f"feat: x\n\n{CO_AUTHOR}\n",
        f"fix: y\n\nbody line\n{SESSION}\n",
    ],
    ids=["co-author-trailer", "session-link-trailer"],
)
def test_attribution_rule_flags_trailers(shipped: EffectiveConfig, message: str) -> None:
    assert _attribution_pattern(shipped).search(message)


def test_attribution_rule_ignores_plain_mentions(shipped: EffectiveConfig) -> None:
    message = f"docs: explain the {ASSISTANT}-Session trailer rule\n\nsee CLAUDE.md\n"
    assert not _attribution_pattern(shipped).search(message)
