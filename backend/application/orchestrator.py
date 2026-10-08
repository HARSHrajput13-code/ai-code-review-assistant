"""ReviewOrchestrator: runs the stages for one submission (CIS §7.2, §5.7, §14.5).

It depends only on interfaces (§3): the language adapter, the AI provider and, for the
improvement operation, an `Improver`. The improvement operation and its §14.3 validation are
PR-07; until an `Improver` is supplied, improvement reports as disabled (a non-failure skip) and
no unvalidated code is ever returned. The orchestrator never touches the job store.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from backend.application.deadline import Clock
from backend.review.coverage import coverage
from backend.review.issues import build_issues
from backend.review.matching import corroborate, dedupe_ai
from backend.review.numbering import number_ai, number_static
from backend.review.scoring.policy import ScoringPolicyV1
from backend.review.summary import generated_summary
from shared.domain.enums import (
    ErrorCode,
    ImprovedCodeStatus,
    OutcomeStatus,
    ReviewStage,
    ReviewStatus,
    Severity,
    SkipReason,
    SummarySource,
)
from shared.domain.errors import ReviewError
from shared.domain.interfaces import AIReviewProvider, AIReviewRequest, Deadline, LanguageAdapter
from shared.domain.models import (
    MAX_ISSUES_RETURNED,
    AIReviewResult,
    AnalysisReport,
    Finding,
    FindingCandidate,
    ImprovedCode,
    Issue,
    ReviewFailure,
    ReviewMetadata,
    ReviewResult,
    ReviewSubmission,
    ReviewSummary,
    ReviewWarning,
    SeverityCounts,
    SourceText,
    StageOutcome,
    StaticAnalysisResult,
    ToolOutcome,
)

logger = logging.getLogger(__name__)

PROMPT_MAX_IMPROVEMENT_ISSUES = 20  # §9.7
DEPENDENCY_CODES = (
    ErrorCode.AI_MODEL_UNAVAILABLE,
    ErrorCode.AI_CONTEXT_EXCEEDED,
    ErrorCode.REVIEW_TIMEOUT,
)
FAILURE_MESSAGES = {
    ErrorCode.STATIC_ANALYSIS_FAILURE: "Static analysis could not be completed.",
    ErrorCode.AI_MODEL_UNAVAILABLE: "AI analysis is unavailable.",
    ErrorCode.AI_OUTPUT_INVALID: "The AI response could not be validated.",
    ErrorCode.AI_CONTEXT_EXCEEDED: "The code is too large for the AI model's context window.",
    ErrorCode.REVIEW_TIMEOUT: "The review took too long and was stopped.",
    ErrorCode.IMPROVED_CODE_INVALID: "Improved code could not be validated.",
    ErrorCode.INTERNAL_ERROR: "An unexpected error occurred.",
}
IMPROVEMENT_DISABLED = "Improved-code generation is disabled."
IMPROVEMENT_NOT_NEEDED = "No changes were needed for the detected issues."


@dataclass(frozen=True)
class OrchestratorOptions:
    improvement_enabled: bool = True
    ai_start_min_s: float = 5.0  # start thresholds (§7.1, policy)
    improvement_start_min_s: float = 15.0


class Improver(Protocol):
    """The improvement operation (§14), implemented in PR-07."""

    async def improve(
        self, submission: ReviewSubmission, issues: tuple[Issue, ...], deadline: Deadline
    ) -> tuple[StageOutcome, ImprovedCode]: ...


type ProgressReporter = Callable[[ReviewStage], Awaitable[None]]


@dataclass(frozen=True)
class ReviewOutcome:
    status: ReviewStatus
    result: ReviewResult | None = None
    failure: ReviewFailure | None = None
    refs_rejected: int = 0


def _failed(code: ErrorCode, message: str | None = None, duration_ms: int = 0) -> StageOutcome:
    return StageOutcome(
        status=OutcomeStatus.FAILED,
        error_code=code,
        message=message or FAILURE_MESSAGES[code],
        duration_ms=duration_ms,
    )


def _skipped(reason: SkipReason, code: ErrorCode | None = None) -> StageOutcome:
    return StageOutcome(status=OutcomeStatus.SKIPPED, skip_reason=reason, error_code=code)


def _failure_related(outcome: StageOutcome) -> bool:
    if outcome.status is OutcomeStatus.FAILED:
        return True
    return outcome.status is OutcomeStatus.SKIPPED and bool(
        outcome.skip_reason and outcome.skip_reason.failure_related
    )


def _unavailable(code: ErrorCode | None, message: str) -> ImprovedCode:
    return ImprovedCode(
        status=ImprovedCodeStatus.UNAVAILABLE, code=None, failure_code=code, message=message
    )


def _static_failure(
    syntax_candidate: FindingCandidate | None, code: ErrorCode
) -> StaticAnalysisResult:
    tools = tuple(
        ToolOutcome(tool=tool, tool_version=None, outcome=_failed(code))
        for tool in ("pylint", "bandit")
    )
    return StaticAnalysisResult(
        syntax_valid=syntax_candidate is None,
        tools=tools,
        candidates=(syntax_candidate,) if syntax_candidate else (),
        diagnostics=(),
    )


def _bounded(deadline: Deadline) -> float | None:
    remaining = deadline.remaining()
    return remaining if remaining > 0 else None


class ReviewOrchestrator:
    def __init__(
        self,
        provider: AIReviewProvider,
        clock: Clock,
        options: OrchestratorOptions,
        improver: Improver | None = None,
        policy: ScoringPolicyV1 | None = None,
    ) -> None:
        self._provider = provider
        self._clock = clock
        self._options = options
        self._improver = improver
        self._policy = policy or ScoringPolicyV1()

    async def run(
        self,
        submission: ReviewSubmission,
        adapter: LanguageAdapter,
        deadline: Deadline,
        report: ProgressReporter,
    ) -> ReviewOutcome:
        started_at, started = self._clock.now(), self._clock.monotonic()
        source = submission.source

        static = await self._static(adapter, source, deadline)  # stages 3-4
        try:
            static_findings = number_static(static.candidates, source.line_count)  # stage 4a
        except Exception as error:
            return self._internal_error("STATIC_NUMBERING", error)

        ai_outcome, ai_result = await self._ai(submission, static_findings, deadline)  # stage 5

        try:
            covered, assessed = coverage(ai_outcome, static, adapter.covered_categories)
            if not assessed:  # checkpoint: nothing can be assessed
                code = ai_outcome.error_code or ErrorCode.REVIEW_TIMEOUT
                return ReviewOutcome(
                    ReviewStatus.FAILED,
                    failure=ReviewFailure(code=code, message=FAILURE_MESSAGES[code]),
                )
            ai_findings = dedupe_ai(
                number_ai(ai_result.candidates, source.line_count) if ai_result else ()
            )  # stage 6
            corroboration = corroborate(ai_findings, static_findings)  # stage 7
            issues = build_issues(static_findings, ai_findings, corroboration)
            score = self._policy.score(  # stage 8
                issues,
                assessed,
                syntax_valid=static.syntax_valid,
                coverage_complete=covered.complete,
            )
            summary = (  # stage 9
                ReviewSummary(text=ai_result.summary, source=SummarySource.AI)
                if ai_result
                else ReviewSummary(
                    text=generated_summary(
                        syntax_valid=static.syntax_valid,
                        issues=issues,
                        unassessed=covered.unassessed_categories,
                    ),
                    source=SummarySource.GENERATED,
                )
            )
        except Exception as error:
            return self._internal_error("SCORING", error)

        await report(ReviewStage.GENERATING_IMPROVEMENT)
        improvement, improved = await self._improve(submission, issues, ai_outcome, deadline)

        outcomes = [t.outcome for t in static.tools] + [ai_outcome, improvement]
        status = (
            ReviewStatus.PARTIAL if any(map(_failure_related, outcomes)) else ReviewStatus.COMPLETED
        )
        descriptor = self._provider.descriptor
        result = ReviewResult(
            summary=summary,
            score=score,
            coverage=covered,
            issues=issues[:MAX_ISSUES_RETURNED],
            total_issue_count=len(issues),
            severity_counts=SeverityCounts(
                **{s.value.lower(): sum(1 for i in issues if i.severity is s) for s in Severity}
            ),
            issues_truncated=len(issues) > MAX_ISSUES_RETURNED,
            improved_code=improved,
            analysis=AnalysisReport(
                static_analysis=self._aggregate(static),
                static_tools=static.tools,
                ai_analysis=ai_outcome,
                improvement=improvement,
            ),
            warnings=self._warnings(issues, static, ai_result),
            metadata=ReviewMetadata(
                ai_provider=descriptor.provider,
                ai_model=descriptor.model,
                prompt_version=descriptor.prompt_version,
                scoring_policy_version=self._policy.version,
                analyzer_versions=adapter.tool_versions(),
                source_bytes=source.byte_size,
                source_lines=source.line_count,
                started_at=started_at,
                finished_at=self._clock.now(),
                duration_ms=max(0, int((self._clock.monotonic() - started) * 1000)),
            ),
        )
        return ReviewOutcome(status, result=result, refs_rejected=corroboration.refs_rejected)

    async def _static(
        self, adapter: LanguageAdapter, source: SourceText, deadline: Deadline
    ) -> StaticAnalysisResult:
        syntax_candidate = None
        try:
            syntax = adapter.check_syntax(source)
            syntax_candidate = syntax.candidate
            async with asyncio.timeout(_bounded(deadline)):
                return await adapter.analyze(source, syntax, deadline)
        except TimeoutError:
            return _static_failure(syntax_candidate, ErrorCode.REVIEW_TIMEOUT)
        except Exception as error:  # isolated at the stage boundary (§7.2)
            logger.error("static analysis stage failed: %s", type(error).__name__)
            return _static_failure(syntax_candidate, ErrorCode.STATIC_ANALYSIS_FAILURE)

    async def _ai(
        self, submission: ReviewSubmission, findings: tuple[Finding, ...], deadline: Deadline
    ) -> tuple[StageOutcome, AIReviewResult | None]:
        if deadline.remaining() < self._options.ai_start_min_s:
            return _skipped(SkipReason.DEADLINE_EXCEEDED, ErrorCode.REVIEW_TIMEOUT), None
        started = self._clock.monotonic()
        request = AIReviewRequest(
            language=submission.language, source=submission.source, static_findings=findings
        )
        try:
            async with asyncio.timeout(_bounded(deadline)):
                result = await self._provider.review(request, deadline)
        except ReviewError as error:
            return _failed(error.code), None
        except TimeoutError:
            return _failed(ErrorCode.REVIEW_TIMEOUT), None
        except Exception as error:
            logger.error("AI stage failed: %s", type(error).__name__)
            return _failed(ErrorCode.AI_MODEL_UNAVAILABLE), None
        elapsed = max(0, int((self._clock.monotonic() - started) * 1000))
        return StageOutcome(status=OutcomeStatus.SUCCEEDED, duration_ms=elapsed), result

    async def _improve(
        self,
        submission: ReviewSubmission,
        issues: tuple[Issue, ...],
        ai: StageOutcome,
        deadline: Deadline,
    ) -> tuple[StageOutcome, ImprovedCode]:
        """The §14.5 decision table, top to bottom."""
        if not self._options.improvement_enabled or self._improver is None:
            return _skipped(SkipReason.DISABLED), _unavailable(None, IMPROVEMENT_DISABLED)
        ai_ok = ai.status is OutcomeStatus.SUCCEEDED
        if not ai_ok and ai.error_code in DEPENDENCY_CODES:
            code = ai.error_code
            return _skipped(SkipReason.DEPENDENCY_FAILED, code), _unavailable(
                code, FAILURE_MESSAGES[code]
            )
        if not issues and ai_ok:
            not_needed = ImprovedCode(
                status=ImprovedCodeStatus.NOT_NEEDED, code=None, message=IMPROVEMENT_NOT_NEEDED
            )
            return _skipped(SkipReason.NOT_NEEDED), not_needed
        if not issues:
            code = ErrorCode.AI_OUTPUT_INVALID
            return _skipped(SkipReason.DEPENDENCY_FAILED, code), _unavailable(
                code, FAILURE_MESSAGES[code]
            )
        if deadline.remaining() < self._options.improvement_start_min_s:
            code = ErrorCode.REVIEW_TIMEOUT
            return _skipped(SkipReason.DEADLINE_EXCEEDED, code), _unavailable(
                code, FAILURE_MESSAGES[code]
            )
        try:
            async with asyncio.timeout(_bounded(deadline)):
                return await self._improver.improve(
                    submission, issues[:PROMPT_MAX_IMPROVEMENT_ISSUES], deadline
                )
        except ReviewError as error:
            return _failed(error.code), _unavailable(error.code, FAILURE_MESSAGES[error.code])
        except TimeoutError:
            code = ErrorCode.REVIEW_TIMEOUT
            return _failed(code), _unavailable(code, FAILURE_MESSAGES[code])
        except Exception as error:
            logger.error("improvement stage failed: %s", type(error).__name__)
            code = ErrorCode.AI_MODEL_UNAVAILABLE
            return _failed(code), _unavailable(code, FAILURE_MESSAGES[code])

    @staticmethod
    def _aggregate(static: StaticAnalysisResult) -> StageOutcome:
        """§5.6: failed if any tool failed; disabled if every tool is; otherwise succeeded."""
        tools = [t.outcome for t in static.tools if t.tool != "python-parser"]
        if any(o.status is OutcomeStatus.FAILED for o in tools):
            return _failed(ErrorCode.STATIC_ANALYSIS_FAILURE)
        if tools and all(o.skip_reason is SkipReason.DISABLED for o in tools):
            return _skipped(SkipReason.DISABLED)
        return StageOutcome(status=OutcomeStatus.SUCCEEDED)

    @staticmethod
    def _warnings(
        issues: tuple[Issue, ...], static: StaticAnalysisResult, ai: AIReviewResult | None
    ) -> tuple[ReviewWarning, ...]:
        warnings = []
        if len(issues) > MAX_ISSUES_RETURNED:
            warnings.append(
                ReviewWarning(
                    code="ISSUES_TRUNCATED",
                    message=f"Showing {MAX_ISSUES_RETURNED} of {len(issues)} issues.",
                )
            )
        if any(t.outcome.skip_reason is SkipReason.DISABLED for t in static.tools):
            warnings.append(
                ReviewWarning(
                    code="REDUCED_COVERAGE",
                    message="Some analysis components are disabled, so coverage is reduced.",
                )
            )
        if ai is not None and ai.dropped_issue_count:
            warnings.append(
                ReviewWarning(
                    code="AI_ISSUES_DROPPED",
                    message=f"{ai.dropped_issue_count} AI issue(s) were invalid and were dropped.",
                )
            )
        return tuple(warnings)

    @staticmethod
    def _internal_error(stage: str, error: Exception) -> ReviewOutcome:
        logger.error("review stage %s failed: %s", stage, type(error).__name__)
        code = ErrorCode.INTERNAL_ERROR
        return ReviewOutcome(
            ReviewStatus.FAILED, failure=ReviewFailure(code=code, message=FAILURE_MESSAGES[code])
        )
