"""FakeAIReviewProvider and the shared retry rules (CIS §9.4, §9.6, D-41, D-77)."""

import asyncio
from typing import Any

import pytest

from ai.fake import (
    CONTEXT_EXCEEDED,
    DEFAULT_SUMMARY,
    INVALID_JSON,
    INVALID_THEN_VALID,
    TIMEOUT,
    UNAVAILABLE,
    FakeAIReviewProvider,
    FakeResponse,
)
from shared.domain.enums import Category, ErrorCode, Language, LocationStatus, Severity
from shared.domain.errors import ReviewError
from shared.domain.interfaces import AIImprovementRequest, AIReviewRequest
from shared.domain.models import AIReviewResult, Location, SourceText
from tests.unit.analysis.fakes import FixedDeadline as Fixed

SOURCE = SourceText.of("\n\nimport os\nprint(os.name)\n")
REQUEST = AIReviewRequest(language=Language.PYTHON, source=SOURCE, static_findings=())


def review(provider: FakeAIReviewProvider, remaining: float = 300) -> AIReviewResult:
    return asyncio.run(provider.review(REQUEST, Fixed(remaining)))


def test_default_review_is_deterministic_and_validated() -> None:
    result = review(FakeAIReviewProvider())
    assert (result.summary, result.attempts, result.dropped_issue_count) == (DEFAULT_SUMMARY, 1, 0)
    (found,) = result.candidates
    assert (found.severity, found.category) == (Severity.LOW, Category.READABILITY)
    assert found.location == Location(start_line=3, end_line=3)  # the first non-blank line
    assert found.location_status is LocationStatus.SOURCE_MATCHED
    assert found.related_static_ids == ()
    assert review(FakeAIReviewProvider()) == result


def test_default_improvement() -> None:
    provider = FakeAIReviewProvider()
    request = AIImprovementRequest(language=Language.PYTHON, source=SOURCE, issues=())
    improved = asyncio.run(provider.improve(request, Fixed(300)))
    assert (improved.code, improved.notes, improved.attempts) == (
        "# Reviewed\n" + SOURCE.text,
        (),
        1,
    )


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (UNAVAILABLE, ErrorCode.AI_MODEL_UNAVAILABLE),
        (TIMEOUT, ErrorCode.REVIEW_TIMEOUT),
        (CONTEXT_EXCEEDED, ErrorCode.AI_CONTEXT_EXCEEDED),
        (FakeResponse(done_reason="length"), ErrorCode.AI_CONTEXT_EXCEEDED),
        (FakeResponse(done_reason="load"), ErrorCode.AI_OUTPUT_INVALID),
    ],
)
def test_non_retryable_failures_make_one_call(response: FakeResponse, code: ErrorCode) -> None:
    provider = FakeAIReviewProvider(review_script=[response, FakeResponse()])
    with pytest.raises(ReviewError) as raised:
        review(provider)
    assert raised.value.code is code
    assert len(provider.calls) == 1


def test_invalid_then_valid_retries_once_with_the_next_seed() -> None:
    provider = FakeAIReviewProvider(review_script=INVALID_THEN_VALID)
    assert review(provider).attempts == 2
    assert [(c.attempt, c.seed) for c in provider.calls] == [(1, 42), (2, 43)]


def test_invalid_twice_fails_after_two_calls() -> None:
    provider = FakeAIReviewProvider(review_script=[INVALID_JSON, INVALID_JSON, FakeResponse()])
    with pytest.raises(ReviewError) as raised:
        review(provider)
    assert raised.value.code is ErrorCode.AI_OUTPUT_INVALID and len(provider.calls) == 2


@pytest.mark.parametrize(("retry_count", "remaining"), [(0, 300), (1, 19.9)])
def test_no_retry_when_disabled_or_under_20_seconds(retry_count: int, remaining: float) -> None:
    provider = FakeAIReviewProvider(review_script=INVALID_THEN_VALID, retry_count=retry_count)
    with pytest.raises(ReviewError):
        review(provider, remaining)
    assert len(provider.calls) == 1


def test_attempt_timeout_is_bounded_by_the_deadline() -> None:
    provider = FakeAIReviewProvider()
    review(provider, remaining=30)
    assert provider.calls[0].timeout_s == 30
    review(provider, remaining=500)
    assert provider.calls[1].timeout_s == 180


def test_thinking_is_ignored_and_counted() -> None:
    provider = FakeAIReviewProvider(review_script=[FakeResponse(thinking="secret reasoning")])
    assert review(provider).summary == DEFAULT_SUMMARY
    assert provider.thinking_emitted == 1


def test_canned_content_and_call_recorder() -> None:
    content = '{"summary": "Canned.", "issues": []}'
    provider = FakeAIReviewProvider(review_script=[FakeResponse(content=content)])
    assert review(provider).summary == "Canned."
    (call,) = provider.calls
    assert (call.operation, call.request) == ("review", REQUEST)


def test_descriptor_and_health() -> None:
    provider = FakeAIReviewProvider()
    assert provider.descriptor.provider == "fake"
    health: Any = asyncio.run(provider.check_health(1))
    assert health.available
