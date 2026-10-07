"""Immutable domain models used by static analysis (CIS §5.0, §5.2, §5.3, §5.6).

The remaining §5 models (issues, score, coverage, results, the review aggregate) are PR-03.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.domain.enums import (
    Category,
    Confidence,
    ErrorCode,
    LocationStatus,
    OutcomeStatus,
    Provenance,
    Severity,
    SkipReason,
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
