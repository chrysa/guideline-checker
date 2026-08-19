"""The proposer seam — turn a rule statement into a candidate ``detect:`` block.

A ``Proposer`` proposes; it never judges. Every proposal is replayed by the
deterministic engine in the sandbox and shown with its proof before any write
(see ``sandbox.replay`` and ADR D-0012). The LLM backends (Ollama, Claude) live
behind the optional ``[assist]`` extra; the ``HeuristicProposer`` here needs no
extra and no network — it recycles the checker's own phrase table, so a dead
rule whose prose the checker already recognises gets an armed detector for free,
tried before any model is called.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from guideline_checker.core.detection.pattern import _build_checks
from guideline_checker.loader import RuleDetector

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class Proposal:
    """A candidate detector for a rule, plus why it was proposed and by whom."""

    rule: str
    detector: RuleDetector
    rationale: str
    source: str  # "heuristic" | "ollama" | "claude"


@runtime_checkable
class Proposer(Protocol):
    """A backend that proposes a detector for a rule, or ``None`` if it cannot."""

    source: str

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None: ...


class HeuristicProposer:
    """Propose a detector by recycling the checker's phrase table — no LLM.

    If ``_build_checks`` recognises the rule's prose, its anti-pattern substrings
    become the proposal's ``forbid`` list. Rules the table cannot map (semantic or
    provider-specific conventions) return ``None`` so the router escalates to an LLM.
    """

    source = "heuristic"

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None:
        checks = _build_checks(rule.lower())
        if not checks:
            return None
        forbid = tuple(dict.fromkeys(check.pattern for check in checks))
        match_in_comments = any(check.match_in_comments for check in checks)
        joined = ", ".join(forbid)
        return Proposal(
            rule=rule,
            detector=RuleDetector(forbid=forbid, match_in_comments=match_in_comments),
            rationale=f"Recognised {len(forbid)} known anti-pattern substring(s): {joined}.",
            source=self.source,
        )


_LLM_PROMPT = """\
You propose a mechanical detector for a coding rule. Reply with ONE JSON object and nothing \
else, using only these keys:
  "forbid": list of case-insensitive substrings whose presence on a source line violates the \
rule (e.g. "os.system(");
  "forbid_regex": list of case-insensitive regexes for the same, when a substring is not \
precise enough;
  "match_in_comments": true only if the rule must also flag comments;
  "rationale": one short sentence.
Only return patterns that would actually appear in violating code. If the rule cannot be \
detected mechanically from source text, return {{}}.

Rule: {rule}
JSON:"""


@dataclass
class OllamaProposer:
    """Propose a detector via a local Ollama model — the LLM backend of the seam.

    The model only *proposes*: every proposal is still replayed in the sandbox
    for proof before any write (ADR D-0012). Lives behind the optional
    ``[assist]`` extra conceptually; the transport is stdlib ``urllib`` so it
    adds no runtime dependency. ``generate`` is injectable for offline tests.
    """

    model: str = "qwen2.5:7b"
    host: str = "http://localhost:11434"
    timeout: float = 30.0
    generate: Callable[[str], str] | None = field(default=None, repr=False)
    source: str = field(default="ollama", init=False)

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None:
        generate = self.generate or self._call_ollama
        try:
            reply = generate(_LLM_PROMPT.format(rule=rule))
        except (OSError, urllib.error.URLError):
            return None
        return _proposal_from_reply(rule, reply, self.source)

    def _call_ollama(self, prompt: str) -> str:
        payload = json.dumps(
            {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed localhost Ollama endpoint
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        return str(body.get("response", ""))


@dataclass
class ClaudeProposer:
    """Propose a detector via the ``claude`` CLI — the portable LLM backend.

    Shells out to ``claude -p`` on the user's subscription (no API key, no local
    model, no RAM). Like every proposer it only proposes: the sandbox proves the
    detector before any write (ADR D-0012). ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_KEY``
    are stripped from the child env so ``claude -p`` uses the subscription session
    instead of exiting on a stray key. ``generate`` is injectable for offline tests.
    """

    binary: str = "claude"
    timeout: float = 120.0
    generate: Callable[[str], str] | None = field(default=None, repr=False)
    source: str = field(default="claude", init=False)

    def propose(self, rule: str, apply_to: str = "**/*") -> Proposal | None:
        generate = self.generate or self._call_claude
        try:
            reply = generate(_LLM_PROMPT.format(rule=rule))
        except (OSError, subprocess.SubprocessError):
            return None
        return _proposal_from_reply(rule, reply, self.source)

    def _call_claude(self, prompt: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_KEY"}}
        result = subprocess.run(
            [self.binary, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            check=True,
        )
        return result.stdout


def _proposal_from_reply(rule: str, reply: str, source: str) -> Proposal | None:
    """Build a Proposal from a model reply, or None if it carries no usable pattern."""
    data = _extract_json(reply)
    if data is None:
        return None
    detector = RuleDetector(
        forbid=tuple(_str_list(data.get("forbid"))),
        forbid_regex=tuple(_str_list(data.get("forbid_regex"))),
        file_regex=tuple(_str_list(data.get("file_regex"))),
        match_in_comments=bool(data.get("match_in_comments", False)),
    )
    if not (detector.forbid or detector.forbid_regex or detector.file_regex):
        return None
    rationale = str(data.get("rationale") or "LLM-proposed detector")
    return Proposal(rule=rule, detector=detector, rationale=rationale, source=source)


def _extract_json(reply: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a possibly chatty / fenced model reply."""
    match = _JSON_OBJECT.search(reply)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _str_list(value: object) -> list[str]:
    """Coerce a model-supplied value into a clean list of non-empty strings."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
