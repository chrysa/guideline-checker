# Rule Health Workshop — Design

> Status: draft · Date: 2026-07-18 · Branch: `feat/rule-health-workshop`
> Supersedes the read-only web dashboard as the project's primary surface.

## 1. Why (the problem this rethink answers)

`guideline-checker` is technically complete (22 tags, 92% coverage, all L1/L2
lots shipped) and strategically orphaned. Its own Notion `Gate de validation`
("≥2 repos run it in blocking CI") reads **0**; its `Hypothèse mortelle`
("dead unless adopted beyond the pilot") is **realised**. It is installed in
exactly one repo other than itself.

The root cause is a **silent green**. Observed live on 2026-07-18 by running the
dashboard against this very repo: every instruction source reports
`violations: []`, including the 8 `ai-models/` rules that carry **no detector**
and can therefore never fire. The tool passes green whether a rule works or is
dead prose. A green that proves nothing is why it is not installed elsewhere —
the author already voted with his behaviour (1 repo of 79).

Three concrete lies compound the trust problem and are fixed in P1:

- `pip install guideline-checker` + PyPI badge → the package returns **404**
  (PyPI publish was explicitly rejected in D-0009; distribution is ghcr image +
  pre-commit git ref).
- README asserts the 26 rules all have a working `detect:`, "none are dead
  prose" → false; 8 `ai-models/` rules have no detector.
- Notion card: `Cycle: ✅ Prod`, `Maturité: 5`, `Critère de sunset: (empty)`
  while the kill-test is failing.

## 2. What we are building

**A local workshop that makes the rule referential alive, proven, and
repairable** — a cockpit (the card already classes the project `C3 · DOC +
COCKPIT`), not a reporting dashboard.

The new central object, which exists nowhere today: **rule health**, computed by
the deterministic engine, never by an LLM.

| State | Meaning |
|---|---|
| 🟢 **Proven** | has a `detect:`, and it fires on real code in this repo |
| 🔵 **Armed** | has a valid `detect:`, fires nowhere (repo is clean) |
| 🔴 **Dead** | no `detect:` → can never detect anything; invisible today |
| 🟠 **Suspect** | fires, but on lines marked false-positive (suppressions/baseline) |

The 8 `ai-models/` rules go 🔴 immediately. The README claim becomes verifiable
instead of asserted.

### The loop (identical on both sides — rules and code)

```
detect → propose (heuristic first, LLM on escalation) → REPLAY in sandbox
       → show the proof → user validates → write (guidelines/*.yml or working tree)
```

The LLM **proposes, never judges**. Detection stays 100% deterministic, no LLM,
no network. Pre-commit and CI do not change one byte. This is the non-negotiable
trust boundary — it is the only thing the project genuinely owns.

## 3. Components (each testable in isolation)

1. **`rule_health.py`** — computes each rule's state. Deterministic, zero deps,
   zero LLM. Foundation; everything hangs off it. Input: the loaded
   `InstructionFile`s + a scan result. Output: `list[RuleHealth]`.
2. **`proposer/`** — the seam (= the "Router" already named on the Notion card).
   `Proposer.propose(rule, context) -> Proposal`. Implementations:
   `HeuristicProposer` (the 43 `_build_checks` phrases recycled — free, instant),
   `OllamaProposer` (qwen2.5:7b, per the card), `ClaudeProposer` (sonnet, heavy
   tasks). Router tries heuristic first, escalates when it comes up dry.
   Optional extra `[assist]`, **never in core**.
3. **`sandbox.py`** — the piece that makes the product. Replays a proposal
   against the repo, in memory, writing nothing, and returns the proof: "this
   `detect:` catches 3 lines (file:line + excerpt), misses 1, and lights up 2
   false positives here." The user is never asked to trust the LLM blind.
4. **`web/`** — real frontend (Exaggerated Minimalism · `#1E293B`/`#22C55E`/
   `#0F172A` · Fira Code/Fira Sans, per the card). Two tabs: **Rules** (health +
   workshop) and **Code** (violations + fix). Nothing is written to disk without
   the proof shown and a click.

### Trust-boundary restatement

- `_build_checks` (the 43 English phrases) is **demoted from judge to proposer**.
  Its work is not thrown away — it moves to the correct side of the boundary. Its
  dead branches (`_annotation_checks` → `[]`, magic-number → `pass`) are deleted
  or promoted to real YAML rules.
- The checker's verdict path stays deterministic and offline.

## 4. Sequencing (anchored to the kill-test, not to feature completeness)

- **P1 — The truth.** `rule_health` + Rules tab read-only. Zero LLM, zero write.
  Output: the author finally sees which of the 37 rules are dead. **Plus the 3
  lies fixed** (PyPI badge/README, README dead-prose claim, Notion sunset field
  — the Notion write only after per-element validation, per standing rule).
  This alone is worth the trip.
- **P2 — The workshop.** `sandbox` + `HeuristicProposer` + validated write.
  Still zero LLM. Resurrect the 8 dead rules with proof attached.
- **P3 — The LLM.** Ollama/Claude router wired onto the seam. The card asks to
  "prototype 3 Ollama vs Claude outputs, compare" — this becomes a test, not an
  intuition.
- **P4 — The Code tab.** Fix + diff. The easiest, therefore **deliberately
  last** — it is the one that would eat time without serving the gate; `autofix`
  already works from the CLI.
- **P5 — Fleet.** *Unlocked only if ≥2 repos run in blocking CI.* Otherwise not
  built; `central.py` stays frozen.

## 5. What we freeze / kill

- `central.py` (422 LOC): **frozen**, not deleted. Revives in P5 or never.
- `_DASHBOARD_HTML` (412-line string): removed, replaced by the real frontend.
- `_build_checks`: demoted judge → proposer; dead branches removed.
- `cli.py` (1076) and `checker.py` (1040) exceed the repo's own 500-line limit.
  **Not** refactored on principle — only where P1–P3 touch them.

## 6. The risk not hidden

This repairs the **cause** of non-adoption; it does not guarantee adoption. It is
possible that after P2 — rules alive and proven — the author still has no wish to
install it on repo #2, because Ruff/mypy/Sonar already suffice. **That would be a
good outcome**: learned in 2 phases instead of 5 lots, and the answer would be
sunset. The empty `Critère de sunset` is itself a governance bug filled in P1.

## 7. Non-goals

- No PyPI publish (D-0009 stands).
- No remote/pip rule packs.
- No fleet write (PR-on-79-repos) before P5's gate.
- No LLM anywhere in the deterministic verdict path.
