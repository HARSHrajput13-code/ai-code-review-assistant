"""Review lifecycle log events (CIS §19.1, §7.2, §18 D-55).

Context variables carry the request and review IDs: a review's background task runs in a copy of
the submitting request's context, so its records keep that request's ID without shared state.
Records carry only whitelisted metadata (`backend.logging_setup`). An exception is described by
its type and stack frames, attached as fields rather than `exc_info`, so no handler can render
its message, which may contain input, or its locals.
"""

import logging
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from shared.domain.enums import OutcomeStatus, ReviewStatus, Severity
from shared.domain.models import StageOutcome
from shared.domain.review import CodeReview

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
review_id_var: ContextVar[str | None] = ContextVar("review_id", default=None)


@contextmanager
def bound(request_id: str | None, review_id: str | None) -> Iterator[None]:
    """Bind both IDs for records written outside the review's own task."""
    request, review = request_id_var.set(request_id), review_id_var.set(review_id)
    try:
        yield
    finally:
        review_id_var.reset(review)
        request_id_var.reset(request)


def diagnostics(error: BaseException) -> dict[str, object]:
    """The exception type and its frames (file:line in function); never message or locals."""
    return {
        "exception_type": type(error).__name__,
        "traceback": [
            f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(error.__traceback__)
        ],
    }


def stage_started(stage: str, /, **fields: object) -> None:
    extra = {"stage": stage, "status": ReviewStatus.RUNNING, "duration_ms": 0, **fields}
    logger.info("review.stage.started", extra=extra)


def stage_finished(
    stage: str,
    outcome: StageOutcome,
    duration_ms: int,
    error: BaseException | None = None,
    /,
    **fields: object,
) -> None:
    """A stage's outcome. A stage that never started logs only this record (§7.2)."""
    extra = {"stage": stage, "duration_ms": duration_ms, **_outcome(outcome), **fields}
    level = logging.WARNING if outcome.status is OutcomeStatus.FAILED else logging.INFO
    if error is not None:
        level, extra = logging.ERROR, extra | diagnostics(error)
    logger.log(level, "review.stage.finished", extra=extra)


def review_finished(
    review: CodeReview, refs_rejected: int, error: BaseException | None = None
) -> None:
    """The one record for a review's terminal state, from its stored result (§19.1)."""
    end = review.finished_at or review.created_at
    extra: dict[str, object] = {
        "status": review.status,
        "duration_ms": max(0, int((end - review.created_at).total_seconds() * 1000)),
        "refs_rejected": refs_rejected,
    }
    if review.failure is not None:
        extra["error_code"] = review.failure.code
    if review.result is not None:
        result, analysis = review.result, review.result.analysis
        extra |= {
            "score": result.score.overall,
            "assessed_weight": result.score.assessed_weight,
            "coverage": result.coverage.model_dump(mode="json"),
            "severity_counts": {
                s.value: getattr(result.severity_counts, s.value.lower()) for s in Severity
            },
            "outcomes": {
                "static_analysis": _outcome(analysis.static_analysis),
                **{t.tool: _outcome(t.outcome) for t in analysis.static_tools},
                "ai_analysis": _outcome(analysis.ai_analysis),
                "improvement": _outcome(analysis.improvement),
            },
        }
    level = logging.WARNING if review.status is ReviewStatus.FAILED else logging.INFO
    if error is not None:
        level, extra = logging.ERROR, extra | diagnostics(error)
    logger.log(level, "review.finished", extra=extra)


def _outcome(outcome: StageOutcome) -> dict[str, object]:
    """Status and codes only: the outcome's message is never logged."""
    found = {
        "status": outcome.status,
        "error_code": outcome.error_code,
        "skip_reason": outcome.skip_reason,
    }
    return {k: v for k, v in found.items() if v is not None}
