# fleet-scan — Scanner MVP specification

Status: draft v1 (2026-10-24). Local branch `feat/fleet-scan-mvp`, not pushed.

## Why here and not in a new repository

The Notion page "Pipeline autonome de convergence — challenge de chrysa-forge" (2026-10-16)
concluded: no `chrysa-forge` repository; four of the six envisaged components already exist.
Its decision D1 recommends option (a): add a batch mode to `guideline-checker` (the "Enforcer")
plus a thin orchestration. `guideline-checker` already owns a `fleet/` package (gh-backed
multi-repo governance), JSON/Markdown/SARIF reporters and a Docker/Make workflow. The scanner
is therefore a new sub-package `guideline_checker/fleet/scan/`, kept out of the default install
behind the `scan` extra. D1 is still open in Notion: this placement is a proposal to validate.

## Scope (MVP)

In: load + validate YAML config; resolve the requested scope; collect evidence through the
GitHub REST API (static inspection only, never executing repository code); identify canonical
repos and aliases; run a limited set of rules; keep every error and unknown; JSON + Markdown +
CLI reports; scan history; checkpoint + resume; revival-candidate list.

Out: auto-fix, project generation, Notion/Shortcut writes, deployment, dashboard.

## Layout

```
config/fleet-scan/
  github.yaml             # client: auth env ref, timeouts, concurrency, retries, rate limits, cache
  conformance.yaml        # rules + profiles (owned by the standards lot)
  repos.yaml              # project/repo catalog + subsets (owned by the catalog lot)
  reporters.yaml          # outputs, paths, retention
  standards-mapping.yaml  # requirement -> source -> rule (owned by the standards lot)
  skills-mapping.yaml     # skill recommendation -> rule/profile (owned by the standards lot)
guideline_checker/fleet/scan/
  models.py      # pydantic v2 models (config + results)
  config.py      # load + cross-validate all YAML files, compute fingerprints
  github.py      # async httpx client: pagination, bounded retries, primary/secondary limits, ETag cache
  collect.py     # evidence collection per repo
  profiles.py    # profile resolution (explicit or auto-detected from the tree)
  rules.py       # evaluation methods (closed registry)
  engine.py      # orchestration, concurrency, checkpoint/resume, aggregation
  scoring.py     # per-dimension score + coverage, blocking failures surfaced separately
  revival.py     # revival-candidate signals (not a maturity verdict)
  reporters/     # cli.py, json_out.py, markdown.py, history.py
  cli.py         # `guideline-checker fleet-scan ...`
```

## Result statuses

`pass` | `fail` | `not_applicable` | `unknown` | `error`. Never a bare boolean.
- `not_applicable`: no repo profile matches the rule's profiles.
- `unknown`: evidence could not be determined (e.g. no CI run exists, file too large, ambiguous).
- `error`: collection failed (typed error attached: `not_found`, `forbidden`, `auth`, `rate_limited`,
  `secondary_rate_limited`, `server_error`, `network`, `config`). A 404 means "not visible to this
  token", never "deleted".

## Dimensions

`conformance` (standards), `operational_readiness` (can it build/run/deploy), `functional_maturity`
(signals only). Scores are never merged across dimensions.

## Scoring

Per repo and per dimension: `score = pass / (pass + fail)`; `coverage = (pass + fail) / applicable`
where `applicable = pass + fail + unknown + error`. Unknown/error never count as pass. Any failed
rule with `blocking: true` sets `blocked: true` and is listed, whatever the score. Reports always
print score, coverage and the denominator.

## conformance.yaml schema

```yaml
schema_version: "1.0"
rules_version: "2026.10.24-1"        # bump on any rule change
profiles:
  python:      {detect: {any_file: ["pyproject.toml", "setup.py", "requirements.txt"]}}
  node:        {detect: {any_file: ["package.json"]}}
  container:   {detect: {any_file: ["Dockerfile", "docker-compose.yml", "compose.yaml"]}}
  docs-only:   {detect: {none_of_profiles: ["python", "node", "unity", "go"]}}
  # profile "all" is implicit and always matches
rules:
  - id: DOC-001                       # ^[A-Z]{2,6}-\d{3}$ unique
    title: README present
    description: ...
    source: {document: "shared-standards/STANDARDS.chrysa.md", locator: "§x / anchor",
             repo: "chrysa/shared-standards", ref: "<commit sha or tag>"}
    dimension: conformance            # conformance | operational_readiness | functional_maturity
    profiles: [all]
    severity: medium                  # critical | high | medium | low | info
    blocking: false
    evidence: "README.md at repo root on the default branch"
    method: {type: file_exists, params: {any_of: ["README.md", "README.rst"]}}
```

Closed method registry (engine rejects unknown types at config validation):

| type | params | pass when |
|---|---|---|
| `file_exists` | `any_of: [path]` | one path exists in the tree |
| `file_absent` | `paths: [glob]` | no tree path matches |
| `tree_glob` | `glob`, `min_count` (default 1) | at least `min_count` tree paths match |
| `file_regex` | `path` (or `any_of`), `pattern`, `expect: present\|absent` | content matches (or not); missing file = `fail` if expect present, `pass` if absent... unless `missing: unknown` |
| `makefile_targets` | `targets: [name]`, `path` default `Makefile` | every target declared |
| `repo_field` | `field` (archived, visibility, default_branch, has_issues...), `op` (eq, ne, in), `value` | comparison true |
| `ci_last_run` | `branch: default`, `max_age_days` optional | latest completed workflow run on branch concluded `success`; no run = `unknown` |
| `commit_messages_absent` | `pattern`, `last_n` (<=100) | no recent commit message matches |
| `last_commit_age` | `max_days` | default-branch head commit newer than max_days |

## repos.yaml schema

```yaml
schema_version: "1.0"
generated_at: "2026-10-24"
sources: [{kind: github, ref: "gh repo list chrysa", fetched_at: ...}, {kind: notion, ref: "collection://..."}]
projects:
  - id: portfolio-viz                 # stable slug
    canonical_name: portfolio-viz
    aliases: [pviz, chrysa-portfolio-viz]
    owner: chrysa
    perimeter: chrysa                 # chrysa | travaux | prive | collab-heya | padam | other
    notion: {page_id: "...", url: "..."}   # or null
    component_kind: app               # app | lib | tool | infra | docs | config | game | template | unknown
    doc_status: unknown               # documented | partial | stub | missing | unknown
    repos:
      - full_name: chrysa/chrysa-portfolio-viz
        role: primary                 # primary | secondary | mirror | legacy | fork
        scan_profile: auto            # auto or explicit list of profile names
        include: true
        exclusion_reason: null        # mandatory when include is false (e.g. "collab", "fork")
        archived: false               # true is rejected: archived repos are out of scope
subsets:
  pilot: [chrysa/guideline-checker, ...]   # <= 10 for the first validated run
```

A project with `repos: []` stays in the catalog and in the report as "no repository".

## github.yaml schema

```yaml
api_url: https://api.github.com
auth: {token_env: GITHUB_TOKEN, required: false}     # value never stored in config
timeouts: {connect_s: 10, read_s: 30}
concurrency: 4
pagination: {per_page: 100, max_pages: 10}
retries: {max_attempts: 4, backoff_base_s: 1.0, backoff_max_s: 30}
rate_limit: {min_remaining: 50, honor_retry_after: true, secondary_backoff_s: 60, max_wait_s: 900}
cache: {enabled: true, dir: .cache/fleet-scan/http, ttl_s: 3600, use_etag: true}
logging: {level: INFO}
```

Rate limits come from response headers (`x-ratelimit-remaining`, `x-ratelimit-reset`,
`retry-after`), never from constants. Below `min_remaining` the client waits for reset if
`<= max_wait_s`, otherwise stops collecting and marks remaining repos `error/rate_limited`.

## reporters.yaml schema

```yaml
output_dir: .fleet-scan/runs
cli: {enabled: true, max_rows: 50}
json: {enabled: true, filename: report.json}
markdown: {enabled: true, filename: report.md}
history: {enabled: true, index: .fleet-scan/history.jsonl, retention_runs: 30}
notion: {enabled: false, reason: "contract and writes not authorised"}
```

## Report contract (JSON)

`ScanReport{schema_version, run_id, started_at, finished_at, tool_version, rules_version,
rules_sha256, config_fingerprint, scope{requested[], collected[], evaluated[], excluded[{repo, reason}],
errors[{repo, kind, message}]}, repos[RepoResult], aggregates, revival_candidates[]}`.

`RepoResult{repo, project_id, canonical, aliases, ref, commit_sha, profiles[], collected_at,
checks[CheckResult{rule_id, status, dimension, severity, blocking, message, evidence[{kind, locator,
value, source_endpoint, fetched_at}]}], collection_errors[], scores{dimension: {score, coverage,
pass, fail, unknown, error, not_applicable, applicable}}, blocked, blocking_failures[]}`.

Every requested repo appears exactly once in `scope` and in `repos` (with errors if needed).

## Checkpoint / resume

Run dir `<output_dir>/<run_id>/`: `manifest.json` (config fingerprint, requested scope),
`repos/<owner>__<name>.json` written atomically per finished repo. `--resume <run_id>` reloads
finished repos when the config fingerprint matches (else refuses unless `--force-config-change`),
collects the rest, then aggregates.

## Commands (all containerised)

```
make scan-validate                    # validate all config/fleet-scan/*.yaml
make scan-test                        # pytest for the scan package (docker)
make scan-run SUBSET=pilot            # scan a subset (needs GITHUB_TOKEN in env)
make scan-resume RUN=<run_id>
make scan-report RUN=<run_id>         # regenerate reports from saved results
```

## Implementation notes (2026-10-25)

Implemented as specified, with the deviations listed in [IMPLEMENTATION.md](IMPLEMENTATION.md)
("Decisions and deviations from SPEC"): two extra error kinds (`invalid_response`, `interrupted`),
an explicit `RepoResult.outcome`, a fingerprint restricted to result-affecting settings, and the
resume policy for retryable errors.

## Archived projects are out of scope

Owner decision of 2026-10-25: archived projects are not processed.

- The catalog lists no repo archived on GitHub and no project whose Notion fiche sits in the
  "Archives" pillar. `archived: true` in `repos.yaml` is a configuration error.
- A repo found archived at scan time is stopped after its metadata call: no tree, file, CI,
  commit or tag request, no check. It is reported `excluded` with reason
  "archived on GitHub; out of scope, not evaluated", so the requested scope still adds up.

## Forks are judged on their own commits

Commit-message rules (`commit_messages_absent`, e.g. CONF-010 and SCM-001) read a fork's own
commits only. Upstream history was written by someone else, so it is not held against the fork.

- For a repo whose metadata says `fork: true`, the collector calls
  `compare/{upstream_owner}:{upstream_default_branch}...{head}` instead of `commits`. It keeps
  the newest `last_n` commits the fork carries on top of its parent.
- The check message then reads "commit(s) on top of <parent>" instead of "recent commit(s)".
- A fork whose parent is not reported cannot have its own commits told apart. Its commit facet
  is recorded as an `invalid_response` collection error, so these rules report `error` rather
  than judge upstream commits.
