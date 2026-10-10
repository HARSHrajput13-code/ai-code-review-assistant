"""The improvement operation, the orchestrator's `Improver` (CIS §14, §7.2 stages 10-11).

Stage 10 is one `provider.improve` operation: the provider owns its request-time budget, attempt
timeouts and single retry (§9, §7.3), and its errors propagate unchanged for the orchestrator to
map by the §14.5 decision table. Stage 11 validates the candidate (§14.3) and is logged here,
because only this operation holds a candidate: a rejection logs its internal reason and raises
`ImprovedCodeInvalid`. Generated code is never executed, written or logged (§14.6).
"""

from backend.application.deadline import Clock
from backend.application.events import stage_finished, stage_started
from backend.application.validation import AdapterLookup
from backend.review.improvement import accept_candidate
from shared.domain.enums import ErrorCode, ImprovedCodeStatus, OutcomeStatus
from shared.domain.errors import ImprovedCodeInvalid
from shared.domain.interfaces import AIImprovementRequest, AIReviewProvider, Deadline
from shared.domain.models import ImprovedCode, Issue, ReviewSubmission, StageOutcome

VALIDATION = "IMPROVEMENT_VALIDATION"
INVALID = StageOutcome(
    status=OutcomeStatus.FAILED,
    error_code=ErrorCode.IMPROVED_CODE_INVALID,
    message="Improved code could not be validated.",
)


class ImprovementOperation:
    def __init__(
        self,
        provider: AIReviewProvider,
        registry: AdapterLookup,
        clock: Clock,
        max_source_bytes: int,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._clock = clock
        self._max_source_bytes = max_source_bytes

    async def improve(
        self, submission: ReviewSubmission, issues: tuple[Issue, ...], deadline: Deadline
    ) -> tuple[StageOutcome, ImprovedCode]:
        """The orchestrator passes at most the first 20 sorted issues (§14.1)."""
        started = self._clock.monotonic()
        request = AIImprovementRequest(
            language=submission.language, source=submission.source, issues=issues
        )
        result = await self._provider.improve(request, deadline)
        code = self._validate(submission, result.code)
        outcome = StageOutcome(status=OutcomeStatus.SUCCEEDED, duration_ms=self._ms(started))
        return outcome, ImprovedCode(
            status=ImprovedCodeStatus.AVAILABLE, code=code, notes=result.notes
        )

    def _validate(self, submission: ReviewSubmission, candidate: str) -> str:
        """Stage 11: §14.3 steps 1-6 in order; the first failure ends it."""
        since = self._clock.monotonic()
        stage_started(VALIDATION)
        try:
            code = accept_candidate(submission.source, candidate, self._max_source_bytes)
            adapter = self._registry.get(submission.language)
            if adapter is None:  # the submission was validated against this registry
                raise LookupError("no language adapter for the submission")
            checked = adapter.validate_generated_code(submission.source, code)
            if checked.reason is not None:
                raise ImprovedCodeInvalid(checked.reason)
        except ImprovedCodeInvalid as rejected:
            stage_finished(VALIDATION, INVALID, self._ms(since), rejection_reason=rejected.reason)
            raise
        except Exception as error:  # §7.2: an unexpected stage-11 error is IMPROVED_CODE_INVALID
            stage_finished(VALIDATION, INVALID, self._ms(since), error)
            raise ImprovedCodeInvalid(type(error).__name__) from None
        stage_finished(VALIDATION, StageOutcome(status=OutcomeStatus.SUCCEEDED), self._ms(since))
        return code

    def _ms(self, since: float) -> int:
        return max(0, int((self._clock.monotonic() - since) * 1000))
