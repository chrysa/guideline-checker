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

---
