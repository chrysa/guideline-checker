from __future__ import annotations

from pathlib import Path

import pytest

from guideline_checker.manifest import RepoTarget, load_manifest

_YAML = """
repos:
  - name: alpha
    status: dev
  - name: bravo
    status: non-dev
  - name: charlie
    status: dev
    distribution:
      license: false
      precommit: false
"""


def _write(tmp_path: Path) -> Path:
    p = tmp_path / "repos.yml"
    p.write_text(_YAML, encoding="utf-8")
    return p


class TestLoadManifest:
    def test_keeps_only_dev_repos(self, tmp_path: Path) -> None:
        targets = load_manifest(_write(tmp_path))
        assert [t.name for t in targets] == ["alpha", "charlie"]

    def test_defaults_all_checks_applicable(self, tmp_path: Path) -> None:
        alpha = next(t for t in load_manifest(_write(tmp_path)) if t.name == "alpha")
        assert alpha == RepoTarget(name="alpha", owner="chrysa")
        assert alpha.license_applicable and alpha.standards_applicable and alpha.precommit_applicable

    def test_distribution_opt_out_flags(self, tmp_path: Path) -> None:
        charlie = next(t for t in load_manifest(_write(tmp_path)) if t.name == "charlie")
        assert charlie.license_applicable is False
        assert charlie.precommit_applicable is False
        assert charlie.standards_applicable is True

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path / "absent.yml")
