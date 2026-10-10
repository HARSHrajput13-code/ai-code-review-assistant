"""Immutable domain models (CIS §5.0-§5.6). The review aggregate is in `shared.domain.review`."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.domain.enums import (
    Category,
    Confidence,
    ErrorCode,
    ImprovedCodeStatus,
    Language,
    LocationStatus,
    OutcomeStatus,
    Provenance,
    ReviewStatus,
    ScoreBand,
    ScoreCap,
    Severity,
    SeveritySource,
    SkipReason,
    SummarySource,
)

# One limit per text field (CIS §5.3, D-42).
TEXT_LIMITS = {"title": 150, "summary": 600, "impact": 600, "recommendation": 800}


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceText(DomainModel):
    text: str  # normalized (§7.2)
    byte_size: int = Field(ge=1)  # UTF-8 length of the submitted text
    line_count: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.text.strip():
            raise ValueError("source text is blank")
        if "\x00" in self.text:
            raise ValueError("source text contains NUL")
        if self.line_count != len(self.text.split("\n")):
            raise ValueError("line_count does not match the text")
        return self

    @classmethod
    def of(cls, text: str) -> Self:
        """Build from already-normalized text, measuring it."""
        return cls(text=text, byte_size=len(text.encode("utf-8")), line_count=len(text.split("\n")))

    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.split("\n"))

    def line(self, n: int) -> str:
        """The 1-based line `n`."""
        if not 1 <= n <= self.line_count:
            raise IndexError(n)
        return self.lines()[n - 1]


class Location(DomainModel):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line precedes start_line")
        return self


class ToolVersion(DomainModel):
    tool: str
    version: str


class FindingCandidate(DomainModel):
    """A finding without an ID (D-40).

    Texts must be non-empty. The §5.3 length limits are enforced on `Finding`: static texts are
    truncated to them during normalization (§12.2), so a candidate may still exceed them.
    """

    provenance: Provenance
    origin: str = Field(pattern=r"^[a-z0-9-]{1,32}$")
    rule_key: str | None
    severity: Severity
    category: Category
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    location: Location | None
    location_status: LocationStatus
    tool_confidence: Confidence | None
    related_static_ids: tuple[str, ...] = ()
    ai_output_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        static = self.provenance is Provenance.STATIC
        if self.provenance is Provenance.HYBRID:
            raise ValueError("a candidate is STATIC or AI")
        if (self.location is not None) != (self.location_status is LocationStatus.SOURCE_MATCHED):
            raise ValueError("location is present if and only if SOURCE_MATCHED")
        if static:
            if self.rule_key is None or self.tool_confidence is None:
                raise ValueError("a static candidate needs rule_key and tool_confidence")
            if self.location_status is LocationStatus.UNMATCHED:
                raise ValueError("UNMATCHED applies to AI candidates only")
            if self.related_static_ids or self.ai_output_index is not None:
                raise ValueError("related_static_ids and ai_output_index are AI-only")
        elif self.rule_key is not None or self.tool_confidence is not None:
            raise ValueError("an AI candidate has no rule_key or tool_confidence")
        elif self.ai_output_index is None:
            raise ValueError("an AI candidate needs ai_output_index")
        return self


class Finding(FindingCandidate):
    """A numbered candidate: `S<n>` (static) or `A<n>` (AI), assigned by backend/review (§12.1)."""

    finding_id: str = Field(pattern=r"^[SA][1-9][0-9]*$")

    @model_validator(mode="after")
    def _check_finding(self) -> Self:
        if (self.finding_id[0] == "S") != (self.provenance is Provenance.STATIC):
            raise ValueError("S IDs are static, A IDs are AI")
        for field, limit in TEXT_LIMITS.items():
            if len(getattr(self, field)) > limit:
                raise ValueError(f"{field} exceeds {limit} characters")
        return self


class StageOutcome(DomainModel):
    status: OutcomeStatus
    error_code: ErrorCode | None = None
    skip_reason: SkipReason | None = None
    message: str | None = Field(default=None, max_length=200)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        match self.status:
            case OutcomeStatus.SUCCEEDED:
                ok = self.error_code is None and self.skip_reason is None
            case OutcomeStatus.FAILED:
                ok = self.error_code is not None and self.skip_reason is None
            case OutcomeStatus.SKIPPED:
                ok = self.skip_reason is not None and (
                    (self.error_code is not None) == self.skip_reason.failure_related
                )
        if not ok:
            raise ValueError(f"inconsistent {self.status} outcome")
        return self


class ToolOutcome(DomainModel):
    tool: str
    tool_version: str | None
    outcome: StageOutcome


class CodeValidation(DomainModel):
    """Result of `LanguageAdapter.validate_generated_code` (§5.8, §14.3 steps 5-6)."""

    valid: bool
    reason: str | None  # internal only (DOES_NOT_PARSE, INTERFACE_CHANGED); never shown

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.valid != (self.reason is None):
            raise ValueError("an invalid result has a reason; a valid one has none")
        return self


class SyntaxCheck(DomainModel):
    valid: bool
    candidate: FindingCandidate | None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.valid != (self.candidate is None):
            raise ValueError("an invalid syntax check carries exactly one candidate")
        return self


class StaticAnalysisResult(DomainModel):
    syntax_valid: bool
    tools: tuple[ToolOutcome, ...]
    candidates: tuple[FindingCandidate, ...]
    diagnostics: tuple[str, ...]  # internal only; never logged with source

    @property
    def usable(self) -> bool:
        return (not self.syntax_valid) or any(
            t.outcome.status is OutcomeStatus.SUCCEEDED
            for t in self.tools
            if t.tool != "python-parser"
        )


class ReviewSubmission(DomainModel):
    language: Language
    source: SourceText


class SeverityCounts(DomainModel):
    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)


class IssueSource(DomainModel):
    finding_id: str
    origin: str
    rule_key: str | None


MAX_ADDITIONAL_LOCATIONS = 20


class Issue(DomainModel):
    """A deduplicated, user-facing problem (§5.4)."""

    issue_id: str = Field(pattern=r"^ISS-[0-9]{3,}$")
    severity: Severity
    severity_source: SeveritySource
    category: Category
    title: str = Field(min_length=1, max_length=TEXT_LIMITS["title"])
    summary: str = Field(min_length=1, max_length=TEXT_LIMITS["summary"])
    impact: str = Field(min_length=1, max_length=TEXT_LIMITS["impact"])
    recommendation: str = Field(min_length=1, max_length=TEXT_LIMITS["recommendation"])
    location: Location | None
    additional_locations: tuple[Location, ...] = Field(max_length=MAX_ADDITIONAL_LOCATIONS)
    occurrence_count: int = Field(ge=1)
    provenance: Provenance
    confidence: Confidence
    sources: tuple[IssueSource, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        keys = [(loc.start_line, loc.end_line) for loc in self.additional_locations]
        if keys != sorted(keys):
            raise ValueError("additional_locations must be sorted")
        if self.location is not None and self.occurrence_count < 1 + len(keys):
            raise ValueError("occurrence_count is below the number of locations")
        return self


class CategoryScore(DomainModel):
    category: Category
    assessed: bool
    score: int | None = Field(ge=0, le=100)
    issue_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.assessed != (self.score is not None):
            raise ValueError("an assessed category has a score; an unassessed one has none")
        if not self.assessed and self.issue_count:
            raise ValueError("an unassessed category has no issues")
        return self


def band_for(overall: int) -> ScoreBand:
    """§13.6."""
    if overall >= 90:
        return ScoreBand.EXCELLENT
    if overall >= 75:
        return ScoreBand.GOOD
    if overall >= 50:
        return ScoreBand.FAIR
    if overall >= 25:
        return ScoreBand.POOR
    return ScoreBand.VERY_POOR


class Score(DomainModel):
    overall: int = Field(ge=0, le=100)
    band: ScoreBand
    provisional: bool
    assessed_weight: int = Field(ge=0, le=100)
    categories: tuple[CategoryScore, ...]
    caps_applied: tuple[ScoreCap, ...]
    policy_version: str

    @model_validator(mode="after")
    def _check(self) -> Self:
        if tuple(c.category for c in self.categories) != tuple(Category):
            raise ValueError("exactly the six categories, in canonical order")
        if self.band is not band_for(self.overall):
            raise ValueError("band does not match overall")
        return self


class Coverage(DomainModel):
    complete: bool
    unassessed_categories: tuple[Category, ...]
    missing_components: tuple[str, ...]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.unassessed_categories != tuple(
            c for c in Category if c in self.unassessed_categories
        ):
            raise ValueError("unassessed_categories must be in canonical order")
        order = ("ai", "pylint", "bandit")
        if self.missing_components != tuple(c for c in order if c in self.missing_components):
            raise ValueError("missing_components must be from ai, pylint, bandit, in that order")
        return self


class AIReviewResult(DomainModel):
    summary: str = Field(min_length=1, max_length=1500)
    candidates: tuple[FindingCandidate, ...]
    dropped_issue_count: int = Field(ge=0)
    attempts: int = Field(ge=1, le=2)
    static_findings_omitted: int = Field(default=0, ge=0)  # beyond the prompt cap (§10.2)


class ImprovedCode(DomainModel):
    status: ImprovedCodeStatus
    code: str | None
    notes: tuple[str, ...] = Field(default=(), max_length=10)
    failure_code: ErrorCode | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        available = self.status is ImprovedCodeStatus.AVAILABLE
        if available != (self.code is not None):
            raise ValueError("code is present if and only if AVAILABLE")
        if self.status is ImprovedCodeStatus.UNAVAILABLE and not self.message:
            raise ValueError("UNAVAILABLE requires a message")
        if self.failure_code is not None and self.status is not ImprovedCodeStatus.UNAVAILABLE:
            raise ValueError("failure_code is only for UNAVAILABLE")
        return self


class AnalysisReport(DomainModel):
    static_analysis: StageOutcome
    static_tools: tuple[ToolOutcome, ...]
    ai_analysis: StageOutcome
    improvement: StageOutcome


class ReviewSummary(DomainModel):
    text: str = Field(min_length=1, max_length=1500)
    source: SummarySource


class ReviewMetadata(DomainModel):
    ai_provider: str | None
    ai_model: str | None
    prompt_version: str | None
    scoring_policy_version: str
    analyzer_versions: tuple[ToolVersion, ...]
    source_bytes: int = Field(ge=0)
    source_lines: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)


class ReviewWarning(DomainModel):
    code: str
    message: str


MAX_ISSUES_RETURNED = 50  # policy (§5.6)


class ReviewResult(DomainModel):
    summary: ReviewSummary
    score: Score
    coverage: Coverage
    issues: tuple[Issue, ...] = Field(max_length=MAX_ISSUES_RETURNED)
    total_issue_count: int = Field(ge=0)
    severity_counts: SeverityCounts
    issues_truncated: bool
    improved_code: ImprovedCode
    analysis: AnalysisReport
    warnings: tuple[ReviewWarning, ...]
    metadata: ReviewMetadata

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.issues_truncated != (self.total_issue_count > len(self.issues)):
            raise ValueError("issues_truncated does not match the counts")
        return self


class ReviewFailure(DomainModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=200)


class ReviewRecord(DomainModel):
    """The metadata-only persistence record (§19.2): no source, hash, key, issue text or code."""

    review_id: UUID
    created_at: datetime
    finished_at: datetime | None
    language: Language
    status: ReviewStatus
    error_code: ErrorCode | None
    overall_score: int | None = Field(ge=0, le=100)
    score_provisional: bool
    assessed_weight: int | None = Field(ge=0, le=100)
    coverage_complete: bool
    issue_count: int = Field(ge=0)
    critical_count: int = Field(ge=0)
    high_count: int = Field(ge=0)
    medium_count: int = Field(ge=0)
    low_count: int = Field(ge=0)
    static_status: OutcomeStatus
    ai_status: OutcomeStatus
    improvement_status: OutcomeStatus
    ai_provider: str | None
    ai_model: str | None
    prompt_version: str | None
    scoring_policy_version: str
    source_bytes: int = Field(ge=0)
    source_lines: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
