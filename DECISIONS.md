# DECISIONS — guideline-checker

> Repository-local ADRs (Architectural Decision Records). Numbering: D-XXXX.
> Any deviation from [CODE_MANIFEST.md](../../shared-standards/CODE_MANIFEST.md) must be documented here.
> No active deviation → this project follows all chrysa global standards.

---

## D-0001 — Adherence to chrysa global standards

**Date**: 2026-05-25
**Status**: accepted

This project follows all conventions defined in `CODE_MANIFEST.md` (chrysa portfolio standards).
No active deviation is in effect. Any future deviation must be added as a new ADR entry below.

---

## D-0002 — Adopt PyYAML for the structured rule referential

**Date**: 2026-06-09
**Status**: accepted

The multi-dimension rule referential (`guidelines/<dimension>/*.yml`) is authored in
YAML (per the chrysa spec). Parsing it adds **`PyYAML`** as the core CLI's first and only
runtime dependency, ending the previous "zero runtime dependencies" property (markdown
sources still parse with the stdlib `re`-based loader). `PyYAML` is ubiquitous, pure-data,
and stub-typed (`types-PyYAML`), so the cost is minimal.

*Rejected alternative*: TOML + stdlib `tomllib` (zero-dep) — contradicts the spec's YAML
format and reads poorly for nested rule lists.

The referential is **100 % Notion-agnostic**: rule files carry no `source_ref` and no links
back to Notion. Origin traceability lives in git history only. Notion may serve as an
upstream editor, but repatriation to the repo is a one-off copy — once in the repo, rules
are self-sufficient.

---

## D-0003 — Central aggregation server: push model + file-backed store

**Date**: 2026-06-10
**Status**: accepted

A central server (`guideline_checker/web/central.py`, served by `guideline-checker central`)
aggregates compliance across all chrysa repos. The flow is **push, not pull**: each repo's CI
runs `check --json` and posts the report to `POST /api/ingest`; the server keeps the **latest
snapshot per repo** and renders a combined view. Pull (server clones/scans every repo) was
rejected — it would couple the server to every repo's credentials, languages, and build env.

**Storage is file-backed** (one JSON file per repo under `CENTRAL_STORE`), not a database: the
data is a small key→latest-snapshot map, history is not required for the MVP, and this keeps the
server deployable with zero extra infra. A DB can replace the store module later without changing
the API. Repo identifiers are constrained to `^[A-Za-z0-9._-]+$` so they map to filenames without
path traversal.

The **`push` command uses only the standard library** (`urllib`), so reporting repos do not need
the `web` extra (the server side does). Ingest auth reuses the existing `AUTH_MODE` contract.

*Update 2026-06-10*: added a bounded per-repo **history** alongside the latest snapshot — an
append-only `history/<repo>.jsonl` capped at `_HISTORY_LIMIT` points (oldest dropped). It powers
`GET /api/repos/{repo}/history` and the error trend (`▲/▼`) in the aggregated view. Still
file-backed and swappable; the cap keeps growth bounded without a database.

---

## D-0004 — Declarative per-rule detectors in the YAML referential

**Date**: 2026-06-12
**Status**: accepted

Detection was 100 % coupled to rule **prose**: `checker._build_checks(rule_lower)` scans a rule's
text for a fixed vocabulary of hardcoded trigger phrases and only then emits a check. A structured
YAML rule contributed its `severity` but **no detector**, so a rule whose wording the checker did
not recognise loaded, appeared in reports, and silently never fired — three shipped Python rules
(`py-pydantic-v2`, `py-async-fastapi`, `py-structured-logging`) were exactly this dead weight.

A rule may now carry an optional **`detect:`** block (`forbid` substrings, `forbid_regex` /
`file_regex` regexes, `match_in_comments`). The detector type (`loader.RuleDetector`) lives in the
loader so `guidelines` and `checker` share it without an import cycle; it flows through
`InstructionFile.rule_detectors` (keyed by rule text, mirroring `rule_severity`) and runs in
`checker._declared_violations` **alongside** the phrase-derived path. A declared violation inherits
the rule's own severity. This closes the "rules as data" loop: authoring a rule that actually fires
no longer requires editing `checker.py`.

*Rejected alternatives*: (a) keep phrase-coupling and just add trigger phrases for the three rules —
the next authored rule hits the same wall; (b) adopt a full Python AST analyser now — larger scope,
deferred to a later increment. Backward compatible: `detect:` is optional and markdown sources never
populate `rule_detectors`, so phrase-derived detection is unchanged for them.

---

## D-0005 — AST-backed Python detectors (`detect.ast`)

**Date**: 2026-06-13
**Status**: accepted

The declarative detectors from D-0004 match **text** (`forbid` substrings, `forbid_regex`,
`file_regex`). That is blunt for two shipped Python rules: `from pydantic import validator`
matched even inside a string literal (we had to inline-suppress the project's own test fixtures),
and the sync-vs-async route check was a brittle regex sensitive to spacing and multiline
decorator arguments.

Added a **`detect.ast`** key listing *named* checks resolved from `guideline_checker.ast_python`
(stdlib `ast`, zero new deps). Two checks ship: `pydantic-v1` and `sync-fastapi-route`. A file is
parsed once; non-Python or syntax-error files yield nothing (detection never crashes the scan).
The two shipped rules migrated from text patterns to AST — eliminating the string-literal false
positives and the regex brittleness. The `ast_python` module depends only on stdlib, so importing
it from both `guidelines` (validation) and `checker` (execution) adds no cycle.

*Rejected alternatives*: keep the text patterns (false positives + brittle); make AST the *only*
detector path (would drop the cross-language `forbid`/`regex` value — the two coexist; a rule
picks whichever fits). The named-check registry keeps the "rules as data" contract: wiring a
shipped rule to AST is a YAML edit, not a checker edit.

---
