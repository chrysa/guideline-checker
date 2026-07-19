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

## D-0007 — Local autofix via a declarative `fix:` block

**Date**: 2026-07-07
**Status**: accepted

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

## D-0008 — Cross-file rule inheritance and distributable rule packs

**Date**: 2026-07-07
**Status**: accepted

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

## D-0009 — Version derived from git tags (setuptools-scm), never hardcoded

**Date**: 2026-07-07
**Status**: accepted

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
