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

---

## D-0009 — Origin-side fleet distribution audit (Scanner seam + `--fix`)

**Date**: 2026-06-27
**Status**: accepted

`guideline-checker synthesize` enumerated **local** workspace subdirectories, inheriting
the fleet's stale-clone trap: a repo can look non-compliant locally while `origin/<default>`
is fine (and vice-versa). Nothing verified that each repo actually *carries* the managed
standards artifacts on its default branch.

Introduced an **origin-side distribution audit**:

- **`GhClient`** (`gh_client.py`) — the single seam over the `gh` CLI; tests inject a fake
  runner, production shells out (no token ever read in code, `gh` owns auth, no `shell=True`).
- **`Scanner` protocol** (`scanner_source.py`) — `LocalScanner` (filesystem, unchanged
  behaviour) and `OriginScanner` (reads `origin/<default>` via `GhClient`, immune to the
  stale-clone trap by construction).
- **`distribution.py`** — four file presence/equality checks (`standards-file`,
  `claude-import`, `precommit-pin`, `license-present`) emitted as standard `Violation`s, so
  **every existing reporter and the web dashboard render them unchanged**. Expected values
  are derived from a `shared-standards` checkout (DRY with `distribute-standards`).
- **`manifest.py`** — loads `repos.yml` (`status: dev` only). Per-repo applicability is a new
  optional `distribution:` opt-out block (`license/standards/precommit: false`); the legacy
  `public`/`runtime` fields are **not** overloaded (different semantics). Applicability is
  data, not heuristics — a misclassified repo is a one-line, reviewable manifest fix.
- **Tri-state → violation model**: OK = no violation; DRIFT = a `Violation`; NA = applicability
  skip (never a false drift); ERROR = a distinct `origin-fetch-failed` `Violation` so a
  transient API/auth failure is never silently read as "compliant".
- **CLI**: `synthesize --source {local,origin}` (default `local`, unchanged) `--manifest`
  `--shared-standards` `--category`.

**`--fix` (opt-in remediation)** opens **one PR per repo** and **never merges**; `--dry-run`
previews. Idempotent: an existing fix branch/PR short-circuits. Only `license-present` and
`standards-file` are auto-fixable (safe whole-file writes); `precommit-pin` and
`claude-import` are **report-only** because they require content-aware edits (append/inject)
with no safe full-file template — left for a human.

*Spec deviations (deliberate)*: (a) the per-line rule engine is **not** rewired to read
origin — the `Scanner` seam is introduced and consumed by the distribution category only;
full per-line-over-origin is future work. (b) applicability uses a new `distribution:` block
rather than overloading `public`/`runtime`. (c) only two of four checks are auto-fixable, as
above.

*Rejected alternatives*: (a) pull model (server clones/scans every repo) — couples to every
repo's creds/build env, and still reads a working tree; (b) auto-merge — merging stays a
separate, deliberate human action; (c) a bespoke report format — reusing `Violation` gives
every reporter + the dashboard for free.

---

## D-0017 — Local autofix via a declarative `fix:` block

**Date**: 2026-07-07
**Status**: accepted

> Renumbered 2026-07-27 from a duplicate `D-0007` (ID collided with the
> `extends:` inheritance ADR above). The three 2026-07-07 records (autofix,
> rule packs, version-from-tags) reused D-0007/8/9 and are renumbered to
> D-0017/18/19; their content and dates are unchanged.

The checker could only *flag* violations. `fixers.py` exists but is a fleet-level
distribution-drift remediator — it opens one GitHub PR per repo through a `GhClient`
(rules keyed by name, e.g. `license-present`), and never touches a local working tree.
There was no way to mechanically fix a violation in place, so every finding on a
mechanical rule was hand-work, capping adoption.

L2.1 adds a **local, deterministic autofix** kept entirely separate from the remote
`apply_fix` path. A rule opts in with a sibling `fix:` block (alongside `detect:`); the
checker rewrites the working tree for the lines that fired. Detection stays the source of
truth — a fix only runs where a violation actually fired.

`fix:` supports three mechanical operations, each anchored to the violation's line:

- `op: remove_line` — delete the whole violating line (e.g. a `breakpoint()`).
- `op: replace` with `from` / `to` — literal substring replace on the line
  (e.g. `yaml.load(` → `yaml.safe_load(`).
- `op: regex_replace` with `pattern` / `replacement` — regex sub on the line
  (e.g. `\bvar\b` → `const`).

**Contract**:

- A `fix:` is only valid on a rule whose detection is line-anchored (all current
  `forbid` / `forbid_regex` / `scan` / `ast` detectors carry a line number). A rule with
  no `fix:` block is detect-only and is never rewritten.
- Fixes must be **idempotent**: applying twice yields no further change. The shipped fixes
  satisfy this by construction (a removed line is gone; `yaml.safe_load(` no longer matches
  `yaml.load(`; `const` no longer matches `\bvar\b`).
- No LLM, no semantic rewrite — mechanical only. Structural rules (AST-detected React/TS
  shape rules) declare no `fix:`; they stay flag-only by design.
- `--dry-run` prints a unified diff and writes nothing (byte-for-byte unchanged on disk).
  After a real apply the checker re-scans and gates on the post-fix state, so the exit code
  reflects the violations that remain.

Implementation lives in a new `guideline_checker/autofix.py` (`apply_local_fixes`), invoked
by a `fix` subcommand and a `check --fix` flag. The remote `fixers.apply_fix` path is
untouched — the two remediation surfaces are independent.

*Rejected alternatives*: (a) reuse `fixers.py` — it is PR/GhClient-coupled and keyed by
fleet-artifact rule names, a different problem; (b) column/offset-based edits instead of
whole-line ops — brittle against re-detection and offers no safety gain for the mechanical
fixes shipped; (c) an autofix registry in code (like the AST checks) — the fixes are pure
data (a literal/regex + a target), so keeping them in the YAML preserves the rules-as-data
contract, the same reasoning as D-0004.

---

## D-0018 — Cross-file rule inheritance and distributable rule packs

**Date**: 2026-07-07
**Status**: accepted

> Renumbered 2026-07-27 from a duplicate `D-0008` (see D-0017's note).

D-0004/L1.4 gave rules a same-file `extends:` — a child could only inherit from a base
declared in the *same* referential file, because resolution ran per file against that
file's local `raw_by_id`. That blocks organising rules into shared, reusable libraries: a
security base and its strict variant had to live in one file.

L2.4 lifts inheritance to a **global registry** and adds **rule packs**:

- **Cross-file `extends:`** — every `*.yml` under `guidelines/` is parsed into one global
  id→rule registry before any resolution. A rule may now `extends:` a base declared in any
  file. Resolution shares one cache and one recursion stack, so a cross-file `extends:`
  cycle (`A`→`B`→`A` across files) is detected and raised exactly like the same-file case.
  Each raw rule carries the `file_target` of the file that declared it, so a base's target
  fallback stays anchored to its own file, not the consuming one.

- **Rule packs (`include:`)** — a dimension file may declare a top-level
  `include: [packs/<name>.yml]` (paths relative to `guidelines/`). Files under
  `guidelines/packs/` are **excluded from the folder auto-scan**: a pack is a *library*,
  parsed into the global registry (so its bases are available to `extends:` everywhere) but
  **emitted as active rules only where it is `include:`d**. This gives a clear model —
  dimension dirs auto-load; `packs/` is opt-in. A pack of abstract bases is never emitted
  (abstract rules are templates) yet is always available to inherit from.

- **Duplicate ids** keep D-0004's contract: a duplicate id *within one file* is an authoring
  error and raises; a duplicate id *across files/includes* is an intentional transverse
  override — first parsed wins (`_common.yml` first), the later one is logged and skipped.

**Contract / limits**:

- A base referenced by `extends:` must exist in some loaded file or pack; an unknown base
  still raises. Bases should declare their own `category`/`severity` (or be inheritable
  templates) — a child supplies what the base omits.
- `include:` accepts only paths **inside** `guidelines/` (rooted there). Remote or
  pip-installed rule packs (a shared `chrysa` pack distributed as a package) are a deliberate
  future step, not built here — the local-pack primitive is the foundation they would reuse.
- Packs live only in `guidelines/packs/`; other dimension dirs continue to auto-load.

*Rejected alternatives*: (a) keep resolution per file and duplicate bases — the exact
copy-paste D-0004 set out to remove, now across files; (b) auto-load `packs/` like any
dimension — then `include:` is redundant and a pack's rules always fire, defeating the
"library you opt into" model; (c) a global mutable rule-registry object threaded through the
checker — the loader already returns fully-resolved `InstructionFile`s, so resolution stays a
load-time concern and the checker is unchanged.

---

## D-0019 — Version derived from git tags (setuptools-scm), never hardcoded

**Date**: 2026-07-07
**Status**: accepted

> Renumbered 2026-07-27 from a duplicate `D-0009` (see D-0017's note).

The package version was hardcoded in **two** places — `pyproject.toml` (`version = "1.0.0"`)
and `guideline_checker/__init__.py` (`__version__ = "1.0.0"`) — and never updated, while
GitVersion tagged releases up to `v1.4.x`. The SARIF reporter (the only consumer, via
`__version__`) therefore advertised the tool as `1.0.0` in GitHub Code Scanning regardless
of the real release. Bumping the two literals by hand also contradicts the chrysa standard
"Versioning: GitVersion — never bump manually".

The version is now **single-sourced from git tags via `setuptools-scm`**:

- `pyproject` declares `dynamic = ["version"]`; `__init__.__version__` reads the installed
  distribution metadata (`importlib.metadata.version`), so there is one source, not two.
- The Docker build context **excludes `.git`** (`.dockerignore`), so setuptools-scm cannot
  read the tag there. Two escape hatches keep builds deterministic and non-breaking:
  a `fallback_version = "0.0.0+unknown"` in `[tool.setuptools_scm]`, and a Dockerfile
  `ARG VERSION` wired to `SETUPTOOLS_SCM_PRETEND_VERSION` so a build may stamp the real tag
  with `--build-arg VERSION=<tag>`. A plain Docker build reports an honest `0.0.0+unknown`
  instead of a false `1.0.0`; a dev/CI checkout with `.git` gets the real version for free.

**Open follow-up (shared infra)**: the published image is built by the
`chrysa/github-actions` deploy reusable, which does not yet forward a `build-args` input, so
the released image still reports the fallback until the reusable passes
`VERSION=<tag>`. This is the same Socle-reusable surface as the github-actions self-tag
defect and is tracked there, not in this repo.

*Rejected alternatives*: (a) keep the hardcoded literals and bump them by hand — violates
"never bump manually" and is exactly the drift that produced the `1.0.0` lie; (b) plain
setuptools-scm with no fallback — breaks every Docker build (no `.git` in context);
(c) publish to PyPI to make the wheel version authoritative — there is no PyPI publish
(distribution is the ghcr Docker image + the pre-commit hook by git ref), so this would add
a release surface without addressing the drift.

---

## D-0010 — Rule health: judge the YAML referential, treat markdown bullets as advisory

**Date**: 2026-07-18
**Status**: accepted

The deterministic health engine (`rule_health.py`, GET `/api/rules-health`) initially
classed every extracted rule as `proven` / `armed` / `dead` uniformly. On a self-scan that
labelled **478 of 506 rules dead** — but 469 of those are prose bullets the loader lifts
from `CLAUDE.md`, `AGENTS.md` and the agent files (any `must/never/always` line), which the
tool never promised to enforce. Calling them "dead" conflates *guidance we surface* with
*rules we claim to check*, and drowns the real defect: the handful of **YAML** rules that
ship with no detector.

**Decision.** Health is judged by source kind:

- A rule from the **YAML referential** (`SourceType.GUIDELINES_YAML`) with no detector and
  no phrase match is `dead` — a genuine defect (it is advertised as an enforceable rule but
  enforces nothing; fix it or delete it).
- A rule from a **markdown** source (`CLAUDE`/`AGENTS`/copilot) that is undetectable is
  `advisory` — surfaced as agent guidance, never counted as a failure or an enforced rule.
- Detectable rules stay `proven` (fires) / `armed` (valid detector, no match), regardless
  of source. `suspect` (fires only on suppressed lines) is unchanged.

This makes the README's "no dead prose" claim *true of the referential it describes* (the
YAML packs) and turns the 469 markdown bullets from a false alarm into an honest
"advisory / undetectable" bucket.

*Rejected*: (a) keep all 506 as rules and headline the 478 — technically loud but
misleading, since markdown bullets were never enforcement promises; (b) stop extracting
markdown bullets entirely — loses the advisory surface that tells you *which* guidance the
checker cannot yet enforce, which is exactly the signal the workshop needs.

---

## D-0011 — Web surface becomes a real frontend; retire the embedded HTML string

**Date**: 2026-07-18
**Status**: accepted

The dashboard shipped as `_DASHBOARD_HTML`, a 412-line Python string literal inside
`web/app.py` — no templating, no asset pipeline, unstyleable, and the largest single reason
the web module reads as throwaway. The rethink turns the read-only dashboard into a
**local workshop** (health + propose→sandbox→validate), which needs a real, componentised
UI in the project's own design language (Exaggerated Minimalism · slate/green · mono).

**Decision.** Replace the embedded string with a real frontend served from static assets
bundled into the wheel (`importlib.resources`), so `guideline-checker web` works from any
installed location. The FastAPI app keeps its JSON API (`/api/scan`, `/api/results`,
`/api/constraints`, `/api/rules-health`) as the contract; the frontend consumes it. The
CLI/pre-commit/CI verdict paths are untouched — this is a surface change only.

*Rejected*: (a) keep patching the string — every health/workshop view compounds the
unmaintainable literal; (b) minimal health tab inside the existing string, defer the real
front — postpones the same debt while the workshop grows on top of it.

---

## D-0012 — Proposer seam: LLM proposes, never judges; Ollama qwen2.5:7b default

**Date**: 2026-07-18
**Status**: accepted

The workshop proposes fixes to dead/undetectable rules and to violating code. Those
proposals may come from an LLM, but detection must stay deterministic and offline (CI must
remain falsifiable). The Notion card had this pending as "Router: Ollama ↔ Claude, mode IA
indécis".

**Decision.** A `Proposer` seam (mirroring D-0009's `Scanner` seam) with
`propose(rule, context) -> Proposal`. Implementations: `HeuristicProposer` (the existing 43
`_build_checks` phrases, recycled — free, instant, tried first), `OllamaProposer`
(qwen2.5:7b, the default LLM backend), `ClaudeProposer` (heavy tasks). The router escalates
to an LLM only when the heuristic comes up dry.

**Trust boundary (non-negotiable).** The LLM only *proposes*; every proposal is **replayed
by the deterministic engine in a sandbox** and shown with its proof (what it catches,
misses, breaks) before any write. The LLM never enters the verdict path. The LLM backends
live behind an optional `[assist]` extra — **never in `[core]`**, which keeps its two
runtime deps (PyYAML, tree-sitter) and its offline guarantee.

*Rejected*: (a) claude-cli via subscription as the first backend — unusable in CI and for
third parties (needs the `claude` binary); kept as a future seam implementation, not the
default; (b) defer the whole choice to P3 — the seam is the architectural commitment now,
the backend is an interchangeable detail behind it.

---

## D-0013 — Claude CLI proposer as the default LLM backend

**Date**: 2026-07-19
**Status**: accepted

D-0012 introduced the `Proposer` seam with Ollama (qwen2.5:7b) as the nominal LLM
default. A live test on the host (einar) showed Ollama's `/api/generate` failing
across three models (llama3.2:3b, llama3.1:8b, qwen2.5:7b) — root cause was memory
pressure (~1 GiB free of 29), not model quality. A local model is therefore not a
dependable backend here.

**Decision.** Add `ClaudeProposer`, which shells out to the `claude` CLI on the
user's subscription (`claude -p <prompt>`, with `ANTHROPIC_API_KEY`/`ANTHROPIC_KEY`
stripped from the child env so the subscription session is used). It shares the
exact parse path with `OllamaProposer` (`_proposal_from_reply`) and the same trust
boundary: it only proposes; the sandbox proves the detector before any write.

In the web `/api/propose` escalation, **Claude is preferred** (`GC_CLAUDE=1`),
Ollama remains available (`GC_OLLAMA=1`) for the free/offline case when the host
has spare RAM; both stay off by default.

Live result on the 8 dead `ai-models/*.yml` rules: Claude proposed a sensible
detector for the one mechanically-detectable rule (`import anthropic` /
`from anthropic import` for the provider-seam rule) and **honestly returned `{}`
(no proposal)** for the four purely-semantic ones (XML-tag structure, structured
outputs, native function calling, explicit safety settings) — it does not
hallucinate detectors for rules that cannot be caught from source text, which
reinforces the advisory classification from D-0010.

*Rejected*: (a) keep Ollama as the default — not runnable on the current host;
(b) an API-key backend — the project bans a key dependency and the CLI already
carries the subscription. `[assist]` stays dependency-free (both backends use
stdlib transport: `urllib` for Ollama, `subprocess` for Claude).

---

## D-0014 — Markdown credential rules route to the entropy scanner, not substrings

**Date**: 2026-07-19
**Status**: accepted

Running the checker against real chrysa repos exposed the adoption blocker
directly: a markdown "no hardcoded credentials / API keys" rule emitted naive
substring checks (`token =`, `password =`, `secret=`, …) that flagged **every**
variable whose name contained a secret keyword — **596 findings on one repo**,
almost all of them `token = response.json()[...]`, env lookups, empty strings,
or short placeholders. A rule that fires 596 times on non-issues cannot gate a
CI, which is the project's own kill-test (`≥2 repos in blocking CI`).

**Decision.** A hardcoded-credential rule (detected by the same prose trigger as
before) now routes to the existing `secret-assignment` entropy scanner
(`scanners.py`, ADR D-0008) instead of emitting substring `PatternCheck`s. The
scanner fires only on a quoted string literal whose value clears the length
(≥12) and Shannon-entropy (≥3.5) gates and is not an env reference, and it
honours the repo's `.secrets-allowlist`. The naive `_credential_checks` substring
emitter is removed.

Measured on real repos: **ai-aggregator 47 → 0**, **dev-nexus 600 → 12** (the 12
being genuine `ghp_`-shaped literals plus two `no any` and two `shell=True`), so
`--fail-on error` becomes usable in another repo's blocking CI — the concrete
prerequisite for the validation gate.

*Rejected*: (a) keep the substrings and tune per-repo excludes — endless
whack-a-mole; (b) add regex `PatternCheck`s requiring a quoted literal — cannot
express the entropy gate that separates a real key from a dictionary placeholder;
(c) drop credential detection from markdown rules entirely — loses the feature on
repos with no `guidelines/` referential, which is where it matters most.

---

## D-0016 — Independent & pluggable engine: mechanisms in the tool, values in the host

**Date**: 2026-07-27
**Status**: accepted

The tool ships `guidelines/*.yml` (ai-models, languages, packs) carrying **chrysa**
values — the entropy threshold aside, rules encode chrysa's forbidden SDKs and the
scaffolder bakes a chrysa number (`init_cmd.py:22` → `Max file length: 500`). That
couples the binary to one org's standards, contradicting the tool's own vision:
"nothing hardcoded, driven by the repo's files". A third party cloning the tool
inherits chrysa's rules, not its own.

**Decision — separate MECHANISMS from VALUES.**

- **Mechanisms** (in the engine, generic, shipped): a small finite set of check
  *kinds*, each knowing how to *measure* — `file-exists`, `numeric-threshold-on-metric`,
  `forbidden-import`, `naming-convention`, `section-presence`, `file-freshness`. This is
  the existing `detect.*` registry family (D-0004/5/6/8) reframed as the generic layer.
- **Values / rules** (never in the engine): the metric target, threshold, module name,
  or pattern come **exclusively from the host's prose** (`CLAUDE.md`, `AGENTS.md`,
  `.github/instructions/*`, `copilot-instructions.md`) or its machine-readable config
  (`thresholds.json`, `.quality-gate.json`). The engine knows no version, no threshold,
  no target name.

Adding a rule = map a prose sentence onto an existing kind + params; **no engine code
changes, no literal written into the tool**. `guidelines/*.yml` is requalified from a
*shipped chrysa referential* to a **per-repo derived cache** — the interpret-once output
(LLM proposes, D-0012/13), sandbox-proven (D-0011 loop) and versioned **in the host
repo**, regenerable, not part of the tool distribution.

Determinism is preserved by the two-phase loop already accepted (D-0010→D-0014):
interpret-once (LLM proposes a ruleset, each rule carrying its **provenance** — the exact
source sentence) → sandbox-replay proves +/- → cache → CI/pre-commit apply the cache
100% deterministically, never re-calling the LLM. Prose that maps to no kind stays
**advisory** (D-0010), never a hard-fail.

**Fatal hypothesis.** The finite kind set above covers the enforceable rules real host
prose expresses, so a third-party repo yields a non-empty proven ruleset from its prose
alone (no chrysa file). If most host rules need a bespoke kind the engine can't offer,
the "generic mechanisms" model is wrong and this collapses back to a shipped referential.

**Kill-test.** Clone the tool into a repo with **no** chrysa file, only its own
`CLAUDE.md`; run interpret-once + sandbox. Measured 2026-Q3 on ≥3 non-chrysa repos: if
< 3 rules per repo end `proven` from local prose, or if any version/threshold/target-name
still appears as a **literal in the tool's code** (grep gate in CI), the hypothesis is
false → revert to shipping `guidelines/` and mark this `Killed`.

**Validation gate.** Before the engine refactor lands: (a) `init_cmd` scaffolds **zero**
numeric chrysa literals; (b) a CI grep gate asserts no bare threshold/version literal in
`guideline_checker/**`; (c) removing shipped `guidelines/` leaves the engine green
(only the per-repo cache is lost, regenerable). This ADR's first commit delivers (a);
(b) and (c) gate the subsequent kind-registry lots.

*Rejected*: (a) keep `guidelines/` as the shipped source of truth — the coupling this
ADR removes; (b) let the LLM judge at CI time — non-deterministic gate, violates
D-0012's "LLM proposes, never judges"; the cache is the determinism boundary.

## D-0020 — The MECHANISMS taxonomy: a finite set of generic check kinds

**Date**: 2026-07-28
**Status**: accepted

D-0016 draws the line — mechanisms in the engine, values in the host — but the
mechanism layer was only *implicit*: which generic check a rule used was an
accident of which `detect.*` field (`forbid`, `forbid_regex`, `file_regex`,
`ast`, `scan`) it happened to carry, or which phrase the checker recognised.
Nothing named the finite set of mechanisms or let a rule (or a reader) say *how*
a rule is measured, independently of *what* it enforces.

**Decision.** Name the mechanism layer as a first-class, value-free taxonomy in
`kinds.py` — a `CheckKind` enum with a fixed set: `forbidden-pattern`,
`file-content`, `ast-structure`, `content-scan`, `numeric-threshold`,
`file-presence`, and `advisory` (no mechanical kind). Each kind describes only
what it *measures*; the pattern, metric, threshold or path it measures against
always comes from host prose/config, never from this table. `kind_of_detector`
classifies a `RuleDetector` and `kind_of_phrase` a phrase-detected rule, so every
rule reports exactly one kind. `RuleHealth` carries it, `/api/rules-health`
serialises it, and the workshop shows it next to each rule.

This is **purely additive**: classification is derived from the existing
`RuleDetector` and phrase table, so detection behaviour is unchanged — the engine
runs exactly as before. It makes the D-0016 mechanism/value split inspectable and
is the anchor for later work (interpret-once maps a host sentence onto a kind +
its params; a kind not yet implemented — e.g. `file-freshness`, an explicit
`naming-convention` — is added here once, not case by case across the checker).

*Update 2026-08:* the `file-freshness` kind is now implemented as a first mechanism
of that "add a kind once" path — a `detect.stale_after_days: <int>` on a rule flags
any matching file whose last-modified age exceeds the threshold (`checker._freshness_violations`,
value from host prose, glob scope from the rule's `apply_to`). `CheckKind.FILE_FRESHNESS`
classifies it; the loader validates a positive-integer day count and it round-trips
through `persist`. Detection stays deterministic-within-a-run (age vs the run clock).

**Fatal hypothesis.** The rules real host prose expresses fall into this finite
kind set; a rule needing a mechanism outside it is rare enough to add as one new
kind, not a reason to abandon the fixed taxonomy.

**Kill-test.** Across the fleet self-scan, every enforceable rule classifies into
a kind (no `advisory` rule that actually carries a working detector). If a class
of enforceable rules cannot be expressed as a bounded kind set — each new rule
needing a bespoke mechanism — the taxonomy is the wrong abstraction and this is
`Killed`.

**Validation gate.** Landed additive with no detection change (existing suite
green); `kind` visible in the health API and workshop before any interpret-once
mapping is built on top.

*Rejected*: (a) leave the mechanism implicit in `detect.*` — a reader cannot see
the mechanism without reading the YAML internals, and interpret-once has no target
vocabulary to map prose onto; (b) model kinds as free-form strings — a taxonomy
that is not a closed set cannot be reasoned about or kill-tested.

## D-0015 — Multi-project web workshop (workspace selector)

**Date**: 2026-07-23
**Status**: accepted

The web workshop scanned only its own root (`SCAN_ROOT`). To operate the tool as
a fleet cockpit — pick any repo, see its rule health, work on it — the UI needs a
project selector.

**Decision.** A filesystem-only `workspace.discover_projects(root)` lists the
immediate git-repo sub-directories of a workspace that carry a rule source
(CLAUDE.md/AGENTS.md/instructions/guidelines). `GC_WORKSPACE` sets the workspace
(default: the scan root's parent, so the fleet appears with zero config). The
server holds an `active_project`; `GET /api/projects` lists them, and
`POST /api/scan {project}` switches to a **discovered** project (never a raw
path — the input is resolved against the discovery whitelist, so no traversal)
before scanning. Scan/health/propose/arm all target the active project via
`_active_root()`. Single-repo installs list one project and hide the selector.

This is a surface change: the deterministic engine, the trust boundary, and the
CLI/pre-commit paths are untouched. `central.py` (the frozen push-model
aggregate) stays frozen — this is a *pull* selector, not an aggregate server.

*Rejected*: (a) revive `central.py`'s push/aggregate model — heavier, needs
reporters pushing in; the selector is read-only and needs no per-repo agent;
(b) accept an arbitrary root path in the API — path-traversal risk; the
discovery whitelist is the safe boundary.

## D-0021 — The `numeric-threshold` mechanism: the engine measures, the host bounds

**Date**: 2026-08-01
**Status**: accepted

D-0020 named `numeric-threshold` in the taxonomy and shipped it with nothing
behind it: no `detect.*` key could express one, and no detector measured
anything. A kind a rule cannot be written in is a mechanism that measures
nothing — the silent green this project exists to refuse, sitting inside its own
taxonomy. The numeric fleet gates (file ≤ 500 lines, function ≤ 50, complexity
≤ 10) were therefore enforced only where a host happened to write them as prose
the checker's regexes recognised, and not at all from the YAML referential.

**Decision.** Give the kind a mechanism and keep every number out of it.
`metrics.py` owns three measurers — `file_lines`, `function_lines`, `branches`
(decision points + 1, the cyclomatic heuristic) — each returning *what it read
and where*, never a verdict. A rule opts in with

```yaml
detect:
  numeric_threshold:
    metric: function_lines
    max: 50
```

The loader validates both fields together (a metric with no bound measures
without judging; a bound with no metric judges nothing), rejects an unknown
metric at load rather than arming a rule that checks nothing, and
`checker._numeric_threshold_violations` flags every subject strictly over the
bound. `max` is a bound, not a target: reaching it is compliance. `metrics.py`
states no threshold of its own — a guard test asserts exactly that.

**Shipped in two steps, for a reason worth recording.** This repository applies
its own hook at a pinned release (`.pre-commit-config.yaml`), so a referential
using a `detect.*` key the pinned build does not know **fails to load** — the
gate goes red on the repo's own rules, in CI as well as locally, and no local
`SKIP` reaches CI. Any new schema key therefore lands in two parts: the
**mechanism** first (this change — no referential edit, so the pinned hook still
loads), then a release, then the **rules** that use it. `stale_after_days`
(D-0020) shipped the same way. The three rules carrying the fleet's numbers
(`py-file-length` 500, `py-function-length` 50, `py-branch-count` 10) follow in
that second step; they were written and measured against real code before this
ADR was accepted, so the validation gate below is met on evidence, not on
intention.

**Fatal hypothesis.** Measuring length and branch count from a single-file AST is
close enough to what the fleet gate already measures (`ruff` `C901` / `PLR0915`)
that a finding here predicts a finding there.

**Kill-test.** Run both over the fleet's ten largest repos. If they disagree on
more than 10% of functions, this mechanism is measuring something else while
claiming the gate's authority, and it must defer to the linter instead of
restating it. Checked at the next referential review. First signal, on this
repo: `py-branch-count` fires **zero** times where `C901` is already enforced and
green — consistent, not yet conclusive.

**Validation gate.** The rules fire on real code before any promotion from
`warning` to `error`. Met before acceptance: run against this repository's own
sources, the three rules produced **13 findings, no false positive** — 3 files
over 500 lines, 10 functions over 50, and zero from `py-branch-count`.

**Consequences.** Severity stays `warning`: `ruff` already blocks on the same
bounds in CI, and a second blocking source for one number would double-report a
single defect. **Former blind spot, now closed** — `.guidelineignore` used to
exclude `checker.py` and `cli.py` wholesale because a *pattern* detector's own
tables contain the patterns it flags, and that same blanket blinded the
*measurement* rules to the two longest files in the repository. The exclusion is
now scoped per-rule (`detect.exclude`): `py-no-eval-exec` excludes `checker.py`,
`py-structured-logging` excludes `cli.py`, and the numeric rules carry no
exclude, so they measure both files. The two are off the file-level blanket.

*Rejected*: (a) shell out to `ruff`/`radon` and parse their output — the engine
would depend on a host toolchain being installed, which is the exact failure
mode #310 documents, and offline determinism (D-0016) is not negotiable;
(b) keep the numbers in `metrics.py` behind named constants — shorter, and
precisely the drift D-0016 forbids: the engine would then carry a value and every
host would inherit chrysa's bound whether or not it is theirs.

## D-0022 — The JSON result contract carries its own version

**Date**: 2026-08-01
**Status**: accepted

Standards Hub is to expose the compliance results this tool produces without
integrating its engine or reaching into its internals. The JSON report carried no
version, so the only thing a consumer could pin was the tool's git tag — coupling
the Hub to every unrelated release, and giving it no way to tell an added field
from a changed meaning. It also carried no way to answer the first question a
dashboard asks of a finding: *is this already accepted debt?*

**Decision.** The payload is a contract with a version of its own.
`schema_version` leads the envelope, versioned independently of the tool: an
additive field bumps the minor, a removal or a changed field meaning bumps the
major. Every existing key is kept — this landed additive, so no current consumer
broke. Each violation gains two fields:

- `kind` — the mechanism it was measured by (D-0020), derived from the rule's
  declarative detector or, for a phrase-detected markdown rule, from its prose.
  Never blank: a field a consumer cannot rely on is a field they will ignore.
- `fingerprint` — the *same* content hash `baseline.fingerprint` computes, so a
  consumer joins a result to the project's accepted debt instead of re-deriving
  the hash and drifting from it.

SARIF keeps its own `2.1.0`: that version belongs to the SARIF specification, not
to us, and conflating the two would make our contract unversionable.

The report deliberately carries **no** `rule_id`. A rule is identified by its
statement text throughout the engine — `InstructionFile` maps rule text to
severity, detector and fix — and markdown-sourced rules have no id at all.
Emitting an empty `rule_id` on most findings would be a field that looks pinnable
and is not. Should stable ids become real, they arrive as an additive minor bump.

**Fatal hypothesis.** A consumer can be fully served by a stable JSON shape
without ever reaching into the engine.

**Kill-test.** If Standards Hub's first integration needs a field this contract
does not carry, the contract was designed from the producer's side rather than
the consumer's query, and it is re-derived from that query in one dated revision.
Checked at Hub integration.

**Validation gate.** Standards Hub reads a report end-to-end using only
documented fields, with no access to this repository's modules.

**Consequences.** The Hub never gates local or CI execution: a report is a file on
disk, produced offline, and a Hub outage cannot stop a commit or a pipeline. The
contract is now a public surface — changing a field's meaning is a major bump,
not a refactor.

*Rejected*: (a) version the payload with the tool's own release tag — a patch
release with no payload change would signal a contract change, and a payload
change inside a patch would signal none; (b) publish a JSON Schema file and skip
the inline version — a consumer that fetches a schema out of band cannot tell
which version *this* file was produced against.

---

## D-0023 — The self-check hook runs the in-tree engine, not a pinned release

**Date**: 2026-08-07
**Status**: accepted

D-0021 shipped every new `detect.*` key in two steps — mechanism, **release**,
then the rules that use it — because this repo linted itself with its own hook
pinned at a published tag (`.pre-commit-config.yaml`). A referential using a key
the pinned build did not know **fails to load**, reddening the gate on the repo's
own rules until a release caught up. When the numeric-threshold rules (#345)
merged into `develop` before such a release existed, the deadlock became real:
no published tag carried the `numeric_threshold` parser, so **every commit and
push in the repo was blocked**, and `guideline-check` was red on every PR.

**Decision.** The repo's own `guideline-check` hook is a `repo: local`,
`language: python` hook that runs the **working tree** (`python -m
guideline_checker.cli check --fail-on error`; `python -m` puts the repo root on
`sys.path`, so the in-tree package wins over any installed copy). Its runtime
deps are declared as `additional_dependencies`, so it stays host-native — no
Docker, installable with a single `pipx install pre-commit`. The tool now always
understands the rules it ships: a new `detect.*` key is enforceable in the same
commit that adds both the parser and the rule. The two-step dance of D-0021 is
retired — mechanism and rules may land together.

This governs **only this repository's self-check**. Consumer repos still pin a
published tag (`rev:`) as before — they want a stable, released detector set, not
this repo's bleeding edge.

**Fatal hypothesis.** Running the working tree's engine over the working tree's
referential is strictly more correct than a pinned tag can be, with no case where
the pinned tag would have caught a defect the in-tree run misses.

**Kill-test.** If a commit passes the in-tree hook but the same tree fails
`guideline-check` once released and re-pinned in a consumer, the in-tree run is
lying about the shipped behaviour and the self-check must additionally run the
last released tag. Checked at the next consumer bump that pins a tag built from
this repo.

**Validation gate.** A commit adding a new `detect.*` key together with a rule
that uses it passes `pre-commit run guideline-check` with no prior release. Met:
this change's own commit is gated by the new hook.

**Consequences.** The self-check is only as good as the working tree — a broken
local engine reddens the gate, which is the correct signal (the tool is broken).
CI runs the same `pre-commit` hook, so the in-tree engine is exercised there too;
`docker-test` remains the authoritative suite. `.pre-commit-config.yaml` no longer
carries a `rev:` for this repo's own hook, so the weekly autoupdate cannot bump it.
