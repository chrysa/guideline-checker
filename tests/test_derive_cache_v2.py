"""Tests for core/derive/cache.py — local ephemeral cache (spec §6 determinism claim)."""

from pathlib import Path

from guideline_checker.core.derive.cache import cache_path, load, prose_hash, store
from guideline_checker.loader import CrossReference, NumericThreshold, RuleDetector


def test_prose_hash_is_deterministic_for_same_inputs() -> None:
    assert prose_hash("No print", "1.0.0") == prose_hash("No print", "1.0.0")


def test_prose_hash_changes_when_engine_version_changes() -> None:
    assert prose_hash("No print", "1.0.0") != prose_hash("No print", "1.0.1")


def test_store_then_load_round_trips(tmp_path: Path) -> None:
    detector = RuleDetector(forbid=("print(",))
    key = prose_hash("No print", "1.0.0")
    store(tmp_path, key, detector)
    assert load(tmp_path, key) == detector


def test_load_returns_none_on_cache_miss(tmp_path: Path) -> None:
    assert load(tmp_path, "unknown-key") is None


def test_cache_path_honours_env_override(tmp_path: Path, monkeypatch) -> None:
    override = tmp_path / "custom-cache"
    monkeypatch.setenv("GUIDELINE_CACHE_DIR", str(override))
    assert cache_path(tmp_path) == override
    monkeypatch.delenv("GUIDELINE_CACHE_DIR")
    assert cache_path(tmp_path) == tmp_path / ".guideline-cache"


def test_multi_element_tuple_field_round_trips_as_a_real_tuple(tmp_path: Path) -> None:
    """A tuple field with more than one element must come back as an equal,
    genuine tuple — not a list that merely compares loosely with `==`.

    (`RuleDetector(forbid=["a", "b"]) == RuleDetector(forbid=("a", "b"))` is
    False in Python, since list != tuple, so this only passes if `load`
    actually reconstructs a tuple.)
    """
    detector = RuleDetector(
        forbid=("print(", "eval(", "exec("),
        forbid_regex=(r"^import os$",),
        match_in_comments=True,
    )
    key = prose_hash("No dangerous calls", "1.0.0")
    store(tmp_path, key, detector)

    loaded = load(tmp_path, key)

    assert loaded == detector
    assert isinstance(loaded.forbid, tuple)
    assert loaded.forbid == ("print(", "eval(", "exec(")
    assert isinstance(loaded.forbid_regex, tuple)


def test_nested_dataclass_fields_round_trip(tmp_path: Path) -> None:
    """`cross_reference` and `numeric_threshold` are nested dataclasses (with
    their own tuple field, in CrossReference's case) that must reconstruct as
    real instances, not raw dicts."""
    detector = RuleDetector(
        cross_reference=CrossReference(
            cite="FOO_BAR",
            define_in=("src/config.py", "src/other.py"),
            define_as="FOO_BAR = {name}",
        ),
        numeric_threshold=NumericThreshold(metric="coverage", max_value=80),
    )
    key = prose_hash("Cross-ref and threshold rule", "1.0.0")
    store(tmp_path, key, detector)

    loaded = load(tmp_path, key)

    assert loaded == detector
    assert isinstance(loaded.cross_reference, CrossReference)
    assert isinstance(loaded.cross_reference.define_in, tuple)
    assert isinstance(loaded.numeric_threshold, NumericThreshold)
