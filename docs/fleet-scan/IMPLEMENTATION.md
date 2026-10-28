# fleet-scan — implementation notes (MVP, lot L3 engine)

Branch `feat/fleet-scan-mvp` (local, not pushed). Design: [SPEC.md](SPEC.md).

## Modules (`guideline_checker/fleet/scan/`)

| module | role |
|---|---|
| `models.py` | pydantic v2 models: config (`GithubConfig`, `ConformanceConfig`, `RuleDef`, `ProfileDef`, `MethodSpec` discriminated union with one validated params model per method, `ReposConfig`, `Project`, `RepoRef`, `ReportersConfig`) and results (`Evidence`, `CollectionError`, `CheckResult`, `DimensionScore`, `RepoResult`, `ScanScope`, `Aggregates`, `RevivalCandidate`, `ScanReport`, `RunManifest`). Status enums everywhere, UTC-aware datetimes, invariants as validators (scope partition, `finished_at >= started_at`, counts). |
| `config.py` | loads every YAML with `yaml.safe_load`, aggregates all problems, cross-validates (rule id pattern + uniqueness, closed method registry, profile references, no negative-profile chaining, mapping files → rule ids/profiles, subsets → catalog, duplicates, `include: false` → reason). Computes `rules_sha256` and `config_fingerprint`. |
| `github.py` | async httpx client: semaphore, Link pagination with `max_pages` (+ `truncated` flag, foreign-host `next` refused), bounded retries with exponential backoff + jitter (5xx/network), primary limit from `x-ratelimit-*` (wait ≤ `max_wait_s`, else `RateLimitedError`), secondary limit (403/429 + `retry-after` or message), typed errors, provenance (`endpoint`, `fetched_at`). |
| `http_cache.py`, `atomic_io.py`, `redaction.py` | ETag cache (hashed file names, TTL, 304 revalidation), atomic writes, token-redacting log filter. |
| `collect.py` | `CollectionNeeds.from_rules` (fetch only what active rules/revival need) + `Collector`: metadata, head commit, recursive tree, needed file contents (base64, size cap), latest completed workflow run (`per_page=1`), recent commits, one tag. Per-facet typed errors. |
| `profiles.py`, `globbing.py` | tri-state profile detection (matched / not matched / undetermined); repo globs (`**` crosses segments). |
| `rules.py` | closed `REGISTRY` implementing the 9 SPEC methods; applicability → `not_applicable` / `unknown` / `error`. |
| `scoring.py` | SPEC formula per repo × dimension, fleet micro-aggregates, blocking failures listed separately. |
| `revival.py` | revival signals + ranking (caveat: "signals, not maturity verdict"). |
| `engine.py`, `run_store.py` | scope resolution, bounded concurrency, atomic per-repo checkpoints, interruption → resumable manifest, resume with fingerprint check, report regeneration. |
| `reporters/` | `cli.py` (text table), `json_out.py`, `markdown.py` (scope, totals, blocking, per repo with denominators, unknowns, errors, revival, reprise), `history.py` (JSONL index + retention), `notion.py` (refusing stub), `publisher.py`. |
| `arguments.py`, `cli.py` | argparse declaration (stdlib only) + handlers, lazily imported by `guideline_checker/cli.py`. |

## Commands

```
make scan-validate                 # docker: guideline-checker fleet-scan validate-config
make scan-test                     # docker (network_mode: none): pytest tests/fleet_scan, cov floor 90%
make scan-run SUBSET=pilot         # docker: needs GITHUB_TOKEN in the host env (passed through)
make scan-resume RUN=<run_id>
make scan-report RUN=<run_id>
guideline-checker fleet-scan run --repos owner/a,owner/b [--output-dir DIR] [--config-dir DIR]
```

Exit codes: 0 ok, 2 config/usage error or refused resume, 130 interrupted (run resumable).

## Verification (2026-10-25, containerised)

| command | result |
|---|---|
| `make scan-test` | 126 passed, coverage of `fleet.scan` 96.7% (floor 90%) |
| `docker compose run test pytest tests` (image of `make docker-test`) | 1079 passed, total coverage 91.9% (floor 85%) |
| lint container: `ruff check guideline_checker/fleet guideline_checker/cli.py tests/fleet_scan`, `ruff format --check`, `mypy guideline_checker` (strict, pydantic plugin) | all clean |
| `make docker-lint` | **fails on pre-existing findings only**: 7 `PLR2004` in untouched files (`core/detection/*`, `linters.py`, `loader.py`, `reporters/html.py`) reported by the unpinned ruff 0.17.2 the image resolves |
| `make scan-validate` against the real config of the other lots | OK: 26 rules, 13 profiles, 282 projects, 171 repos, 3 subsets |
| gitleaks (repo config) on the new dirs | no leaks |

No real network scan was performed.

## Decisions and deviations from SPEC

- `ErrorKind` adds `invalid_response` (unexpected status such as 409 "empty repository", non-JSON body,
  foreign pagination link) and `interrupted` (repo never finished in an interrupted run).
- `RepoResult.outcome` (`evaluated` / `excluded` / `error`) makes the scope partition explicit;
  excluded repos also appear in `repos` (SPEC: every requested repo appears once in `repos`).
- `scope.collected` = repos for which a collection attempt returned (including a 404 on metadata);
  `evaluated` = repos whose metadata was read and rules ran.
- `config_fingerprint` covers conformance, repos, both mappings and, from github.yaml, only
  `api_url`, `pagination` and `content` (transport tuning does not change results).
- Resume keeps `evaluated` results and errors a retry cannot change (`not_found`, `forbidden`,
  `invalid_response`); `rate_limited`, `secondary_rate_limited`, `server_error`, `network`, `auth`,
  `config`, `interrupted` and excluded repos are processed again.
- Fresh cache entries (younger than `ttl_s`) are served without a request; their evidence keeps the
  original `fetched_at`. Older entries are revalidated with `If-None-Match`.
- Revival points reward foundations (tests, CI, container files, tag/changelog, README ≥ 1000 B);
  last-commit age is reported and used as tie-breaker only.
- Added config keys (owned files): `github.content.max_file_bytes`, `reporters.revival`.
- pydantic floor is `>=2.11` (not 2.7): `serialize_by_alias` keeps the contract key `pass`.
- `uv.lock` regenerated with `ghcr.io/astral-sh/uv:python3.14-bookworm-slim uv lock`: metadata-only
  diff (new `scan` extra), no version change.

## Known limits

- Unauthenticated runs (no token) have 60 requests/hour; with `min_remaining: 50` they stop early.
- Profile detection matches exact paths (`any_file`) or globs (`any_glob`); no content sniffing.
- `ci_last_run` reads the latest completed run of the branch across all workflows.
- Response bodies are not size-capped (GitHub caps them); file contents are (`max_file_bytes`).
- Rule regexes come from the trusted config; no ReDoS guard beyond the content size cap.
- History retention prunes index lines only; run directories are never deleted.
- Notion reporter is a stub that refuses to run (`notion.enabled` can only be `false`).
- The cache directory may contain private-repository content: created `0700`, files `0600`, git-ignored.

## Integration changes (2026-10-25)

- `ci_last_run` reads push runs only (`event=push`): schedule, Dependabot and PR-helper workflows no
  longer stand in for the branch's CI. New param `min_failure_duration_s` (OPS-006: 15): a failed run
  shorter than that executed no step (Actions billing / account block) and is `unknown`, not `fail`.
- Mapping cross-check also reads `related_rule_ids` and `applies_to_profiles` (skills-mapping).

## Security review (independent, 2026-10-25)

No Critical/High finding. Fixed:
- `api_url` other than `https://api.github.com` requires `allow_custom_api_host: true` (the token follows `api_url`).
- Host check moved into `_get`, so a `next` link replayed from the on-disk cache is checked too.
- Checkpoint file names use `fullmatch` (a trailing newline no longer passes the `$` anchor).
- Markdown cells escape `\`, `` ` ``, `[`, `]`, `<`, `>`, `\r` (API text can no longer inject links/HTML).
- An existing cache directory is re-chmodded to `0700`.

Open (documented, not fixed in the MVP):
- Medium: images install `pydantic`/`httpx` from ranges, not `uv.lock`; the base image is not pinned by digest.
  The rest of the repository's Dockerfile follows the same pattern; switching all stages to
  `uv sync --frozen` is a separate change.
- Low: response bodies are read whole (no streaming byte cap); config regexes have no timeout;
  the redaction filter does not rewrite `exc_text`/`stack_info`; output paths from config are not
  confined; `scan-test` service lacks `read_only`/`cap_drop`; no `mem_limit`/`pids_limit`.

<!-- PR link gate check (docs/gate-check-b), not to be merged. -->
