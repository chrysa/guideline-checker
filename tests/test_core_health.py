"""Tests for the deterministic rule-health engine and sandbox proof (``core.health``).

Rule health answers the question the silent-green dashboard never could:
*is this rule capable of detecting anything, and does it actually fire?* It is
computed by the deterministic engine with no LLM and no network — a rule with no
declarative detector **and** no phrase-derived check is ``DEAD`` and can never
flag a violation, however green the scan looks.

The sandbox (below, merged from ``tests/test_sandbox.py``) runs a candidate
detector through the *real* per-file detection path (``core.detection._check_file``)
against the working tree, writing nothing, and returns exactly what it catches:
file, line, excerpt. This is the proof the workshop shows before any write —
the user never has to trust a proposal blind.
"""

from __future__ import annotations

from pathlib import Path

from guideline_checker.core.detection import RuleResult, Violation
from guideline_checker.core.health import (
    HealthState,
    Proof,
    compute_rule_health,
    replay,
)
from guideline_checker.loader import InstructionFile, RuleDetector, SourceType


def _instruction(
    name: str,
    rules: list[str],
    detectors: dict[str, RuleDetector] | None = None,
    source_type: SourceType = SourceType.GUIDELINES_YAML,
) -> InstructionFile:
    return InstructionFile(
        path=Path(f".github/instructions/{name}.md"),
        apply_to="**/*.py",
        description=name,
        content="",
        rules=rules,
        rule_detectors=detectors or {},
        source_type=source_type,
    )


def _markdown(name: str, rules: list[str]) -> InstructionFile:
    return _instruction(name, rules, source_type=SourceType.CLAUDE)


def _result(instruction: InstructionFile, violations: list[Violation]) -> RuleResult:
    return RuleResult(instruction=instruction, violations=violations, files_checked=1)


# ─── Detectability axis (dead vs detectable) — needs no scan ──────────────────


def test_rule_with_declarative_detector_is_not_dead() -> None:
    rule = "no pickle loads"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})

    health = compute_rule_health([instr])

    entry = next(h for h in health if h.rule == rule)
    assert entry.state is not HealthState.DEAD
    assert entry.has_declarative_detector is True


def test_rule_matching_a_known_phrase_is_not_dead() -> None:
    # "no print" maps to a phrase-derived check even without a declarative detector.
    instr = _instruction("python", ["Never use print for debugging output"])

    health = compute_rule_health([instr])

    entry = health[0]
    assert entry.state is not HealthState.DEAD
    assert entry.has_phrase_detection is True


def test_undetectable_yaml_rule_is_dead() -> None:
    # A YAML rule advertised as enforceable but carrying no detector — a real defect.
    instr = _instruction("claude", ["Respond in the user's own language when unsure"])

    health = compute_rule_health([instr])

    entry = health[0]
    assert entry.state is HealthState.DEAD
    assert entry.has_declarative_detector is False
    assert entry.has_phrase_detection is False


def test_undetectable_markdown_bullet_is_advisory_not_dead() -> None:
    # A prose bullet lifted from CLAUDE.md/AGENTS.md — guidance, never an enforced rule.
    instr = _markdown("CLAUDE", ["Prefer intention-revealing names over comments"])

    health = compute_rule_health([instr])

    entry = health[0]
    assert entry.state is HealthState.ADVISORY
    assert entry.has_declarative_detector is False
    assert entry.has_phrase_detection is False


def test_detectable_markdown_bullet_still_proves() -> None:
    # Even from markdown, a phrase-matched bullet is real detection, not advisory.
    instr = _markdown("CLAUDE", ["Never use print for debugging output"])
    fired = _result(instr, [Violation(Path("a.py"), 1, "print(x)", instr.rules[0], "warning")])

    health = compute_rule_health([instr], [fired])

    assert health[0].state is HealthState.PROVEN


def test_max_function_length_phrase_is_detectable() -> None:
    instr = _instruction("python", ["Max function length: 40 lines"])

    health = compute_rule_health([instr])

    assert health[0].state is not HealthState.DEAD
    assert health[0].has_phrase_detection is True


# ─── Fired axis (proven vs armed) — needs a scan result ───────────────────────


def test_detectable_rule_that_fires_is_proven() -> None:
    rule = "no pickle loads"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})
    fired = _result(instr, [Violation(Path("a.py"), 3, "pickle.loads(x)", rule, "error")])

    health = compute_rule_health([instr], [fired])

    assert next(h for h in health if h.rule == rule).state is HealthState.PROVEN


def test_detectable_rule_that_never_fires_is_armed() -> None:
    rule = "no pickle loads"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})
    clean = _result(instr, [])

    health = compute_rule_health([instr], [clean])

    assert next(h for h in health if h.rule == rule).state is HealthState.ARMED


def test_undetectable_yaml_rule_stays_dead_even_with_scan_results() -> None:
    instr = _instruction("claude", ["Respond in the user's own language when unsure"])
    clean = _result(instr, [])

    health = compute_rule_health([instr], [clean])

    assert health[0].state is HealthState.DEAD


def test_summary_counts_group_by_state() -> None:
    advisory = _markdown("CLAUDE", ["Be concise and thoughtful"])
    dead_yaml = _instruction("gemini", ["Keep answers grounded in provided context"])
    proven_rule = "no eval"
    armed_rule = "no pickle loads"
    coded = _instruction(
        "python",
        [proven_rule, armed_rule],
        {
            proven_rule: RuleDetector(forbid=("eval(",)),
            armed_rule: RuleDetector(forbid=("pickle.loads(",)),
        },
    )
    results = [
        _result(advisory, []),
        _result(dead_yaml, []),
        _result(coded, [Violation(Path("a.py"), 1, "eval(x)", proven_rule, "error")]),
    ]

    health = compute_rule_health([advisory, dead_yaml, coded], results)

    states = {h.rule: h.state for h in health}
    assert states["Be concise and thoughtful"] is HealthState.ADVISORY
    assert states["Keep answers grounded in provided context"] is HealthState.DEAD
    assert states[proven_rule] is HealthState.PROVEN
    assert states[armed_rule] is HealthState.ARMED


def test_provenance_flows_into_health_record() -> None:
    """ADR D-0016: a rule's host prose sentence surfaces on its health record so
    the workshop/report can show what host instruction each rule derives from."""
    rule = "no pickle loads"
    sentence = "Never deserialize untrusted data with pickle"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})
    instr.rule_provenance[rule] = sentence

    health = compute_rule_health([instr])

    entry = next(h for h in health if h.rule == rule)
    assert entry.provenance == sentence


def test_missing_provenance_defaults_to_empty() -> None:
    rule = "no pickle loads"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})

    entry = next(h for h in compute_rule_health([instr]) if h.rule == rule)

    assert entry.provenance == ""


def test_health_carries_the_check_kind() -> None:
    """ADR D-0020: every rule reports its generic mechanism (kind)."""
    from guideline_checker.core.detection import CheckKind

    rule = "no pickle loads"
    instr = _instruction("python", [rule], {rule: RuleDetector(forbid=("pickle.loads(",))})
    entry = next(h for h in compute_rule_health([instr]) if h.rule == rule)
    assert entry.kind == CheckKind.FORBIDDEN_PATTERN.value


def test_dead_rule_kind_is_advisory() -> None:
    from guideline_checker.core.detection import CheckKind

    rule = "Structure prompts with XML tags"  # YAML rule, no detector -> dead
    instr = _instruction("ai", [rule])
    entry = next(h for h in compute_rule_health([instr]) if h.rule == rule)
    assert entry.kind == CheckKind.ADVISORY.value


# ─── Sandbox proof (replay a proposed detector, read-only) ────────────────────


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
