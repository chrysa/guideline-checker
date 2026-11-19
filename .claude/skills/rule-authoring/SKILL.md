---
name: rule-authoring
description: 'Use when drafting or persisting a new guideline-checker detection rule: proposing a detector from example text (proposer.py), interpreting/validating it, and writing it into guidelines/<dimension>/*.yml via persist.py. Not for editing unrelated YAML.'
---

# Rule authoring — draft, prove, persist

## When to invoke
Auto-invoke when: adding a new detector to `guidelines/<dimension>/*.yml`, running
`guideline_checker/workshop/proposer.py` to derive a rule from example text, or calling
`write_derived_ruleset()` in `guideline_checker/workshop/persist.py`.

## Workflow

1. **Draft** — use `guideline_checker/workshop/proposer.py` to turn example
   violation/compliant text into a candidate `RuleDetector`.
2. **Interpret** — pass the candidate through `guideline_checker/workshop/interpret.py`
   to normalize it against the existing rule schema (`detect:` / `detect.ast:` blocks).
3. **Prove** — run the checker against a fixture containing both a violating and a
   compliant snippet:
   ```bash
   guideline-checker check --root <fixture_dir> --fail-on error
   ```
   Confirm the new rule fires on the violation and stays silent on the compliant case.
4. **Persist (dry run first)** — call
   `write_derived_ruleset(root, derived, dry_run=True)` from `persist.py` and review the
   diff before writing. Re-run with `dry_run=False` only after review.
5. **Categorize** — every new rule needs a `category` matching an entry in
   `guidelines/categories.yml` (`prompt-format`, `architecture`, `stack`, `naming`,
   `testing`, `security`, `correctness`, `data`, `observability`).

## Guardrails

- Never hand-edit a rule's `detect.ast` block without running it through
  `interpret.py` first — the AST query syntax is tree-sitter-specific and easy to get
  wrong silently (rule never fires, no error).
- A rule with no fixture proving both a hit and a miss is not done — flag it, don't
  merge it.
- Persist changes are additive by default (`write_derived_ruleset`); do not delete
  existing rule entries in the same change.
