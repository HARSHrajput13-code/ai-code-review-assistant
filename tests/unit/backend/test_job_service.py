"""ReviewJobService and InMemoryReviewJobStore (CIS §6.3, §6.4, §7.1, D-46). No real sleeps."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest

from ai.fake import FakeAIReviewProvider
from analysis.registry import LanguageRegistry
from backend.application.job_service import ReviewJobService, fingerprint
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.validation import SubmissionValidator
from shared.domain.enums import ErrorCode, ReviewStage, ReviewStatus
from shared.domain.errors import IdempotencyConflict, RequestRejected
from shared.domain.review import CodeReview
from tests.unit.backend.pipeline_doubles import SOURCE, GatedRunner, StubAdapter
from tests.unit.builders import FakeClock

KEY = "key-0123456789abcdef"
CODE = SOURCE.text


def service(
    clock: FakeClock,
    runner: Any = None,
    *,
    max_active: int = 3,
    max_retained: int = 50,
    max_concurrent: int = 1,
) -> tuple[ReviewJobService, InMemoryReviewJobStore]:
    store = InMemoryReviewJobStore(
        max_active=max_active, max_retained=max_retained, ttl_seconds=900
    )
    validator = SubmissionValidator(LanguageRegistry([StubAdapter()]), 12_000, 500)
    runner = runner or ReviewOrchestrator(FakeAIReviewProvider(), clock, OrchestratorOptions())
    jobs = ReviewJobService(
        store, validator, runner, clock, max_concurrent=max_concurrent, review_timeout_seconds=300
    )
    return jobs, store


def scenario(test: Callable[[FakeClock], Awaitable[None]]) -> None:
    asyncio.run(test(FakeClock()))


async def rejected(call: Awaitable[Any]) -> ErrorCode:
    with pytest.raises(RequestRejected) as raised:
        await call
    return raised.value.code


def test_a_review_runs_to_completion() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock)
        created, is_new = await jobs.submit("python", CODE)
        assert is_new and created.status is ReviewStatus.PENDING
        await jobs.wait_idle()
        done = await jobs.get(created.review_id)
        assert done.status is ReviewStatus.COMPLETED and done.stage is ReviewStage.FINISHED
        assert done.started_at is not None and done.finished_at is not None

    scenario(body)


def test_same_key_and_payload_replays_without_a_second_review() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, store = service(clock)
        first, created = await jobs.submit("python", CODE, KEY)
        again, replayed = await jobs.submit("python", CODE, KEY)
        assert (created, replayed, again.review_id) == (True, False, first.review_id)
        assert await store.count_active() == 1

    scenario(body)


def test_same_key_with_a_different_payload_conflicts_and_creates_nothing() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, store = service(clock)
        await jobs.submit("python", CODE, KEY)
        assert (
            await rejected(jobs.submit("python", CODE + "\n", KEY))
            is ErrorCode.IDEMPOTENCY_CONFLICT
        )
        assert await store.count_active() == 1

    scenario(body)


def test_new_keys_and_no_key_create_new_reviews() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock, max_active=5)
        ids = {
            (await jobs.submit("python", CODE, key))[0].review_id
            for key in (KEY, KEY.replace("0", "9"), None, None)
        }
        assert len(ids) == 4

    scenario(body)


class ContendedStore(InMemoryReviewJobStore):
    """Holds every key lookup at a barrier, so concurrent POSTs all miss the pre-check (§6.3
    steps 6-7) and race into create_or_replay, where only the lock-protected re-check can
    decide. Records each create_or_replay outcome."""

    def __init__(self, parties: int) -> None:
        super().__init__(max_active=3, max_retained=50, ttl_seconds=900)
        self._barrier = asyncio.Barrier(parties)
        self.lookups: list[object] = []
        self.outcomes: list[str] = []

    async def find_by_idempotency_key(self, key: str) -> tuple[UUID, str] | None:
        found = await super().find_by_idempotency_key(key)
        self.lookups.append(found)
        await self._barrier.wait()  # nobody creates until every POST has looked the key up
        return found

    async def create_or_replay(self, *args: Any) -> tuple[CodeReview, bool]:
        try:
            review, created = await super().create_or_replay(*args)
        except IdempotencyConflict:
            self.outcomes.append("conflict")
            raise
        self.outcomes.append("created" if created else "replayed")
        return review, created


def contended(clock: FakeClock, store: ContendedStore) -> ReviewJobService:
    validator = SubmissionValidator(LanguageRegistry([StubAdapter()]), 12_000, 500)
    orchestrator = ReviewOrchestrator(FakeAIReviewProvider(), clock, OrchestratorOptions())
    return ReviewJobService(
        store, validator, orchestrator, clock, max_concurrent=1, review_timeout_seconds=300
    )


def test_concurrent_posts_with_one_key_create_once() -> None:
    async def body(clock: FakeClock) -> None:
        store = ContendedStore(parties=2)
        jobs = contended(clock, store)
        results = await asyncio.gather(*(jobs.submit("python", CODE, KEY) for _ in range(2)))
        assert store.lookups == [None, None]  # both passed the pre-check: genuine contention
        assert sorted(store.outcomes) == ["created", "replayed"]  # the locked re-check decided
        assert sorted(created for _, created in results) == [False, True]
        assert len({review.review_id for review, _ in results}) == 1
        await jobs.wait_idle()
        assert len(store._entries) == 1  # exactly one review exists

    scenario(body)


def test_concurrent_posts_with_one_key_and_different_payloads_conflict() -> None:
    async def body(clock: FakeClock) -> None:
        store = ContendedStore(parties=2)
        jobs = contended(clock, store)
        results = await asyncio.gather(
            jobs.submit("python", CODE, KEY),
            jobs.submit("python", CODE + "\n", KEY),
            return_exceptions=True,
        )
        assert store.lookups == [None, None]
        assert sorted(store.outcomes) == ["conflict", "created"]
        assert sum(isinstance(r, IdempotencyConflict) for r in results) == 1
        await jobs.wait_idle()
        assert len(store._entries) == 1  # nothing was created for the conflict

    scenario(body)


def test_capacity_and_replay_when_full() -> None:
    async def body(clock: FakeClock) -> None:
        runner = GatedRunner()
        jobs, _ = service(clock, runner, max_active=1)
        first, _ = await jobs.submit("python", CODE, KEY)
        assert await rejected(jobs.submit("python", CODE)) is ErrorCode.SERVICE_BUSY
        replay, created = await jobs.submit("python", CODE, KEY)  # no SERVICE_BUSY for a replay
        assert (replay.review_id, created) == (first.review_id, False)
        runner.gate.set()
        await jobs.wait_idle()

    scenario(body)


def test_a_rejected_request_records_no_key() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock)
        assert await rejected(jobs.submit("python", "   ", KEY)) is ErrorCode.EMPTY_CODE
        _, created = await jobs.submit("python", CODE, KEY)
        assert created

    scenario(body)


def test_after_the_ttl_purge_the_key_is_unknown() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock)
        first, _ = await jobs.submit("python", CODE, KEY)
        await jobs.wait_idle()
        clock.advance(901)
        assert await rejected(jobs.get(first.review_id)) is ErrorCode.REVIEW_NOT_FOUND
        second, created = await jobs.submit("python", CODE, KEY)
        assert created and second.review_id != first.review_id

    scenario(body)


def test_queue_deadline_fails_a_review_that_never_started() -> None:
    async def body(clock: FakeClock) -> None:
        runner = GatedRunner()
        jobs, _ = service(clock, runner)
        running, _ = await jobs.submit("python", CODE)
        queued, _ = await jobs.submit("python", CODE)
        await asyncio.sleep(0)  # let the first job take the only slot
        assert (await jobs.get(running.review_id)).stage is ReviewStage.GENERATING_IMPROVEMENT
        clock.advance(300)
        timed_out = await jobs.get(queued.review_id)
        assert timed_out.status is ReviewStatus.FAILED
        assert timed_out.failure is not None and timed_out.failure.code is ErrorCode.REVIEW_TIMEOUT
        runner.gate.set()
        await jobs.wait_idle()
        assert runner.runs == 1  # the timed-out review never ran

    scenario(body)


def test_unknown_review_is_not_found() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock)
        assert await rejected(jobs.get(uuid4())) is ErrorCode.REVIEW_NOT_FOUND

    scenario(body)


def test_oldest_terminal_reviews_are_evicted_first() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, _ = service(clock, max_retained=2)
        ids = []
        for _ in range(3):
            review, _ = await jobs.submit("python", CODE)
            await jobs.wait_idle()
            clock.advance(1)
            ids.append(review.review_id)
        assert await rejected(jobs.get(ids[0])) is ErrorCode.REVIEW_NOT_FOUND
        assert (await jobs.get(ids[2])).status is ReviewStatus.COMPLETED

    scenario(body)


def test_the_submission_is_released_at_the_terminal_state() -> None:
    async def body(clock: FakeClock) -> None:
        jobs, store = service(clock)
        review, _ = await jobs.submit("python", CODE)
        assert store._entries[review.review_id].submission is not None
        await jobs.wait_idle()
        assert store._entries[review.review_id].submission is None

    scenario(body)


def test_shutdown_cancels_running_reviews() -> None:
    async def body(clock: FakeClock) -> None:
        runner = GatedRunner()
        jobs, _ = service(clock, runner)
        await jobs.submit("python", CODE)
        await asyncio.sleep(0)
        await jobs.shutdown()
        assert runner.runs == 1

    scenario(body)


def test_fingerprint_is_canonical_and_payload_sensitive() -> None:
    assert fingerprint("python", "x = 1") == fingerprint("python", "x = 1")
    assert fingerprint("python", "x = 1") != fingerprint("python", "x = 2")
    assert len(fingerprint("python", "é")) == 64
