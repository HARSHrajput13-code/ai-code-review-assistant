"""ReviewOrchestrator stage sequence, status and degradation (CIS §5.7, §7.2, §14.5, §20.3)."""

import asyncio
from typing import Any

import pytest

from ai.fake import (
    INVALID_JSON,
    INVALID_THEN_VALID,
    TIMEOUT,
    UNAVAILABLE,
    FakeAIReviewProvider,
    FakeResponse,
)
from backend.application.deadline import MonotonicDeadline
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator, ReviewOutcome
from backend.review.scoring.policy import ScoringPolicyV1
from shared.domain.enums import (
    ErrorCode,
    ImprovedCodeStatus,
    Language,
    OutcomeStatus,
    ReviewStage,
    ReviewStatus,
    SkipReason,
    SummarySource,
)
from shared.domain.interfaces import Deadline
from shared.domain.models import (
    ImprovedCode,
    ReviewResult,
    ReviewSubmission,
    SourceText,
    StageOutcome,
)
from tests.unit.backend.pipeline_doubles import (
    DISABLED,
    FAILED,
    SUBMISSION,
    FakeImprover,
    StubAdapter,
    candidate,
)
from tests.unit.builders import MEDIUM, FakeClock, ScriptedDeadline


def run(
    adapter: StubAdapter | None = None,
    provider: FakeAIReviewProvider | None = None,
    improver: FakeImprover | None = None,
    deadline: Deadline | None = None,
    submission: ReviewSubmission = SUBMISSION,
    **options: Any,
) -> tuple[ReviewOutcome, list[ReviewStage]]:
    clock = FakeClock()
    orchestrator = ReviewOrchestrator(
        provider or FakeAIReviewProvider(), clock, OrchestratorOptions(**options), improver
    )
    stages: list[ReviewStage] = []

    async def report(stage: ReviewStage) -> None:
        stages.append(stage)

    outcome = asyncio.run(
        orchestrator.run(
            submission, adapter or StubAdapter(), deadline or MonotonicDeadline(clock, 300), report
        )
    )
    return outcome, stages


def result(outcome: ReviewOutcome) -> ReviewResult:
    assert outcome.result is not None
    return outcome.result


def test_happy_path_completes_with_complete_coverage() -> None:
    outcome, stages = run(improver=FakeImprover())
    found = result(outcome)
    assert outcome.status is ReviewStatus.COMPLETED
    assert found.coverage.complete and not found.score.provisional
    assert found.summary.source is SummarySource.AI
    assert found.improved_code.status is ImprovedCodeStatus.AVAILABLE
    assert found.analysis.static_analysis.status is OutcomeStatus.SUCCEEDED
    assert (found.metadata.ai_provider, found.metadata.scoring_policy_version) == ("fake", "1.0")
    assert stages == [ReviewStage.GENERATING_IMPROVEMENT]


def test_without_an_improver_improvement_is_a_non_failure_disabled_skip() -> None:
    outcome, _ = run()
    found = result(outcome)
    assert outcome.status is ReviewStatus.COMPLETED
    assert found.analysis.improvement.skip_reason is SkipReason.DISABLED
    assert found.improved_code.status is ImprovedCodeStatus.UNAVAILABLE


def test_disabled_tool_completes_with_reduced_coverage() -> None:
    outcome, _ = run(StubAdapter(pylint=DISABLED), improver=FakeImprover())
    found = result(outcome)
    assert outcome.status is ReviewStatus.COMPLETED
    assert not found.coverage.complete and found.score.provisional
    assert found.coverage.missing_components == ("pylint",)
    assert [w.code for w in found.warnings] == ["REDUCED_COVERAGE"]


def test_ai_unavailable_gives_partial_static_only_review() -> None:
    improver = FakeImprover()
    outcome, _ = run(provider=FakeAIReviewProvider(review_script=[UNAVAILABLE]), improver=improver)
    found = result(outcome)
    assert outcome.status is ReviewStatus.PARTIAL
    assert {i.provenance.value for i in found.issues} == {"STATIC"}
    assert found.summary.source is SummarySource.GENERATED
    assert found.analysis.ai_analysis.error_code is ErrorCode.AI_MODEL_UNAVAILABLE
    improvement = found.analysis.improvement
    assert (improvement.skip_reason, improvement.error_code) == (
        SkipReason.DEPENDENCY_FAILED, ErrorCode.AI_MODEL_UNAVAILABLE,
    )  # fmt: skip
    assert improver.calls == []


def test_static_unusable_and_ai_failing_fails_the_review() -> None:
    outcome, _ = run(
        StubAdapter(pylint=FAILED, bandit=FAILED), FakeAIReviewProvider(review_script=[TIMEOUT])
    )
    assert outcome.status is ReviewStatus.FAILED
    assert outcome.failure is not None and outcome.failure.code is ErrorCode.REVIEW_TIMEOUT


def test_syntax_error_still_calls_ai_and_caps_the_score() -> None:
    provider = FakeAIReviewProvider()
    broken = ReviewSubmission(language=Language.PYTHON, source=SourceText.of("def f(:\n    pass\n"))
    outcome, _ = run(provider=provider, submission=broken)
    found = result(outcome)
    assert len(provider.calls) == 1
    assert found.score.overall <= 20
    assert found.analysis.static_tools[0].outcome.skip_reason is SkipReason.SYNTAX_ERROR


def test_invalid_ai_output_still_attempts_improvement_with_static_issues() -> None:
    improver = FakeImprover()
    provider = FakeAIReviewProvider(review_script=[INVALID_JSON, INVALID_JSON])
    outcome, _ = run(provider=provider, improver=improver)
    assert outcome.status is ReviewStatus.PARTIAL
    assert result(outcome).analysis.ai_analysis.error_code is ErrorCode.AI_OUTPUT_INVALID
    (issues,) = improver.calls
    assert {i.provenance.value for i in issues} == {"STATIC"}


def test_invalid_improvement_is_partial_and_leaves_the_score_unchanged() -> None:
    failed = (
        StageOutcome(status=OutcomeStatus.FAILED, error_code=ErrorCode.IMPROVED_CODE_INVALID),
        ImprovedCode(
            status=ImprovedCodeStatus.UNAVAILABLE,
            code=None,
            failure_code=ErrorCode.IMPROVED_CODE_INVALID,
            message="Improved code could not be validated.",
        ),
    )
    baseline, _ = run(improver=FakeImprover())
    outcome, _ = run(improver=FakeImprover(result=failed))
    assert outcome.status is ReviewStatus.PARTIAL
    assert result(outcome).score == result(baseline).score


def test_attempt_timeout_is_bounded_by_the_remaining_deadline() -> None:
    provider = FakeAIReviewProvider()
    clock = FakeClock()
    run(provider=provider, deadline=MonotonicDeadline(clock, 30))
    assert provider.calls[0].timeout_s == 30


def test_deadline_exhaustion_during_the_ai_stage_is_partial_review_timeout() -> None:
    slow = FakeAIReviewProvider(review_script=[FakeResponse(delay_s=5)])
    # remaining(): static bound, AI start threshold, then the AI operation's bound.
    outcome, _ = run(provider=slow, deadline=ScriptedDeadline([300, 300, 0.05]))
    assert outcome.status is ReviewStatus.PARTIAL
    assert result(outcome).analysis.ai_analysis.error_code is ErrorCode.REVIEW_TIMEOUT


def test_ai_is_skipped_with_less_than_5_seconds_left() -> None:
    provider = FakeAIReviewProvider()
    outcome, _ = run(provider=provider, deadline=ScriptedDeadline([4.9]))
    ai = result(outcome).analysis.ai_analysis
    assert (ai.skip_reason, ai.error_code) == (
        SkipReason.DEADLINE_EXCEEDED,
        ErrorCode.REVIEW_TIMEOUT,
    )
    assert provider.calls == [] and outcome.status is ReviewStatus.PARTIAL


def test_improvement_is_skipped_with_less_than_15_seconds_left() -> None:
    improver = FakeImprover()
    outcome, _ = run(improver=improver, deadline=ScriptedDeadline([14]))
    improvement = result(outcome).analysis.improvement
    assert improvement.skip_reason is SkipReason.DEADLINE_EXCEEDED and improver.calls == []
    assert outcome.status is ReviewStatus.PARTIAL


def test_no_issues_with_ai_success_needs_no_improvement() -> None:
    clean = FakeAIReviewProvider(
        review_script=[FakeResponse(content='{"summary": "Fine.", "issues": []}')]
    )
    outcome, _ = run(StubAdapter(candidates=()), clean, FakeImprover())
    assert result(outcome).improved_code.status is ImprovedCodeStatus.NOT_NEEDED
    assert outcome.status is ReviewStatus.COMPLETED


def test_unexpected_exceptions_are_mapped_at_the_stage_boundary() -> None:
    static_crash, _ = run(StubAdapter(error=RuntimeError("boom")))
    tools = result(static_crash).analysis.static_tools
    assert {t.outcome.error_code for t in tools} == {ErrorCode.STATIC_ANALYSIS_FAILURE}

    class Exploding(FakeAIReviewProvider):
        async def review(self, request: Any, deadline: Any) -> Any:
            raise RuntimeError("boom")

    ai_crash, _ = run(provider=Exploding())
    assert result(ai_crash).analysis.ai_analysis.error_code is ErrorCode.AI_MODEL_UNAVAILABLE

    class BrokenPolicy(ScoringPolicyV1):
        def score(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("boom")

    clock = FakeClock()

    async def report(stage: ReviewStage) -> None:
        pass

    broken = asyncio.run(
        ReviewOrchestrator(
            FakeAIReviewProvider(), clock, OrchestratorOptions(), policy=BrokenPolicy()
        ).run(SUBMISSION, StubAdapter(), MonotonicDeadline(clock, 300), report)
    )
    assert broken.status is ReviewStatus.FAILED
    assert broken.failure is not None and broken.failure.code is ErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    ("review_script", "improve_script"),
    [
        (INVALID_THEN_VALID, INVALID_THEN_VALID),
        ([INVALID_JSON, INVALID_JSON], [INVALID_JSON, INVALID_JSON]),
        ([UNAVAILABLE], INVALID_THEN_VALID),
        ([], []),
    ],
)
def test_a_review_never_exceeds_four_provider_calls(
    review_script: Any, improve_script: Any
) -> None:
    provider = FakeAIReviewProvider(review_script=review_script, improve_script=improve_script)
    run(provider=provider, improver=FakeImprover(provider=provider))
    assert len(provider.calls) <= 4
    if review_script is INVALID_THEN_VALID:
        assert len(provider.calls) == 4


def test_truncation_and_dropped_issue_warnings() -> None:
    lines = "\n".join(f"x{n} = {n}" for n in range(1, 61))
    many = tuple(candidate(n, "pylint:W0612", line=n, severity=MEDIUM) for n in range(1, 61))
    dropped = FakeResponse(
        content='{"summary": "S.", "issues": [{"severity": "LOW", "category": "READABILITY", '
        '"title": "\\u0001", "line": null, "end_line": null, "evidence": null, "summary": "s", '
        '"impact": "i", "recommendation": "r", "related_static_ids": []}]}'
    )
    submission = ReviewSubmission(language=Language.PYTHON, source=SourceText.of(lines))
    outcome, _ = run(
        StubAdapter(candidates=many),
        FakeAIReviewProvider(review_script=[dropped]),
        submission=submission,
    )
    found = result(outcome)
    assert (len(found.issues), found.total_issue_count, found.issues_truncated) == (50, 60, True)
    assert [w.code for w in found.warnings] == ["ISSUES_TRUNCATED", "AI_ISSUES_DROPPED"]
    assert found.severity_counts.medium == 60


def test_the_same_inputs_give_an_identical_result() -> None:
    assert run(improver=FakeImprover())[0] == run(improver=FakeImprover())[0]
