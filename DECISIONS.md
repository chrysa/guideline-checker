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

## D-0006 — JS/TS AST detection via tree-sitter

**Date**: 2026-06-15
**Status**: accepted

D-0005's AST engine is stdlib `ast` — Python only. The shipped TypeScript and React rules
(`ts-strict-types`, `ts-no-suppressions`, `react-hooks-top-level`, `react-stable-keys`,
`react-no-inline-component-defs`) carried **no `detect:` block at all**, so they loaded,
appeared in reports, and silently never fired — the exact dead-weight failure D-0004
described, now for JS/TS. Three of them (the `any` type, conditional-hook, and
inline-component rules) cannot be detected reliably with `forbid`/regex: `: any` matches
inside strings, a conditional `useState()` is invisible to a line regex, and a component
nested in another component's render is a structural fact no substring can see.

Added a second AST engine, `guideline_checker.ast_javascript`, backed by **tree-sitter**
(`tree-sitter` + `tree-sitter-typescript` for TS/TSX + `tree-sitter-javascript` for JS/JSX).
It mirrors `ast_python`'s contract — named checks, `run_js_ast_checks`, `VALID_JS_AST_CHECKS`
— so `guidelines` (validation) and `checker` (dispatch by file suffix) integrate uniformly.
Five checks ship: `ts-any-type`, `ts-suppression`, `react-hook-order`, `react-index-key`,
`react-inline-component`. Parsing never raises (malformed input yields an `ERROR` tree), so
detection cannot crash the scan. The five shipped rules are now wired via `detect.ast`,
keeping the "rules as data" contract — adding a JS/TS check is a YAML edit, not a checker edit.

This adds the **first non-stdlib *detection* dependency** (`PyYAML` is data-only;
`ast_python` is stdlib). tree-sitter is moved into the **core** `dependencies` (not the
`web` extra) because detection is the CLI's core job. It ships prebuilt wheels, so the
slim Docker image needs no C toolchain.

*Rejected alternatives*: (a) pure-Python parsers (`esprima`, `pyjsparser`) — no TSX/JSX
support, the very dialects the React rules target; (b) regex-only detectors — the three
structural rules above are not regex-expressible without unacceptable false positives;
(c) keep the rules undetected — leaves five shipped rules as permanent dead weight.

---

## D-0007 — Rule inheritance via `extends:` (same-file composition)

**Date**: 2026-06-22
**Status**: accepted

Authoring families of related YAML rules (a shared secret pattern, a base "no console"
detector specialised per environment) meant copy-pasting `detect:`, `severity`, `category`,
and `rule` text across near-identical entries — the duplication D-0004 set out to avoid, now
in the referential itself.

Added an `extends: <base-rule-id>` key so a child rule inherits from a base declared **in the
same file**, overriding selectively:

- **Inheritable fields** — `category`, `severity`, `rule`, `rationale`, and the `*_target`:
  the child's value wins when present, else the base's. A child carrying `extends` may omit
  the otherwise-required `category`/`severity`/`rule` and inherit them; the *merged* rule must
  still satisfy every requirement (enforced after the chain is resolved).
- **`detect` merge is union** — the child's `forbid`/`forbid_regex`/`file_regex`/`ast` lists
  are appended to the base's (deduplicated, first-seen order); `match_in_comments` is OR-ed.
  A child cannot *remove* a base pattern (acceptable for v1: families specialise by *adding*).
- **`abstract: true`** marks a template — a valid base that is usable as an `extends` target
  but is itself never emitted or checked. It lets a pure base exist without also firing.
- **Chains** (`a` extends `b` extends `c`) resolve recursively and memoised; **cycles** and
  **unknown bases** are hard `GuidelineError`s.

Loading became two passes inside `_parse_dimension_file`: parse every rule into a `_RawRule`
(so a child may reference a base declared later), then resolve each. L1.3's guarantee that
intra-file ids are unique makes same-file base resolution unambiguous and needs no global index.

*Scope — same-file only (v1)*. Cross-file `extends` is deferred: cross-file ids already carry
intentional transverse-override semantics (`_common.yml`-wins, kept-first), and resolving an
`extends` against that keep-first set would entangle two distinct mechanisms. Same-file keeps
the feature self-contained and the unambiguous-base guarantee intact.

*Rejected alternatives*: (a) **replace** semantics for `detect` (child wholly overrides the
base's block) — forces re-stating shared patterns, which is the duplication this removes;
(b) **no `abstract`**, every base also fires — forces bases to be rules you actually want
reported, or pollutes reports with template scaffolding; (c) **cross-file `extends`** now —
adds a global resolution pass and a collision with transverse-override semantics for no
demonstrated need (families are authored together, in one file).

---

## D-0008 — Entropy-based secret detection via a named content-scanner registry

**Date**: 2026-06-22
**Status**: accepted

The transverse `secrets-via-env` rule carried **no detector**, so it loaded and never
fired (the dead-weight failure D-0006 named). The only secret detection was
`_credential_checks`, a phrase-triggered set of `secret =`/`password =` substring patterns:
it missed every assignment shape it did not hardcode and false-positived on test fixtures
(`secret = "super-secret-should-not-leak"`).

Added a **named content-scanner registry** — `guideline_checker/scanners.py` — mirroring the
AST-check registries of D-0005/6: a rule lists scanners in `detect.scan`, `guidelines`
validates them against `VALID_SCANS`, and the checker runs them over file content. Scanners
are stdlib-only and never raise. The shipped `secret-assignment` scanner finds
`<key> = "<value>"` assignments whose key names a secret, then applies a **Shannon-entropy
gate** to the value — random keys/tokens sit at ~4-5 bits/char, dictionary placeholders at
~2-3 — skipping low-entropy placeholders, environment lookups (`os.environ`, `getenv`,
`${VAR}`, `<...>`), and short values. This is the first detector that reasons about a value's
*content* rather than matching a fixed pattern, which is why it warranted a registry rather
than another `forbid_regex`.

False positives on legitimate fixtures are handled by a **repo-level `.secrets-allowlist`**
(YAML: `paths` globs + `values` substrings), mirroring `.guidelineignore` but secrets-specific
so an allowlisted file stays scanned for every *other* rule. Inline `# guideline: disable`
suppression continues to work. The allowlist is threaded via the existing `root` already held
by the per-instruction worker, so only `_check_file`/`_evaluate_rule`/`_declared_violations`
gained an optional `root` argument.

*Scope note*: this lot does **not** extract the detect-schema parser out of `guidelines.py`.
On `main` the file is well under the 500-line cap; the extraction becomes necessary only once
**both** D-0007 (which rewrote the loader) and this lot land, at which point `guidelines.py`
approaches the cap — tracked as the reconciliation follow-up when the two branches merge.

*Rejected alternatives*: (a) a dedicated `detect.secret:` block — a one-off schema branch that
does not generalise to future content scanners (license headers, TODO budgets, banned APIs);
(b) a `forbid_regex` for secrets — no entropy reasoning, so it re-introduces the placeholder
false positives; (c) keeping `_credential_checks` — misses real secrets and is un-allowlistable.
