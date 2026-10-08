"""ReviewJobService: idempotent submission, scheduling, retrieval (CIS §6.3, §6.4, §7.1, D-46).

Each created review runs in a background asyncio task that holds a strong reference and is gated
by asyncio.Semaphore(REVIEW_MAX_CONCURRENT). Time comes from the injected clock, so the queue
deadline and the TTL purge are evaluated on every submit and get (no timers).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

from backend.application.deadline import Clock, MonotonicDeadline
from backend.application.orchestrator import ProgressReporter, ReviewOutcome
from backend.application.validation import SubmissionValidator
from shared.domain.enums import ErrorCode, ReviewStage, ReviewStatus
from shared.domain.errors import IdempotencyConflict, ReviewNotFound
from shared.domain.interfaces import Deadline, IdempotencyRecord, LanguageAdapter, ReviewJobStore
from shared.domain.models import ReviewFailure, ReviewSubmission
from shared.domain.review import CodeReview, enter_improvement, fail, finish, new_review, start

logger = logging.getLogger(__name__)

QUEUE_TIMEOUT_MESSAGE = "The review waited too long to start."
INTERNAL_MESSAGE = "An unexpected error occurred."


class ReviewRunner(Protocol):
    """Satisfied by ReviewOrchestrator."""

    async def run(
        self,
        submission: ReviewSubmission,
        adapter: LanguageAdapter,
        deadline: Deadline,
        report: ProgressReporter,
    ) -> ReviewOutcome: ...


def fingerprint(language: str, source_code: str) -> str:
    """SHA-256 of the canonical JSON of the submitted raw values (§6.3). In memory only."""
    canonical = json.dumps(
        {"language": language, "source_code": source_code},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8", errors="surrogatepass")).hexdigest()


class ReviewJobService:
    def __init__(
        self,
        store: ReviewJobStore,
        validator: SubmissionValidator,
        orchestrator: ReviewRunner,
        clock: Clock,
        *,
        max_concurrent: int,
        review_timeout_seconds: int,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._store = store
        self._validator = validator
        self._orchestrator = orchestrator
        self._clock = clock
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._timeout = review_timeout_seconds
        self._new_id = new_id
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(
        self, language: str, source_code: str, idempotency_key: str | None = None
    ) -> tuple[CodeReview, bool]:
        """(review, created). A replay returns the existing review and runs no other check."""
        await self.sweep()
        record_fingerprint = fingerprint(language, source_code) if idempotency_key else None
        if idempotency_key is not None:
            known = await self._store.find_by_idempotency_key(idempotency_key)  # steps 6-7
            if known is not None:
                review_id, known_fingerprint = known
                if known_fingerprint != record_fingerprint:
                    raise IdempotencyConflict()
                existing = await self._store.get(review_id)
                if existing is not None:
                    return existing, False

        submission, adapter = self._validator.validate(language, source_code)  # steps 8-11
        review = new_review(self._new_id(), submission.language, self._clock.now())
        record = (
            IdempotencyRecord(
                key=idempotency_key,
                fingerprint=record_fingerprint or "",
                review_id=review.review_id,
            )
            if idempotency_key is not None
            else None
        )
        stored, created = await self._store.create_or_replay(review, submission, record)  # 12-13
        if created:
            task = asyncio.create_task(self._run(stored.review_id, submission, adapter))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return stored, created

    async def get(self, review_id: UUID) -> CodeReview:
        await self.sweep()
        review = await self._store.get(review_id)
        if review is None:
            raise ReviewNotFound()
        return review

    async def sweep(self) -> None:
        """TTL purge, and the queue deadline for reviews that never started (§7.1)."""
        now = self._clock.now()
        await self._store.purge_expired(now)
        limit = timedelta(seconds=self._timeout)
        for review in await self._store.active_reviews():
            if review.status is ReviewStatus.PENDING and now - review.created_at >= limit:
                failure = ReviewFailure(
                    code=ErrorCode.REVIEW_TIMEOUT, message=QUEUE_TIMEOUT_MESSAGE
                )
                await self._store.replace(fail(review, failure, now))

    async def shutdown(self) -> None:
        """Cancel running and queued tasks."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def wait_idle(self) -> None:
        """Await every scheduled review (used by tests and graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _run(
        self, review_id: UUID, submission: ReviewSubmission, adapter: LanguageAdapter
    ) -> None:
        async with self._semaphore:
            await self.sweep()  # the queue deadline may have passed while waiting
            queued = await self._store.get(review_id)
            if queued is None or queued.status is not ReviewStatus.PENDING:
                return
            review = start(queued, self._clock.now())
            await self._store.replace(review)

            async def report(stage: ReviewStage) -> None:
                nonlocal review
                if stage is ReviewStage.GENERATING_IMPROVEMENT:
                    review = enter_improvement(review)
                    await self._store.replace(review)

            deadline = MonotonicDeadline(self._clock, self._timeout)
            try:
                outcome = await self._orchestrator.run(submission, adapter, deadline, report)
            except Exception as error:
                logger.error("review %s failed: %s", review_id, type(error).__name__)
                failure = ReviewFailure(code=ErrorCode.INTERNAL_ERROR, message=INTERNAL_MESSAGE)
                await self._store.replace(fail(review, failure, self._clock.now()))
                return
            now = self._clock.now()
            if outcome.result is not None:
                final = finish(review, outcome.status, outcome.result, now)
            else:
                failure = outcome.failure or ReviewFailure(
                    code=ErrorCode.INTERNAL_ERROR, message=INTERNAL_MESSAGE
                )
                final = fail(review, failure, now)
            await self._store.replace(final)  # also releases the submission
