"""Tests for cross-file rule inheritance and distributable rule packs (L2.4 / D-0008)."""

from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.guidelines import GuidelineError, load_yaml_guidelines

_REPO_ROOT = Path(__file__).resolve().parents[1]

_CATEGORIES = "categories:\n  - id: security\n    description: s\n  - id: correctness\n    description: c\n"


def _file(root: Path, dim: str, name: str, body: str) -> None:
    path = root / "guidelines" / dim / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _referential(root: Path) -> None:
    (root / "guidelines").mkdir(exist_ok=True)
    (root / "guidelines" / "categories.yml").write_text(_CATEGORIES, encoding="utf-8")


def _rules(root: Path) -> set[str]:
    return {r for i in load_yaml_guidelines(root) for r in i.rules}


def _detectors(root: Path) -> set[str]:
    return {r for i in load_yaml_guidelines(root) for r in i.rule_detectors}


# ─── Cross-file extends ───────────────────────────────────────────────────────


def test_cross_file_extends_inherits(tmp_path: Path) -> None:
    _referential(tmp_path)
    _file(
        tmp_path,
        "packs",
        "sec.yml",
        'language_target: "*"\nrules:\n'
        "  - id: base\n    abstract: true\n    category: security\n    severity: warning\n"
        "    rule: no weak hash\n    detect:\n      forbid: ['md5(']\n",
    )
    _file(
        tmp_path,
        "languages",
        "python.yml",
        'language_target: python\napply_to_glob: "**/*.py"\nrules:\n'
        "  - id: child\n    extends: base\n    severity: error\n",
    )
    detectors = _detectors(tmp_path)
    assert "no weak hash" in detectors  # child inherited the base's detector cross-file


def test_unknown_extends_base_raises(tmp_path: Path) -> None:
    _referential(tmp_path)
    _file(
        tmp_path,
        "languages",
        "python.yml",
        "language_target: python\nrules:\n  - id: child\n    extends: ghost\n    category: security\n"
        "    severity: error\n    rule: r\n    detect:\n      forbid: ['x']\n",
    )
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


def test_cross_file_extends_cycle_raises(tmp_path: Path) -> None:
    _referential(tmp_path)
    _file(
        tmp_path,
        "languages",
        "a.yml",
        "language_target: python\nrules:\n  - id: a\n    extends: b\n    category: security\n"
        "    severity: error\n    rule: ra\n    detect:\n      forbid: ['x']\n",
    )
    _file(
        tmp_path,
        "languages",
        "b.yml",
        "language_target: python\nrules:\n  - id: b\n    extends: a\n    category: security\n"
        "    severity: error\n    rule: rb\n    detect:\n      forbid: ['y']\n",
    )
    with pytest.raises(GuidelineError, match="cycle"):
        load_yaml_guidelines(tmp_path)


# ─── include: and rule packs ──────────────────────────────────────────────────


def _pack(root: Path) -> None:
    _file(
        root,
        "packs",
        "sec.yml",
        'language_target: "*"\nrules:\n'
        "  - id: base\n    abstract: true\n    category: security\n    severity: warning\n"
        "    rule: base rule\n    detect:\n      forbid: ['md5(']\n"
        "  - id: pack-concrete\n    category: security\n    severity: error\n"
        "    rule: no pickle\n    detect:\n      forbid: ['pickle.loads(']\n",
    )


def test_include_emits_pack_concrete_rules(tmp_path: Path) -> None:
    _referential(tmp_path)
    _pack(tmp_path)
    _file(
        tmp_path,
        "languages",
        "python.yml",
        "language_target: python\ninclude:\n- packs/sec.yml\nrules:\n"
        "  - id: local\n    category: correctness\n    severity: warning\n    rule: local rule\n"
        "    detect:\n      forbid: ['todo']\n",
    )
    rules = _rules(tmp_path)
    assert "no pickle" in rules  # concrete pack rule activated by include
    assert "local rule" in rules


def test_pack_not_emitted_without_include(tmp_path: Path) -> None:
    _referential(tmp_path)
    _pack(tmp_path)
    _file(
        tmp_path,
        "languages",
        "python.yml",
        "language_target: python\nrules:\n"
        "  - id: local\n    category: correctness\n    severity: warning\n    rule: local rule\n"
        "    detect:\n      forbid: ['todo']\n",
    )
    rules = _rules(tmp_path)
    assert "no pickle" not in rules  # pack is parsed but not emitted when not included
    assert "local rule" in rules


def test_abstract_base_never_emitted(tmp_path: Path) -> None:
    _referential(tmp_path)
    _pack(tmp_path)
    _file(
        tmp_path,
        "languages",
        "python.yml",
        "language_target: python\ninclude:\n- packs/sec.yml\nrules: []\n",
    )
    assert "base rule" not in _rules(tmp_path)  # abstract, even though its pack is included


def test_include_missing_pack_raises(tmp_path: Path) -> None:
    _referential(tmp_path)
    _file(tmp_path, "languages", "python.yml", "language_target: python\ninclude:\n- packs/nope.yml\nrules: []\n")
    with pytest.raises(GuidelineError, match="include"):
        load_yaml_guidelines(tmp_path)


def test_include_outside_guidelines_raises(tmp_path: Path) -> None:
    _referential(tmp_path)
    _file(tmp_path, "languages", "python.yml", 'language_target: python\ninclude:\n- "../../etc/x.yml"\nrules: []\n')
    with pytest.raises(GuidelineError):
        load_yaml_guidelines(tmp_path)


# ─── Shipped pack wiring (real referential) ───────────────────────────────────


def test_shipped_security_pack_is_wired() -> None:
    rules = _rules(_REPO_ROOT)
    # py-no-weak-hash inherits the pack base cross-file; pack-no-pickle-loads is activated by include.
    assert "Use a strong hash (SHA-256 or better), never a broken algorithm" in rules
    assert "Never unpickle untrusted data with pickle.loads" in rules
