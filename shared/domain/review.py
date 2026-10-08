"""The `CodeReview` aggregate and its transitions (CIS §5.7).

PENDING ─► RUNNING (ANALYZING → GENERATING_IMPROVEMENT) ─► COMPLETED | PARTIAL | FAILED
PENDING ─► FAILED (queue deadline or shutdown)
Any other transition raises InvalidStateTransition. Terminal states are immutable.
"""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import model_validator

from shared.domain.enums import Language, ReviewStage, ReviewStatus
from shared.domain.errors import InvalidStateTransition
from shared.domain.models import DomainModel, ReviewFailure, ReviewResult

_STAGES = {
    ReviewStatus.PENDING: {ReviewStage.QUEUED},
    ReviewStatus.RUNNING: {ReviewStage.ANALYZING, ReviewStage.GENERATING_IMPROVEMENT},
    ReviewStatus.COMPLETED: {ReviewStage.FINISHED},
    ReviewStatus.PARTIAL: {ReviewStage.FINISHED},
    ReviewStatus.FAILED: {ReviewStage.FINISHED},
}


class CodeReview(DomainModel):
    review_id: UUID
    language: Language
    status: ReviewStatus
    stage: ReviewStage
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: ReviewResult | None = None
    failure: ReviewFailure | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.stage not in _STAGES[self.status]:
            raise ValueError(f"stage {self.stage} is invalid for {self.status}")
        if (self.result is not None) != (
            self.status in (ReviewStatus.COMPLETED, ReviewStatus.PARTIAL)
        ):
            raise ValueError("result is present if and only if COMPLETED or PARTIAL")
        if (self.failure is not None) != (self.status is ReviewStatus.FAILED):
            raise ValueError("failure is present if and only if FAILED")
        if (self.finished_at is not None) != self.status.terminal:
            raise ValueError("finished_at is set if and only if terminal")
        if self.status is ReviewStatus.RUNNING and self.started_at is None:
            raise ValueError("a running review has started_at")
        for moment in (self.created_at, self.started_at, self.finished_at):
            if moment is not None and moment.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware (UTC)")
        return self


def new_review(review_id: UUID, language: Language, created_at: datetime) -> CodeReview:
    return CodeReview(
        review_id=review_id,
        language=language,
        status=ReviewStatus.PENDING,
        stage=ReviewStage.QUEUED,
        created_at=created_at,
    )


def _with(review: CodeReview, **changes: object) -> CodeReview:
    """A validated copy (model_copy would skip validation)."""
    return CodeReview.model_validate({**dict(review), **changes})


def _require(review: CodeReview, *allowed: ReviewStatus) -> None:
    if review.status not in allowed:
        raise InvalidStateTransition(f"not allowed from {review.status}")


def start(review: CodeReview, now: datetime) -> CodeReview:
    _require(review, ReviewStatus.PENDING)
    return _with(review, status=ReviewStatus.RUNNING, stage=ReviewStage.ANALYZING, started_at=now)


def enter_improvement(review: CodeReview) -> CodeReview:
    _require(review, ReviewStatus.RUNNING)
    if review.stage is not ReviewStage.ANALYZING:
        raise InvalidStateTransition("improvement follows analysis")
    return _with(review, stage=ReviewStage.GENERATING_IMPROVEMENT)


def finish(
    review: CodeReview, status: ReviewStatus, result: ReviewResult, now: datetime
) -> CodeReview:
    _require(review, ReviewStatus.RUNNING)
    if status not in (ReviewStatus.COMPLETED, ReviewStatus.PARTIAL):
        raise InvalidStateTransition("finish produces COMPLETED or PARTIAL")
    return _with(review, status=status, stage=ReviewStage.FINISHED, result=result, finished_at=now)


def fail(review: CodeReview, failure: ReviewFailure, now: datetime) -> CodeReview:
    _require(review, ReviewStatus.PENDING, ReviewStatus.RUNNING)
    return _with(
        review,
        status=ReviewStatus.FAILED,
        stage=ReviewStage.FINISHED,
        failure=failure,
        finished_at=now,
    )
