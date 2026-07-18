"""Tests for the sandbox — replay a proposed detector for proof.

The sandbox runs a candidate detector through the *real* per-file detection path
(``checker._check_file``) against the working tree, writing nothing, and returns
exactly what it catches: file, line, excerpt. This is the proof the workshop
shows before any write — the user never has to trust a proposal blind.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.loader import RuleDetector
from guideline_checker.sandbox import Proof, replay


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("x = 1\nprint(x)\nvalue = 2\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("y = 1\nreturn y\n", encoding="utf-8")
    return tmp_path


def test_replay_reports_matching_lines_as_proof(tmp_path: Path) -> None:
    proof = replay("no print", RuleDetector(forbid=("print(",)), _repo(tmp_path))

    assert isinstance(proof, Proof)
    assert proof.match_count == 1
    hit = proof.hits[0]
    assert hit.file == "app.py"
    assert hit.line == 2
    assert "print(" in hit.excerpt


def test_replay_writes_nothing(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = (root / "app.py").read_text(encoding="utf-8")

    replay("no print", RuleDetector(forbid=("print(",)), root)

    assert (root / "app.py").read_text(encoding="utf-8") == before


def test_replay_of_a_matchless_detector_proves_zero(tmp_path: Path) -> None:
    proof = replay("no pdb", RuleDetector(forbid=("import pdb",)), _repo(tmp_path))

    assert proof.match_count == 0
    assert proof.hits == []
    assert proof.files_scanned >= 2


def test_replay_honours_apply_to_glob(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "notes.md").write_text("print(hello)\n", encoding="utf-8")

    proof = replay("no print", RuleDetector(forbid=("print(",)), root, apply_to="**/*.py")

    assert all(h.file.endswith(".py") for h in proof.hits)
    assert proof.match_count == 1
