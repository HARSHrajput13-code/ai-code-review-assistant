"""Transport DTOs (CIS §6.3, §6.5, §6.6). Separate from the domain models; built by mapping.py.

Every enum is declared explicitly (the shared string enums), so the generated TypeScript types are
string-literal unions (§6.9). Response fields have no defaults, so every key is required; an
optional value is `null`.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.domain.enums import (
    Category,
    Confidence,
    ErrorCode,
    ImprovedCodeStatus,
    Language,
    OutcomeStatus,
    Provenance,
    ReviewStage,
    ReviewStatus,
    ScoreBand,
    ScoreCap,
    Severity,
    SeveritySource,
    SkipReason,
    SummarySource,
)
from shared.domain.models import MAX_ISSUES_RETURNED

LANGUAGE_PATTERN = r"^[a-z0-9_+-]+$"


class ReviewCreateRequest(BaseModel):
    """The body of POST /api/v1/reviews. Content rules belong to the application validator."""

    model_config = ConfigDict(extra="forbid")

    # Deliberately a string, not an enum: an unknown language is UNSUPPORTED_LANGUAGE (D-24).
    language: str = Field(min_length=1, max_length=32, pattern=LANGUAGE_PATTERN)
    source_code: str


class _Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StageOutcomeDTO(_Response):
    status: OutcomeStatus
    error_code: ErrorCode | None
    skip_reason: SkipReason | None
    message: str | None
    duration_ms: int


class LocationDTO(_Response):
    start_line: int
    end_line: int


class IssueSourceDTO(_Response):
    origin: str
    rule_key: str | None


class IssueDTO(_Response):
    issue_id: str
    severity: Severity
    severity_source: SeveritySource
    category: Category
    title: str
    location: LocationDTO | None
    additional_locations: list[LocationDTO]
    occurrence_count: int
    summary: str
    impact: str
    recommendation: str
    provenance: Provenance
    confidence: Confidence
    sources: list[IssueSourceDTO]


class SummaryDTO(_Response):
    text: str
    source: SummarySource


class CategoryScoreDTO(_Response):
    category: Category
    assessed: bool
    score: int | None
    issue_count: int


class ScoreDTO(_Response):
    overall: int
    band: ScoreBand
    provisional: bool
    assessed_weight: int
    categories: list[CategoryScoreDTO]
    caps_applied: list[ScoreCap]
    policy_version: str


class CoverageDTO(_Response):
    complete: bool
    unassessed_categories: list[Category]
    missing_components: list[str]


class SeverityCountsDTO(BaseModel):
    CRITICAL: int
    HIGH: int
    MEDIUM: int
    LOW: int


class ImprovedCodeDTO(_Response):
    status: ImprovedCodeStatus
    code: str | None
    notes: list[str]
    failure_code: ErrorCode | None
    message: str | None


class ToolOutcomeDTO(_Response):
    tool: str
    tool_version: str | None
    outcome: StageOutcomeDTO


class AnalysisDTO(_Response):
    static_analysis: StageOutcomeDTO
    static_tools: list[ToolOutcomeDTO]
    ai_analysis: StageOutcomeDTO
    improvement: StageOutcomeDTO


class ResultCapabilitiesDTO(BaseModel):
    static_analysis: bool
    ai_analysis: bool
    improved_code: bool


class WarningDTO(_Response):
    code: str
    message: str


class ToolVersionDTO(_Response):
    tool: str
    version: str


class MetadataDTO(_Response):
    ai_provider: str | None
    ai_model: str | None
    prompt_version: str | None
    scoring_policy_version: str
    analyzer_versions: list[ToolVersionDTO]
    source_bytes: int
    source_lines: int
    duration_ms: int


class ReviewResultDTO(BaseModel):
    summary: SummaryDTO
    score: ScoreDTO
    coverage: CoverageDTO
    issues: list[IssueDTO] = Field(max_length=MAX_ISSUES_RETURNED)
    total_issue_count: int
    severity_counts: SeverityCountsDTO
    issues_truncated: bool
    improved_code: ImprovedCodeDTO
    analysis: AnalysisDTO
    capabilities: ResultCapabilitiesDTO
    warnings: list[WarningDTO]
    metadata: MetadataDTO


class FailureDTO(_Response):
    code: ErrorCode
    message: str


class ReviewResource(BaseModel):
    review_id: UUID
    status: ReviewStatus
    stage: ReviewStage
    language: Language
    created_at: datetime
    finished_at: datetime | None
    result: ReviewResultDTO | None
    failure: FailureDTO | None


class LanguageDTO(BaseModel):
    id: Language
    display_name: str
    monaco_language: str


class LimitsDTO(BaseModel):
    max_source_bytes: int
    max_source_lines: int


class Capabilities(BaseModel):
    languages: list[LanguageDTO]
    limits: LimitsDTO
    review_timeout_seconds: int
    improvement_enabled: bool


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class ComponentDTO(_Response):
    available: bool
    detail: str


class ComponentsDTO(BaseModel):
    ai_provider: ComponentDTO
    static_tools: ComponentDTO
    persistence: ComponentDTO


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    components: ComponentsDTO


class ErrorDetail(BaseModel):
    field: str
    problem: str


class ErrorInfo(BaseModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail] | None
    request_id: str


class ErrorBody(BaseModel):
    error: ErrorInfo
