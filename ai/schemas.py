"""AI output models: the ONLY AI schema definition (CIS §9.2, §11, D-42).

Every key is required; an optional value is `null`. Unknown keys are rejected
(`additionalProperties: false`). Text fields are whitespace-stripped and must stay non-empty,
except `improved_code`, which is validated verbatim. The `max_length` values are the schema
safety bounds (D-76); AI text is never truncated.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from shared.domain.enums import Category, Severity


def _text(max_length: int) -> object:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


Title = Annotated[str, _text(150)]
Summary = Annotated[str, _text(600)]
Impact = Annotated[str, _text(600)]
Recommendation = Annotated[str, _text(800)]
Evidence = Annotated[str, _text(300)]
Note = Annotated[str, _text(300)]
OverallSummary = Annotated[str, _text(1500)]
StaticId = Annotated[str, StringConstraints(pattern=r"^S[0-9]{1,4}$")]
LineNumber = Annotated[int, Field(ge=1)]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class AIIssue(_Output):
    severity: Severity
    category: Category
    title: Title
    line: LineNumber | None
    end_line: LineNumber | None
    evidence: Evidence | None
    summary: Summary
    impact: Impact
    recommendation: Recommendation
    related_static_ids: list[StaticId] = Field(max_length=10)


class AIReviewOutput(_Output):
    summary: OverallSummary
    issues: list[AIIssue] = Field(max_length=20)


class AIImprovementOutput(_Output):
    # Schema safety bound: 2 × the largest configurable MAX_SOURCE_BYTES, in characters (§11.2).
    improved_code: str = Field(min_length=1, max_length=200_000)
    notes: list[Note] = Field(max_length=10)
