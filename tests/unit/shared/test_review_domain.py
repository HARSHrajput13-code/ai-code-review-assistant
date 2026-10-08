"""The review aggregate, its transitions, and the result models (CIS §5.4-§5.7, §17.2)."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.domain import errors
from shared.domain.enums import (
    Category,
    ErrorCode,
    ImprovedCodeStatus,
    Language,
    ReviewStage,
    ReviewStatus,
    ScoreBand,
)
from shared.domain.errors import InvalidStateTransition
from shared.domain.models import (
    CategoryScore,
    Coverage,
    ImprovedCode,
    ReviewFailure,
    Score,
)
from shared.domain.review import CodeReview, enter_improvement, fail, finish, new_review, start
from tests.unit.builders import CONF_HIGH, LOW, M, issue, loc

NOW = datetime(2026, 10, 8, tzinfo=UTC)
FAILURE = ReviewFailure(code=ErrorCode.REVIEW_TIMEOUT, message="Too slow.")


def pending() -> CodeReview:
    return new_review(uuid4(), Language.PYTHON, NOW)


def test_lifecycle_pending_running_improvement_failed() -> None:
    review = start(pending(), NOW)
    assert (review.status, review.stage, review.started_at) == (
        ReviewStatus.RUNNING, ReviewStage.ANALYZING, NOW,
    )  # fmt: skip
    review = enter_improvement(review)
    assert review.stage is ReviewStage.GENERATING_IMPROVEMENT
    done = fail(review, FAILURE, NOW + timedelta(seconds=1))
    assert (done.status, done.stage, done.failure) == (
        ReviewStatus.FAILED,
        ReviewStage.FINISHED,
        FAILURE,
    )


def test_pending_review_can_fail_from_the_queue() -> None:
    assert fail(pending(), FAILURE, NOW).status is ReviewStatus.FAILED


@pytest.mark.parametrize(
    "transition",
    [
        lambda r: enter_improvement(r),  # not running yet
        lambda r: finish(r, ReviewStatus.COMPLETED, None, NOW),  # type: ignore[arg-type]
        lambda r: start(start(r, NOW), NOW),  # already running
        lambda r: enter_improvement(enter_improvement(start(r, NOW))),
        lambda r: start(fail(r, FAILURE, NOW), NOW),  # terminal is immutable
        lambda r: fail(fail(r, FAILURE, NOW), FAILURE, NOW),
    ],
)
def test_invalid_transitions_raise(transition: Any) -> None:
    with pytest.raises(InvalidStateTransition):
        transition(pending())


def test_finish_only_produces_completed_or_partial() -> None:
    with pytest.raises(InvalidStateTransition):
        finish(start(pending(), NOW), ReviewStatus.FAILED, None, NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"status": ReviewStatus.RUNNING},  # QUEUED stage, no started_at
        {"result": None, "status": ReviewStatus.COMPLETED, "stage": ReviewStage.FINISHED},
        {"failure": FAILURE},
        {"created_at": datetime(2026, 10, 8)},  # naive timestamp
    ],
)
def test_aggregate_invariants(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CodeReview.model_validate({**dict(pending()), **changes})


def test_category_score_and_score_invariants() -> None:
    with pytest.raises(ValidationError):
        CategoryScore(category=Category.SECURITY, assessed=False, score=100, issue_count=0)
    with pytest.raises(ValidationError):
        CategoryScore(category=Category.SECURITY, assessed=False, score=None, issue_count=1)
    categories = tuple(
        CategoryScore(category=c, assessed=True, score=100, issue_count=0) for c in Category
    )
    base: dict[str, Any] = {"overall": 100, "provisional": False, "assessed_weight": 100,
            "caps_applied": (), "policy_version": "1.0"}  # fmt: skip
    Score(**base, band=ScoreBand.EXCELLENT, categories=categories)
    with pytest.raises(ValidationError):
        Score(**base, band=ScoreBand.GOOD, categories=categories)
    with pytest.raises(ValidationError):
        Score(**base, band=ScoreBand.EXCELLENT, categories=categories[::-1])


def test_coverage_orders_are_canonical() -> None:
    with pytest.raises(ValidationError):
        Coverage(complete=False, unassessed_categories=(Category.SECURITY, Category.CORRECTNESS),
                 missing_components=())  # fmt: skip
    with pytest.raises(ValidationError):
        Coverage(complete=False, unassessed_categories=(), missing_components=("bandit", "ai"))


def test_improved_code_invariants() -> None:
    ImprovedCode(status=ImprovedCodeStatus.AVAILABLE, code="x = 1")
    ImprovedCode(status=ImprovedCodeStatus.UNAVAILABLE, code=None, message="Disabled.")
    for fields in (
        {"status": ImprovedCodeStatus.AVAILABLE, "code": None},
        {"status": ImprovedCodeStatus.NOT_NEEDED, "code": "x"},
        {"status": ImprovedCodeStatus.UNAVAILABLE, "code": None},
        {"status": ImprovedCodeStatus.NOT_NEEDED, "code": None,
         "failure_code": ErrorCode.AI_OUTPUT_INVALID},
    ):  # fmt: skip
        with pytest.raises(ValidationError):
            ImprovedCode(**fields)


def test_issue_locations_must_be_sorted_and_counted() -> None:
    with pytest.raises(ValidationError):
        issue(M, LOW, CONF_HIGH, line=1, additional_locations=(loc(5), loc(3)), occurrence_count=3)
    with pytest.raises(ValidationError):
        issue(M, LOW, CONF_HIGH, line=1, additional_locations=(loc(3),), occurrence_count=1)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.EmptyCode(), ErrorCode.EMPTY_CODE),
        (errors.InputTooLarge(12000, 500), ErrorCode.INPUT_TOO_LARGE),
        (errors.UnsupportedLanguage(), ErrorCode.UNSUPPORTED_LANGUAGE),
        (errors.IdempotencyConflict(), ErrorCode.IDEMPOTENCY_CONFLICT),
        (errors.ServiceBusy(), ErrorCode.SERVICE_BUSY),
        (errors.ReviewNotFound(), ErrorCode.REVIEW_NOT_FOUND),
        (errors.AIProviderTimeout(), ErrorCode.REVIEW_TIMEOUT),
        (errors.AIResponseInvalid(retryable=True), ErrorCode.AI_OUTPUT_INVALID),
        (errors.InvalidStateTransition(), ErrorCode.INTERNAL_ERROR),
    ],
)
def test_error_codes(error: errors.ReviewError, code: ErrorCode) -> None:
    assert error.code is code and error.safe_message
