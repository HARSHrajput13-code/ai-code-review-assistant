"""ReviewOrchestrator: runs the stages for one submission (CIS §7.2, §5.7, §14.5).

It depends only on interfaces (§3): the language adapter, the AI provider and, for the
improvement operation, an `Improver`. The improvement operation and its §14.3 validation are
PR-07; until an `Improver` is supplied, improvement reports as disabled (a non-failure skip) and
no unvalidated code is ever returned. The orchestrator never touches the job store.

Each stage that runs logs `review.stage.started` and `review.stage.finished` under its §7.2 log
name; a stage that never starts logs only its finish, with the skip (§19.1). Stage 11
(IMPROVEMENT_VALIDATION, §14.3) validates the improvement operation's candidate: when the
operation is skipped or ends without returning one, stage 11 logs a finish-only skip for the same
cause. A result the operation returns has been through its own validation, which the operation
(PR-07) logs.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from backend.application.deadline import Clock
from backend.application.events import stage_finished, stage_started
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
from shared.domain.metrics import collect_ai_metrics
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

SUCCEEDED = StageOutcome(status=OutcomeStatus.SUCCEEDED)
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
    error: Exception | None = None  # behind FAILED(INTERNAL_ERROR): its type and frames are logged


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


def _without_tools(
    syntax_candidate: FindingCandidate | None, outcome: StageOutcome
) -> StaticAnalysisResult:
    """The static result when the tools could not run: each one gets `outcome`."""
    tools = tuple(
        ToolOutcome(tool=tool, tool_version=None, outcome=outcome) for tool in ("pylint", "bandit")
    )
    return StaticAnalysisResult(
        syntax_valid=syntax_candidate is None,
        tools=tools,
        candidates=(syntax_candidate,) if syntax_candidate else (),
        diagnostics=(),
    )


def _may_start(remaining: float, threshold: float = 0.0) -> bool:
    """A stage starts only with time left and at least its start threshold (§7.1, D-48).

    The same `remaining` value then bounds the stage, so every timeout is positive: an exhausted
    deadline is never turned into "no limit".
    """
    return remaining > 0 and remaining >= threshold


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
            with self._stage("STATIC_NUMBERING"):  # stage 4a
                static_findings = number_static(static.candidates, source.line_count)
        except Exception as error:
            return self._internal_error(error)

        ai_outcome, ai_result = await self._ai(submission, static_findings, deadline)  # stage 5

        try:
            covered, assessed = coverage(ai_outcome, static, adapter.covered_categories)
            if not assessed:  # checkpoint: nothing can be assessed
                code = ai_outcome.error_code or ErrorCode.REVIEW_TIMEOUT
                return ReviewOutcome(
                    ReviewStatus.FAILED,
                    failure=ReviewFailure(code=code, message=FAILURE_MESSAGES[code]),
                )
            with self._stage("NORMALIZATION"):  # stage 6
                numbered = number_ai(ai_result.candidates, source.line_count) if ai_result else ()
            with self._stage("DEDUPLICATION"):  # stage 7
                ai_findings = dedupe_ai(numbered)
                corroboration = corroborate(ai_findings, static_findings)
                issues = build_issues(static_findings, ai_findings, corroboration)
            with self._stage("SCORING"):  # stage 8
                score = self._policy.score(
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
            return self._internal_error(error)

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

    def _elapsed_ms(self, since: float) -> int:
        return max(0, int((self._clock.monotonic() - since) * 1000))

    def _started(self, stage: str, /, **fields: object) -> float:
        stage_started(stage, **fields)
        return self._clock.monotonic()

    def _finished(
        self,
        stage: str,
        outcome: StageOutcome,
        since: float | None = None,
        error: Exception | None = None,
        /,
        **fields: object,
    ) -> None:
        """`since` is None for a stage that never started."""
        duration = 0 if since is None else self._elapsed_ms(since)
        stage_finished(stage, outcome, duration, error, **fields)

    @contextmanager
    def _stage(self, stage: str) -> Iterator[None]:
        """A pure stage (4a, 6-8): an exception is a bug, FAILED(INTERNAL_ERROR) (§7.2)."""
        since = self._started(stage)
        try:
            yield
        except Exception:
            self._finished(stage, _failed(ErrorCode.INTERNAL_ERROR), since)
            raise  # the review fails, and review.finished logs the exception
        self._finished(stage, SUCCEEDED, since)

    async def _static(
        self, adapter: LanguageAdapter, source: SourceText, deadline: Deadline
    ) -> StaticAnalysisResult:
        """Stages 3-4, each isolated at its boundary (§7.2): a failure fails the tools."""
        since = self._started("PARSING")
        try:
            syntax = adapter.check_syntax(source)  # a syntax error is a result, not a failure
        except Exception as error:
            failed = _failed(ErrorCode.STATIC_ANALYSIS_FAILURE)
            self._finished("PARSING", failed, since, error)
            self._finished("STATIC_ANALYSIS", failed)  # the tools never started
            return _without_tools(None, failed)
        self._finished("PARSING", SUCCEEDED, since)
        remaining = deadline.remaining()
        if not _may_start(remaining):  # the tools never started (§7.2)
            skipped = _skipped(SkipReason.DEADLINE_EXCEEDED, ErrorCode.REVIEW_TIMEOUT)
            self._finished("STATIC_ANALYSIS", skipped)
            return _without_tools(syntax.candidate, skipped)
        since = self._started("STATIC_ANALYSIS")
        try:
            async with asyncio.timeout(remaining):
                static = await adapter.analyze(source, syntax, deadline)
        except TimeoutError:
            failed = _failed(ErrorCode.REVIEW_TIMEOUT)
            self._finished("STATIC_ANALYSIS", failed, since)
            return _without_tools(syntax.candidate, failed)
        except Exception as error:
            failed = _failed(ErrorCode.STATIC_ANALYSIS_FAILURE)
            self._finished("STATIC_ANALYSIS", failed, since, error)
            return _without_tools(syntax.candidate, failed)
        self._finished("STATIC_ANALYSIS", self._aggregate(static), since)
        return static

    async def _ai(
        self, submission: ReviewSubmission, findings: tuple[Finding, ...], deadline: Deadline
    ) -> tuple[StageOutcome, AIReviewResult | None]:
        descriptor = self._provider.descriptor
        model = {
            "ai_provider": descriptor.provider,
            "ai_model": descriptor.model,
            "prompt_version": descriptor.prompt_version,
        }
        remaining = deadline.remaining()
        if not _may_start(remaining, self._options.ai_start_min_s):
            skipped = _skipped(SkipReason.DEADLINE_EXCEEDED, ErrorCode.REVIEW_TIMEOUT)
            self._finished("AI_ANALYSIS", skipped, **model)
            return skipped, None
        started = self._started("AI_ANALYSIS", **model)
        request = AIReviewRequest(
            language=submission.language, source=submission.source, static_findings=findings
        )
        failed, error = None, None
        with collect_ai_metrics() as metrics:  # what the provider observed (§19.1)
            try:
                async with asyncio.timeout(remaining):
                    result = await self._provider.review(request, deadline)
            except ReviewError as rejected:
                failed = _failed(rejected.code)
            except TimeoutError:
                failed = _failed(ErrorCode.REVIEW_TIMEOUT)
            except Exception as unexpected:
                failed, error = _failed(ErrorCode.AI_MODEL_UNAVAILABLE), unexpected
        if failed is not None:
            self._finished("AI_ANALYSIS", failed, started, error, **model, **metrics.fields())
            return failed, None
        elapsed = self._elapsed_ms(started)
        outcome = StageOutcome(status=OutcomeStatus.SUCCEEDED, duration_ms=elapsed)
        observed = metrics.fields() | {"attempt": result.attempts}
        self._finished("AI_ANALYSIS", outcome, started, **model, **observed)
        return outcome, result

    async def _improve(
        self,
        submission: ReviewSubmission,
        issues: tuple[Issue, ...],
        ai: StageOutcome,
        deadline: Deadline,
    ) -> tuple[StageOutcome, ImprovedCode]:
        """Stage 10, by the §14.5 decision table, top to bottom. A skip logs only its finish."""
        improver = self._improver if self._options.improvement_enabled else None
        if improver is None:
            skip = _skipped(SkipReason.DISABLED), _unavailable(None, IMPROVEMENT_DISABLED)
        elif (dependency := self._dependency_skip(issues, ai)) is not None:
            skip = dependency
        else:
            remaining = deadline.remaining()
            if _may_start(remaining, self._options.improvement_start_min_s):
                return await self._run_improver(improver, submission, issues, remaining, deadline)
            code = ErrorCode.REVIEW_TIMEOUT
            skip = (
                _skipped(SkipReason.DEADLINE_EXCEEDED, code),
                _unavailable(code, FAILURE_MESSAGES[code]),
            )
        self._finished("IMPROVEMENT", skip[0])
        self._finished("IMPROVEMENT_VALIDATION", skip[0])  # no candidate, for the same reason
        return skip

    @staticmethod
    def _dependency_skip(
        issues: tuple[Issue, ...], ai: StageOutcome
    ) -> tuple[StageOutcome, ImprovedCode] | None:
        """§14.5 rows 2-4: improvement depends on the AI outcome and the issues."""
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
        return None

    async def _run_improver(
        self,
        improver: Improver,
        submission: ReviewSubmission,
        issues: tuple[Issue, ...],
        remaining: float,
        deadline: Deadline,
    ) -> tuple[StageOutcome, ImprovedCode]:
        since = self._started("IMPROVEMENT")
        error, not_validated = None, None  # not_validated: the operation returned no candidate
        try:
            async with asyncio.timeout(remaining):
                outcome, improved = await improver.improve(
                    submission, issues[:PROMPT_MAX_IMPROVEMENT_ISSUES], deadline
                )
        except ReviewError as rejected:
            outcome = _failed(rejected.code)
            improved = _unavailable(rejected.code, FAILURE_MESSAGES[rejected.code])
            if rejected.code is not ErrorCode.IMPROVED_CODE_INVALID:  # else validation ran
                not_validated = _skipped(SkipReason.DEPENDENCY_FAILED, rejected.code)
        except TimeoutError:  # the deadline cancelled the operation (§7.2)
            code = ErrorCode.REVIEW_TIMEOUT
            outcome, improved = _failed(code), _unavailable(code, FAILURE_MESSAGES[code])
            not_validated = _skipped(SkipReason.DEADLINE_EXCEEDED, code)
        except Exception as unexpected:
            code, error = ErrorCode.AI_MODEL_UNAVAILABLE, unexpected
            outcome, improved = _failed(code), _unavailable(code, FAILURE_MESSAGES[code])
            not_validated = _skipped(SkipReason.DEPENDENCY_FAILED, code)
        self._finished("IMPROVEMENT", outcome, since, error)
        if not_validated is not None:
            self._finished("IMPROVEMENT_VALIDATION", not_validated)
        return outcome, improved

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
        if ai is not None and ai.static_findings_omitted:
            warnings.append(
                ReviewWarning(
                    code="STATIC_FINDINGS_TRUNCATED_IN_PROMPT",
                    message=(
                        f"{ai.static_findings_omitted} static finding(s) were not included "
                        "in the AI request."
                    ),
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
    def _internal_error(error: Exception) -> ReviewOutcome:
        code = ErrorCode.INTERNAL_ERROR
        return ReviewOutcome(
            ReviewStatus.FAILED,
            failure=ReviewFailure(code=code, message=FAILURE_MESSAGES[code]),
            error=error,
        )
