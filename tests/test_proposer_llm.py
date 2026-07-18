"""Tests for the Ollama LLM proposer — offline, via an injected transport.

The LLM proposes a detector for prose the heuristic cannot map (the ai-models
rules); it never judges — the proposal is still replayed in the sandbox for
proof before any write. These tests inject a fake generator so they need no
running Ollama and stay deterministic.
"""

from __future__ import annotations

from guideline_checker.loader import RuleDetector
from guideline_checker.proposer import OllamaProposer, Proposal, Proposer


def _fake(reply: str):
    return lambda _prompt: reply


def test_source_is_ollama_and_satisfies_the_seam() -> None:
    proposer = OllamaProposer(generate=_fake("{}"))
    assert proposer.source == "ollama"
    assert isinstance(proposer, Proposer)


def test_parses_plain_json_into_a_detector() -> None:
    reply = '{"forbid": ["pickle.loads("], "match_in_comments": false, "rationale": "unsafe deser"}'
    proposal = OllamaProposer(generate=_fake(reply)).propose("Never unpickle untrusted data")

    assert isinstance(proposal, Proposal)
    assert proposal.source == "ollama"
    assert proposal.detector == RuleDetector(forbid=("pickle.loads(",))
    assert proposal.rationale == "unsafe deser"


def test_extracts_json_from_fenced_or_chatty_reply() -> None:
    reply = 'Sure, here it is:\n```json\n{"forbid_regex": ["os\\\\.system\\\\("]}\n```\n'
    proposal = OllamaProposer(generate=_fake(reply)).propose("Never call os.system")

    assert proposal is not None
    assert proposal.detector.forbid_regex == (r"os\.system\(",)


def test_returns_none_when_reply_has_no_json() -> None:
    assert OllamaProposer(generate=_fake("I cannot help with that.")).propose("x") is None


def test_returns_none_when_detector_would_be_empty() -> None:
    # Valid JSON but no usable pattern -> nothing to replay, so no proposal.
    assert OllamaProposer(generate=_fake('{"rationale": "n/a"}')).propose("x") is None


def test_transport_error_yields_none_not_a_crash() -> None:
    def boom(_prompt: str) -> str:
        raise OSError("connection refused")

    assert OllamaProposer(generate=boom).propose("x") is None
