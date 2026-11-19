# guideline-checker — Redesign (core / workshop / fleet + auto-derived proven detectors)

**Date:** 2026-08-19
**Status:** Approved design — pending spec review
**Supersedes / archives:** the meta-layer of `2026-07-18-rule-health-workshop-design.md`
(the workshop stops being a standalone product and becomes the LLM-only tail of a core
generation loop) and the standalone framing of `2026-06-27-fleet-origin-audit-design.md`
(fleet moves behind an optional extra).
**Target release:** `v2.0.0` (breaking).

---

## 1. Problem

`guideline-checker` grew four identities at once:

- **(a) Lint** of AI-agent instruction files (`CLAUDE.md`, `AGENTS.md`, `.github/instructions/*`) → violations. *(the core)*
- **(b) Rule-health** — classifies each rule `proven` / `armed` / `dead` / `advisory` so the tool never shows green for a rule with no working detector. *(the differentiator)*
- **(c) Workshop** — an LLM loop (`proposer`/`sandbox`/`persist`/`interpret`) that authors detectors. *(a second product bolted on)*
- **(d) Fleet-governance** — origin-side distribution audit, central server, repo lifecycle. *(an admin tool in disguise)*

Symptoms of the resulting accidental complexity:

- Three files hold ~47% of the LOC. `checker.py` is a 1265-line god-module with ~50
  `_*_checks` functions — it violates the project's own 500-line rule.
- **Two parallel detection paradigms:** a hardcoded phrase→regex table inside
  `checker.py`, *and* a declarative YAML `detect:` referential. Same output, double surface.
- 23 ADRs, ~10 of them about the health/proposer/"mechanisms-vs-values" meta-layer rather
  than shipping detections.
- `central.py` already marked FROZEN/dead.
- `CLAUDE.md` is 113K — a fleet-wide constitution inlined into a ~7K-LOC linter.

## 2. Goals

1. **One identity for the core:** honestly lint agent instruction files and report which
   rules are dead. Everything else is optional.
2. **Kill the dual detection paradigm** — a single mental model: every rule is data with a
   `detect:` mechanism.
3. **Auto-derive detectors from prose, kept in sync**, so the user writes rules in
   `CLAUDE.md`/`.github` and the tool maintains a *proven* detector per rule automatically.
4. **Strict dependency direction:** `core/` never imports `workshop/` or `fleet/`.
   `pip install guideline-checker` with no extras = a pure linter, no LLM/`gh` deps.
5. **Rule-health is the headline**, not a report tile (positioning pivot).
6. Shrink governance weight (`CLAUDE.md`, ADR corpus) to match a small tool.

### Non-goals

- No new fleet features; fleet is preserved, only relocated behind `[fleet]`.
- No LLM in the default install path; LLM derivation lives behind `[workshop]`.
- Not a rewrite — a decomposition + relocation + one new generation loop.

## 3. Target architecture

### 3.1 Three zones, one dependency rule

```
guideline_checker/
  core/                              ← installed by default, no LLM / no gh deps
    detection/
      __init__.py                    ← orchestrator + kind registry (detect.<kind> → handler)
      pattern.py                     ← regex / per-line + whole-file
      ast_python.py                  ← stdlib ast
      ast_javascript.py              ← tree-sitter
      scanners.py                    ← entropy secret scanner
      numeric.py                     ← ex-metrics.py (file/func/branch length vs bound)
      presence.py                    ← presence / length / freshness
      crossref.py                    ← cross-reference checks
    loader.py                        ← markdown instruction files → rules
    referential.py                   ← ex-guidelines.py: YAML load/validate, extends/include/packs
    health.py                        ← ex-rule_health.py — the differentiator + generation gate
    derive/                          ← NEW: heuristic detector derivation (deterministic)
      __init__.py                    ← prose rule → candidate detector (heuristic only)
      cache.py                       ← local ephemeral cache keyed by hash(prose + engine version)
    baseline.py  config.py  autofix.py  fixers.py  linters.py
    reporters/                       ← html / json / markdown / sarif (+ synthesis_html local)
    cli.py                           ← check / fix / init / health / synthesize (local only)

  workshop/                          ← EXTRA [workshop] — optional, LLM tail
    proposer.py                      ← Ollama / Claude backends (LLM derivation only)
    sandbox.py  persist.py  interpret.py
    web_endpoints.py                 ← propose-detector web routes

  fleet/                             ← EXTRA [fleet] — optional, gh-backed
    distribution.py  manifest.py  origin_audit.py  lifecycle.py  gh_client.py
    (synthesize --origin dispatches here)

  web/                               ← the core dashboard — app.py ships by DEFAULT
    app.py  auth.py                   ← always installed; [web] extra only pulls future deps
    (central.py DELETED, push DELETED)
```

**Dependency rule (enforced):** `core/` imports neither `workshop/` nor `fleet/`. Satellites
import `core/`, never the reverse. A dedicated import-linter/ruff rule (or a `guideline`
rule dog-fooded on this repo) guards the boundary. Optional features are reached through a
registry/entry-point the core exposes, so the core calls *out* to a plugin interface it
defines, without importing the plugin.

### 3.2 Unified detection — one paradigm

Everything the engine detects is a YAML rule carrying a `detect:` block:
`detect.pattern`, `detect.ast`, `detect.scan`, `detect.numeric`, `detect.presence`,
`detect.crossref`. The kind registry in `detection/__init__.py` maps `detect.<kind>` to a
handler. Adding a kind = one file + one registry entry.

The hardcoded phrase table **no longer detects**. It is demoted to a *seed translator*: it
lives inside `derive/` and turns recognised prose ("no print") into an in-memory YAML
`pattern` rule. The engine only ever sees YAML rules — never phrases. The ~15
`_docker_checks`/`_django_checks`/... families cease to be code; the ones worth shipping
statically become `guidelines/*.yml`, the rest are produced on demand by derivation.

### 3.3 Generation loop (the new core mechanism)

```
prose rules (CLAUDE.md, .github/instructions, AGENTS.md)
   │ 1. extract  (loader)
   ▼
for each rule whose detector is missing or stale (prose hash changed):
   │ 2. derive:  heuristic first (core/derive) → LLM fallback (workshop, only if [workshop])
   ▼
   │ 3. prove in sandbox (health): does the detector actually match its intent?
   ▼
   proven      → materialised into the ephemeral cache, used for real detection
   not proven  → rule stays `advisory` (counted, shown, never a false green)
   │ 4. next run: prose hash mismatch → re-derive only the affected rule
   ▼
deterministic detection over the proven cache
```

### 3.4 Cache & determinism (decisions locked)

- **Cache is local and ephemeral:** `.guideline-cache/` (git-ignored), key =
  `hash(prose + engine version)`. Warm runs are fast; cold runs re-derive.
- **Resync is automatic at `check`:** `check` detects a hash mismatch, re-derives, updates
  the local cache. No manual command. `check` writes only the cache, never the repo tree.
- **Determinism guarantee:** heuristic derivation is pure (same prose → same detector), so
  ephemeral is safe. LLM derivation is non-deterministic and therefore **never runs without
  the `[workshop]` extra**. In a standard CI install (no `[workshop]`), a rule only an LLM
  could detect stays `advisory` — so a cold CI run is fully reproducible.

### 3.5 Rule-health as the headline (positioning pivot)

- Reports open on the **health matrix** (`proven`/`armed`/`dead`/`advisory` per rule) before
  the violation list.
- New subcommand `guideline-checker health`: the health audit alone, no violation scan — the
  sales pitch ("47 rules in CLAUDE.md; 5 are dead and have been lying green for months").
- `health.py` gains a second job: it is the **gate** of the generation loop — a derived
  detector is used only once proven; otherwise its rule is advisory.

## 4. Data flow (default install, `check`)

1. `cli.check` → `loader` extracts prose rules + `referential` loads any committed YAML.
2. `derive` + `cache`: for each rule with a missing/stale detector, derive heuristically;
   `health` proves it in `sandbox` (core sandbox path, read-only); proven → cache, else advisory.
3. `detection/` runs every proven `detect.<kind>` over the (excluded/`applyTo`-scoped) tree.
4. `baseline`/`config` filter; `reporters` emit health matrix + violations; exit code per `--fail-on`.

## 5. Error handling

- **Unreachable LLM / missing `[workshop]`:** never an error — affected rules fall back to
  `advisory`. Logged once, not per rule.
- **Malformed YAML / unknown `detect.kind`:** the offending rule is skipped with a warning;
  the run never crashes (existing "unknown keys ignored with a warning" contract extends here).
- **Cache corruption / version skew:** a cache whose key mismatches the engine version is
  ignored and re-derived, never trusted.
- **Sandbox proof failure:** treated as "not proven" → advisory, not an error.
- **Fleet `origin` fetch failure:** unchanged — a synthetic `origin-fetch-failed` error
  finding rather than silent compliance (feature now lives in `fleet/`).

## 6. Testing strategy

- **Unit, per kind:** each `detection/<kind>.py` gets an isolated test (the decomposition of
  `checker.py` makes this possible for the first time).
- **Derivation determinism test:** same prose fixture derived twice → byte-identical detector;
  guards the ephemeral-cache determinism claim.
- **CI-reproducibility test:** a rule that only an LLM could detect, run without `[workshop]`,
  must land `advisory` and produce a stable exit code across repeated runs.
- **Boundary test:** an import-graph assertion that `core/` imports neither `workshop/` nor
  `fleet/` (fails the build if the dependency rule is broken).
- **Migration test:** a repo whose rules were caught by the old phrase table still yields the
  same violations through the seed-translator + derivation path (kill-test for the rewrite).
- **Regression:** the existing 69-file suite is re-pointed at the new module paths; `docker-test`
  remains authoritative.

## 7. Migration & versioning

- **`v2.0.0` (breaking).** Removed: `central.py`, `push`, `web/central`. Relocated (not
  removed): workshop → `[workshop]`, fleet → `[fleet]` (incl. `synthesize --origin`).
- **`checker.py` is dissolved** into `core/detection/`; the `_*_checks` families become YAML
  or seed-translator entries.
- **`guidelines/` becomes a generated+validated artifact** for derived rules; hand-authored
  YAML still supported for rules a user writes directly.
- **`CLAUDE.md` 113K → ~15K:** keep only the ~10 conventions that touch this repo; import the
  rest of the chrysa standards by reference.
- **ADRs:** archive the ~10 obsolete meta-layer ADRs; add **D-0024 — core/workshop/fleet
  split + auto-derived proven detectors** (with kill-test: a proven detector that would flip
  to a different verdict once re-derived in CI means the determinism claim is false).

## 8. Open questions (resolve during planning, none block the spec)

- Exact plugin/entry-point mechanism for the core→satellite boundary (Python entry points vs
  a lazy registry) — pick during the first implementation lot.
- Whether `armed` vs `proven` needs a richer proof (fixtures shipped alongside a rule) or the
  current heuristic proof suffices.
- Cache location override (`--cache-dir`, `XDG_CACHE_HOME`) — cosmetic, decide in planning.

## 9. Cut list (explicit)

- `central.py`, `push` subcommand, `web/central` — **deleted**.
- The in-code `_*_checks` phrase table as a *detector* — **deleted** (survives only as the
  `derive/` seed translator).
- The "mechanisms-vs-values" philosophy scaffolding (`kinds.py`, much of `interpret.py`) —
  **collapsed** into the concrete kind registry; no standalone abstraction.
