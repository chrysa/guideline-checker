"""Tests for the Claude CLI proposer — offline, via an injected transport.

Claude is the LLM backend that works everywhere (no local model, no RAM, no
pull): it shells out to the ``claude`` CLI using the user's subscription. Like
every proposer it only proposes — the sandbox still proves the detector before
any write. The ``generate`` callable is injected here so tests never spawn the
CLI and stay deterministic.
"""

from __future__ import annotations

from guideline_checker.loader import RuleDetector
from guideline_checker.proposer import ClaudeProposer, Proposal, Proposer


def _fake(reply: str):
    return lambda _prompt: reply


def test_source_is_claude_and_satisfies_the_seam() -> None:
    proposer = ClaudeProposer(generate=_fake("{}"))
    assert proposer.source == "claude"
    assert isinstance(proposer, Proposer)


def test_parses_reply_into_a_detector() -> None:
    reply = '```json\n{"forbid": ["os.system("], "rationale": "shell injection sink"}\n```'
    proposal = ClaudeProposer(generate=_fake(reply)).propose("Never call os.system")

    assert isinstance(proposal, Proposal)
    assert proposal.source == "claude"
    assert proposal.detector == RuleDetector(forbid=("os.system(",))
    assert proposal.rationale == "shell injection sink"


def test_returns_none_when_reply_has_no_usable_pattern() -> None:
    assert ClaudeProposer(generate=_fake('{"rationale": "cannot detect mechanically"}')).propose("x") is None


def test_cli_failure_yields_none_not_a_crash() -> None:
    def boom(_prompt: str) -> str:
        raise OSError("claude binary not found")

    assert ClaudeProposer(generate=boom).propose("x") is None
