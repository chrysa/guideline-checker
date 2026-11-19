"""Pydantic v2 models of the fleet scanner: configuration, identities and results.

Configuration models forbid unknown keys so a typo in YAML is a validation error, not a
silently ignored setting. Result models carry explicit statuses (:class:`Status`) — an
outcome is never a bare boolean — and every datetime is timezone-aware UTC.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

SCHEMA_VERSION = "1.0"
RULE_ID_PATTERN = r"^[A-Z]{2,6}-\d{3}$"
REPO_FULL_NAME_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"
PROJECT_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]*$"
ENV_VAR_PATTERN = r"^[A-Z_][A-Z0-9_]*$"
MAX_COMMITS_PER_PAGE = 100
IMPLICIT_PROFILE = "all"
REVIVAL_CAVEAT = "signals, not maturity verdict"


# ─── Enums ────────────────────────────────────────────────────────────────────


class Status(StrEnum):
    """Outcome of one rule on one repository."""

    PASS = "pass"  # noqa: S105 - a status label, not a credential
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"
    ERROR = "error"


class Dimension(StrEnum):
    """Scoring dimension. Scores are never merged across dimensions."""

    CONFORMANCE = "conformance"
    OPERATIONAL_READINESS = "operational_readiness"
    FUNCTIONAL_MATURITY = "functional_maturity"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ErrorKind(StrEnum):
    """Typed collection failure. ``invalid_response`` and ``interrupted`` extend the SPEC list."""

    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    AUTH = "auth"
    RATE_LIMITED = "rate_limited"
    SECONDARY_RATE_LIMITED = "secondary_rate_limited"
    SERVER_ERROR = "server_error"
    NETWORK = "network"
    CONFIG = "config"
    INVALID_RESPONSE = "invalid_response"
    INTERRUPTED = "interrupted"


# Kinds re-collected on resume: transient, or fixable without touching the repo (token, config).
RETRYABLE_ERROR_KINDS = frozenset(
    {
        ErrorKind.AUTH,
        ErrorKind.CONFIG,
        ErrorKind.RATE_LIMITED,
        ErrorKind.SECONDARY_RATE_LIMITED,
        ErrorKind.SERVER_ERROR,
        ErrorKind.NETWORK,
        ErrorKind.INTERRUPTED,
    }
)


class RepoOutcome(StrEnum):
    """Where a requested repository ended up in the scope."""

    EVALUATED = "evaluated"
    EXCLUDED = "excluded"
    ERROR = "error"


class RunStatus(StrEnum):
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"


# ─── Shared validators ────────────────────────────────────────────────────────


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalise aware ones to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (UTC)")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]


def _safe_repo_path(value: str) -> str:
    """A repository-relative POSIX path or glob: no absolute path, no ``..`` segment."""
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError(f"path must be repository-relative POSIX: {value!r}")
    if any(part == ".." for part in value.split("/")):
        raise ValueError(f"path must not contain '..': {value!r}")
    return value


RepoPath = Annotated[str, AfterValidator(_safe_repo_path)]


def _compilable_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {value!r}: {exc}") from exc
    return value


Regex = Annotated[str, AfterValidator(_compilable_regex)]


class _Strict(BaseModel):
    """Configuration base: unknown keys are errors, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _Result(BaseModel):
    """Result base: unknown keys rejected on reload; ``pass`` aliases serialise by alias."""

    model_config = ConfigDict(extra="forbid", serialize_by_alias=True, validate_by_name=True, validate_by_alias=True)


# ─── github.yaml ──────────────────────────────────────────────────────────────


class AuthConfig(_Strict):
    token_env: str = Field(default="GITHUB_TOKEN", pattern=ENV_VAR_PATTERN)
    required: bool = False


class TimeoutsConfig(_Strict):
    connect_s: float = Field(default=10.0, gt=0)
    read_s: float = Field(default=30.0, gt=0)


class PaginationConfig(_Strict):
    per_page: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1)


class RetriesConfig(_Strict):
    max_attempts: int = Field(default=4, ge=1, le=10)
    backoff_base_s: float = Field(default=1.0, ge=0)
    backoff_max_s: float = Field(default=30.0, ge=0)


class RateLimitConfig(_Strict):
    min_remaining: int = Field(default=50, ge=0)
    honor_retry_after: bool = True
    secondary_backoff_s: float = Field(default=60.0, ge=0)
    max_wait_s: float = Field(default=900.0, ge=0)


class CacheConfig(_Strict):
    enabled: bool = True
    dir: str = ".cache/fleet-scan/http"
    ttl_s: int = Field(default=3600, ge=0)
    use_etag: bool = True


class ContentConfig(_Strict):
    """Caps on what is fetched per file — content is evidence, never executed."""

    max_file_bytes: int = Field(default=512 * 1024, ge=1)


class LoggingConfig(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


DEFAULT_API_URL = "https://api.github.com"


class GithubConfig(_Strict):
    api_url: str = DEFAULT_API_URL
    # The token is sent to api_url: any other host must be opted into explicitly
    # (GitHub Enterprise, a test double), so a config edit cannot exfiltrate it silently.
    allow_custom_api_host: bool = False
    auth: AuthConfig = AuthConfig()
    timeouts: TimeoutsConfig = TimeoutsConfig()
    concurrency: int = Field(default=4, ge=1, le=32)
    pagination: PaginationConfig = PaginationConfig()
    retries: RetriesConfig = RetriesConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    cache: CacheConfig = CacheConfig()
    content: ContentConfig = ContentConfig()
    logging: LoggingConfig = LoggingConfig()

    @field_validator("api_url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("api_url must use https://")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _known_host_only(self) -> GithubConfig:
        if self.api_url != DEFAULT_API_URL and not self.allow_custom_api_host:
            raise ValueError(f"api_url {self.api_url!r} is not {DEFAULT_API_URL}; set allow_custom_api_host: true")
        return self


# ─── conformance.yaml ─────────────────────────────────────────────────────────


class DetectSpec(_Strict):
    """How a profile is auto-detected from the git tree. At least one criterion is required."""

    any_file: list[RepoPath] | None = None
    all_files: list[RepoPath] | None = None
    any_glob: list[RepoPath] | None = None
    none_of_profiles: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if not any((self.any_file, self.all_files, self.any_glob, self.none_of_profiles)):
            raise ValueError("detect needs at least one of any_file, all_files, any_glob, none_of_profiles")
        return self


class ProfileDef(_Strict):
    detect: DetectSpec
    description: str = ""


class SourceRef(_Strict):
    """Where a rule comes from — the standard it enforces."""

    document: str
    locator: str | None = None
    repo: str | None = None
    ref: str | None = None


class FileExistsParams(_Strict):
    any_of: list[RepoPath] = Field(min_length=1)


class FileExistsMethod(_Strict):
    type: Literal["file_exists"]
    params: FileExistsParams


class FileAbsentParams(_Strict):
    paths: list[RepoPath] = Field(min_length=1)


class FileAbsentMethod(_Strict):
    type: Literal["file_absent"]
    params: FileAbsentParams


class TreeGlobParams(_Strict):
    glob: RepoPath
    min_count: int = Field(default=1, ge=1)


class TreeGlobMethod(_Strict):
    type: Literal["tree_glob"]
    params: TreeGlobParams


class FileRegexParams(_Strict):
    path: RepoPath | None = None
    any_of: list[RepoPath] | None = None
    pattern: Regex
    expect: Literal["present", "absent"] = "present"
    missing: Literal["fail", "pass", "unknown"] | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.path is None) == (not self.any_of):
            raise ValueError("file_regex needs exactly one of 'path' or a non-empty 'any_of'")
        return self

    @property
    def candidates(self) -> list[str]:
        return [self.path] if self.path is not None else list(self.any_of or [])


class FileRegexMethod(_Strict):
    type: Literal["file_regex"]
    params: FileRegexParams


class MakefileTargetsParams(_Strict):
    targets: list[str] = Field(min_length=1)
    path: RepoPath = "Makefile"


class MakefileTargetsMethod(_Strict):
    type: Literal["makefile_targets"]
    params: MakefileTargetsParams


RepoFieldName = Literal[
    "archived",
    "disabled",
    "fork",
    "private",
    "visibility",
    "default_branch",
    "has_issues",
    "has_wiki",
    "has_projects",
    "has_discussions",
    "is_template",
    "description",
    "homepage",
    "topics",
    "license",
]


class RepoFieldParams(_Strict):
    field: RepoFieldName
    op: Literal["eq", "ne", "in"]
    value: JsonValue

    @model_validator(mode="after")
    def _in_needs_list(self) -> Self:
        if self.op == "in" and not isinstance(self.value, list):
            raise ValueError("op 'in' needs a list value")
        return self


class RepoFieldMethod(_Strict):
    type: Literal["repo_field"]
    params: RepoFieldParams


class CiLastRunParams(_Strict):
    branch: str = Field(default="default", min_length=1)
    max_age_days: int | None = Field(default=None, ge=1)
    # A failed run shorter than this never executed a step (billing / quota / account
    # block), so it says nothing about the code: reported as unknown. 0 disables.
    min_failure_duration_s: int = Field(default=0, ge=0)


class CiLastRunMethod(_Strict):
    type: Literal["ci_last_run"]
    params: CiLastRunParams = CiLastRunParams()


class CommitMessagesAbsentParams(_Strict):
    pattern: Regex
    last_n: int = Field(default=20, ge=1, le=MAX_COMMITS_PER_PAGE)


class CommitMessagesAbsentMethod(_Strict):
    type: Literal["commit_messages_absent"]
    params: CommitMessagesAbsentParams


class LastCommitAgeParams(_Strict):
    max_days: int = Field(ge=1)


class LastCommitAgeMethod(_Strict):
    type: Literal["last_commit_age"]
    params: LastCommitAgeParams


MethodSpec = Annotated[
    FileExistsMethod
    | FileAbsentMethod
    | TreeGlobMethod
    | FileRegexMethod
    | MakefileTargetsMethod
    | RepoFieldMethod
    | CiLastRunMethod
    | CommitMessagesAbsentMethod
    | LastCommitAgeMethod,
    Field(discriminator="type"),
]

METHOD_TYPES: tuple[str, ...] = (
    "file_exists",
    "file_absent",
    "tree_glob",
    "file_regex",
    "makefile_targets",
    "repo_field",
    "ci_last_run",
    "commit_messages_absent",
    "last_commit_age",
)


class RuleDef(_Strict):
    id: str = Field(pattern=RULE_ID_PATTERN)
    title: str = Field(min_length=1)
    description: str = ""
    source: SourceRef
    dimension: Dimension
    profiles: list[str] = Field(default_factory=lambda: [IMPLICIT_PROFILE], min_length=1)
    severity: Severity
    blocking: bool = False
    evidence: str = ""
    method: MethodSpec


class ConformanceConfig(_Strict):
    schema_version: str
    rules_version: str = Field(min_length=1)
    profiles: dict[str, ProfileDef] = Field(default_factory=dict)
    rules: list[RuleDef] = Field(default_factory=list)


# ─── repos.yaml ───────────────────────────────────────────────────────────────

LooseDate = date | datetime | str


class CatalogSource(_Strict):
    kind: str
    ref: str
    fetched_at: LooseDate | None = None


class NotionRef(_Strict):
    page_id: str | None = None
    url: str | None = None


class RepoRef(_Strict):
    full_name: str = Field(pattern=REPO_FULL_NAME_PATTERN)
    role: Literal["primary", "secondary", "mirror", "legacy", "fork"] = "primary"
    scan_profile: Literal["auto"] | list[str] = "auto"
    include: bool = True
    exclusion_reason: str | None = None
    archived: bool = False

    @model_validator(mode="after")
    def _exclusion_needs_reason(self) -> Self:
        if not self.include and not (self.exclusion_reason or "").strip():
            raise ValueError(f"{self.full_name}: include: false requires a non-empty exclusion_reason")
        if self.archived:
            raise ValueError(f"{self.full_name}: archived repositories are out of scope; remove the entry")
        return self


class Project(_Strict):
    id: str = Field(pattern=PROJECT_ID_PATTERN)
    canonical_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    owner: str = Field(min_length=1)
    perimeter: Literal["chrysa", "travaux", "prive", "collab-heya", "padam", "other"]
    notion: NotionRef | None = None
    component_kind: Literal["app", "lib", "tool", "infra", "docs", "config", "game", "template", "unknown"] = "unknown"
    doc_status: Literal["documented", "partial", "stub", "missing", "unknown"] = "unknown"
    repos: list[RepoRef] = Field(default_factory=list)


class ReposConfig(_Strict):
    schema_version: str
    generated_at: LooseDate | None = None
    sources: list[CatalogSource] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    subsets: dict[str, list[str]] = Field(default_factory=dict)


# ─── reporters.yaml ───────────────────────────────────────────────────────────


class CliReporterConfig(_Strict):
    enabled: bool = True
    max_rows: int = Field(default=50, ge=1)


class FileReporterConfig(_Strict):
    enabled: bool = True
    filename: str = Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")


class HistoryReporterConfig(_Strict):
    enabled: bool = True
    index: str = ".fleet-scan/history.jsonl"
    retention_runs: int = Field(default=30, ge=1)


class NotionReporterConfig(_Strict):
    """The Notion reporter is a disabled stub: ``enabled`` can only be ``false``."""

    enabled: Literal[False] = False
    reason: str = "contract and writes not authorised"


class RevivalConfig(_Strict):
    enabled: bool = True
    top_n: int = Field(default=20, ge=1)


class ReportersConfig(_Strict):
    output_dir: str = ".fleet-scan/runs"
    cli: CliReporterConfig = CliReporterConfig()
    json_report: FileReporterConfig = Field(default=FileReporterConfig(filename="report.json"), alias="json")
    markdown: FileReporterConfig = FileReporterConfig(filename="report.md")
    history: HistoryReporterConfig = HistoryReporterConfig()
    notion: NotionReporterConfig = NotionReporterConfig()
    revival: RevivalConfig = RevivalConfig()

    model_config = ConfigDict(extra="forbid", frozen=True, validate_by_name=True, validate_by_alias=True)


# ─── Results ──────────────────────────────────────────────────────────────────


class Evidence(_Result):
    """One observed fact and where it came from (endpoint + fetch time = provenance)."""

    kind: str
    locator: str
    value: JsonValue = None
    source_endpoint: str
    fetched_at: UtcDatetime


class CollectionError(_Result):
    kind: ErrorKind
    message: str
    endpoint: str | None = None
    status_code: int | None = None


class CheckResult(_Result):
    rule_id: str
    status: Status
    dimension: Dimension
    severity: Severity
    blocking: bool
    message: str
    evidence: list[Evidence] = Field(default_factory=list)
    error_kind: ErrorKind | None = None

    @model_validator(mode="after")
    def _error_kind_iff_error(self) -> Self:
        if (self.status is Status.ERROR) != (self.error_kind is not None):
            raise ValueError("error_kind must be set exactly when status is 'error'")
        return self


class DimensionScore(_Result):
    """``score = pass/(pass+fail)``; ``coverage = (pass+fail)/applicable``. ``None`` = no denominator."""

    score: float | None
    coverage: float | None
    passed: int = Field(alias="pass", ge=0)
    fail: int = Field(ge=0)
    unknown: int = Field(ge=0)
    error: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    applicable: int = Field(ge=0)
    blocked: bool = False
    blocking_failures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.applicable != self.passed + self.fail + self.unknown + self.error:
            raise ValueError("applicable must equal pass + fail + unknown + error")
        return self


class RevivalSignal(_Result):
    name: str
    status: Status
    value: JsonValue = None
    points: float = 0.0
    detail: str = ""


class RepoResult(_Result):
    repo: str
    outcome: RepoOutcome
    project_id: str | None = None
    canonical: str | None = None
    role: str | None = None
    aliases: list[str] = Field(default_factory=list)
    exclusion_reason: str | None = None
    ref: str | None = None
    commit_sha: str | None = None
    profiles: list[str] = Field(default_factory=list)
    profiles_status: Status = Status.UNKNOWN
    collected_at: UtcDatetime | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    collection_errors: list[CollectionError] = Field(default_factory=list)
    scores: dict[Dimension, DimensionScore] = Field(default_factory=dict)
    blocked: bool = False
    blocking_failures: list[str] = Field(default_factory=list)
    revival_signals: list[RevivalSignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _error_outcome_has_error(self) -> Self:
        if self.outcome is RepoOutcome.ERROR and not self.collection_errors:
            raise ValueError("an 'error' outcome must carry at least one collection error")
        if self.outcome is RepoOutcome.EXCLUDED and not self.exclusion_reason:
            raise ValueError("an 'excluded' outcome must carry an exclusion_reason")
        return self

    @property
    def is_final(self) -> bool:
        """Kept as-is on resume: evaluated, or failed for a reason a retry cannot change (404/403)."""
        if self.outcome is RepoOutcome.EVALUATED:
            return True
        if self.outcome is RepoOutcome.EXCLUDED:
            return False  # re-derived from the (possibly changed) catalog; costs no request
        return not any(e.kind in RETRYABLE_ERROR_KINDS for e in self.collection_errors)


class ExcludedRepo(_Result):
    repo: str
    reason: str


class ScopeError(_Result):
    repo: str
    kind: ErrorKind
    message: str


class ScanScope(_Result):
    requested: list[str]
    collected: list[str] = Field(default_factory=list)
    evaluated: list[str] = Field(default_factory=list)
    excluded: list[ExcludedRepo] = Field(default_factory=list)
    errors: list[ScopeError] = Field(default_factory=list)

    @model_validator(mode="after")
    def _partition(self) -> Self:
        requested = set(self.requested)
        if len(requested) != len(self.requested):
            raise ValueError("scope.requested must not contain duplicates")
        buckets = [*self.evaluated, *(e.repo for e in self.excluded), *(e.repo for e in self.errors)]
        if sorted(buckets) != sorted(self.requested):
            raise ValueError("every requested repo must appear exactly once in evaluated, excluded or errors")
        if not set(self.collected) <= requested:
            raise ValueError("scope.collected must be a subset of scope.requested")
        return self


class Aggregates(_Result):
    repos_requested: int
    repos_evaluated: int
    repos_excluded: int
    repos_errored: int
    status_totals: dict[Status, int]
    error_kinds: dict[ErrorKind, int]
    dimensions: dict[Dimension, DimensionScore]
    blocked_repos: list[str]


class RevivalCandidate(_Result):
    rank: int = Field(ge=1)
    repo: str
    project_id: str | None
    signal_points: float
    signals: list[RevivalSignal]
    caveat: str = REVIVAL_CAVEAT


class ScanReport(_Result):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    started_at: UtcDatetime
    finished_at: UtcDatetime
    tool_version: str
    rules_version: str
    rules_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    resumed: bool = False
    forced_config_change: bool = False
    scope: ScanScope
    repos: list[RepoResult]
    aggregates: Aggregates
    revival_candidates: list[RevivalCandidate] = Field(default_factory=list)
    revival_caveat: str = REVIVAL_CAVEAT

    @model_validator(mode="after")
    def _invariants(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be >= started_at")
        names = [r.repo for r in self.repos]
        if sorted(names) != sorted(self.scope.requested):
            raise ValueError("every requested repo must appear exactly once in repos")
        return self


class RunManifest(_Result):
    """``manifest.json`` of a run directory — what a resume must match."""

    schema_version: str = SCHEMA_VERSION
    run_id: str
    status: RunStatus
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    tool_version: str
    rules_version: str
    rules_sha256: str
    config_fingerprint: str
    subset: str | None = None
    requested: list[str]
    resumed: bool = False
    forced_config_change: bool = False
    previous_fingerprints: list[str] = Field(default_factory=list)


def utc_now() -> datetime:
    """The scanner's single source of "now" (injected in tests through the engine clock)."""
    return datetime.now(tz=UTC)


def json_safe(value: Any) -> JsonValue:  # noqa: ANN401 - boundary normaliser for raw API payloads
    """Coerce an arbitrary decoded-JSON value into a :data:`JsonValue` for evidence."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)
