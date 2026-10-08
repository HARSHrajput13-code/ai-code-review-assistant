"""Domain → DTO mappers (CIS §6.5). Fields are copied; nothing is recomputed, reordered or
interpreted. Only the §6.5 derived values (`severity_counts` keys, `capabilities`) are computed.
"""

from backend.api.dtos import (
    AnalysisDTO,
    CoverageDTO,
    FailureDTO,
    ImprovedCodeDTO,
    IssueDTO,
    MetadataDTO,
    ResultCapabilitiesDTO,
    ReviewResource,
    ReviewResultDTO,
    ScoreDTO,
    SeverityCountsDTO,
    SummaryDTO,
    WarningDTO,
)
from shared.domain.enums import ImprovedCodeStatus, OutcomeStatus
from shared.domain.models import ReviewResult
from shared.domain.review import CodeReview

PARSER = "python-parser"


def _static_usable(result: ReviewResult) -> bool:
    """§5.6 `usable`: the syntax is invalid, or a static tool other than the parser succeeded.

    An invalid syntax always yields the parser's CRITICAL, HIGH-confidence issue, which sorts
    first (§12.7), so it is always among the returned issues.
    """
    tool_succeeded = any(
        t.outcome.status is OutcomeStatus.SUCCEEDED
        for t in result.analysis.static_tools
        if t.tool != PARSER
    )
    syntax_invalid = any(s.origin == PARSER for i in result.issues for s in i.sources)
    return tool_succeeded or syntax_invalid


def review_result(result: ReviewResult) -> ReviewResultDTO:
    counts = result.severity_counts
    return ReviewResultDTO(
        summary=SummaryDTO.model_validate(result.summary),
        score=ScoreDTO.model_validate(result.score),
        coverage=CoverageDTO.model_validate(result.coverage),
        issues=[IssueDTO.model_validate(issue) for issue in result.issues],
        total_issue_count=result.total_issue_count,
        severity_counts=SeverityCountsDTO(
            CRITICAL=counts.critical, HIGH=counts.high, MEDIUM=counts.medium, LOW=counts.low
        ),
        issues_truncated=result.issues_truncated,
        improved_code=ImprovedCodeDTO.model_validate(result.improved_code),
        analysis=AnalysisDTO.model_validate(result.analysis),
        capabilities=ResultCapabilitiesDTO(
            static_analysis=_static_usable(result),
            ai_analysis=result.analysis.ai_analysis.status is OutcomeStatus.SUCCEEDED,
            improved_code=result.improved_code.status is ImprovedCodeStatus.AVAILABLE,
        ),
        warnings=[WarningDTO.model_validate(warning) for warning in result.warnings],
        metadata=MetadataDTO.model_validate(result.metadata),
    )


def review_resource(review: CodeReview) -> ReviewResource:
    return ReviewResource(
        review_id=review.review_id,
        status=review.status,
        stage=review.stage,
        language=review.language,
        created_at=review.created_at,
        finished_at=review.finished_at,
        result=review_result(review.result) if review.result is not None else None,
        failure=FailureDTO.model_validate(review.failure) if review.failure is not None else None,
    )
