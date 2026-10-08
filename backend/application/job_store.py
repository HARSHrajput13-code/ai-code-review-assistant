"""InMemoryReviewJobStore: reviews, running submissions, idempotency records (CIS §6.3, §6.4, §7.1).

Single-process, in memory only, guarded by one asyncio.Lock. Idempotency records live exactly as
long as their review; fingerprints are never logged or persisted.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from shared.domain.enums import ReviewStatus
from shared.domain.errors import IdempotencyConflict, ServiceBusy
from shared.domain.interfaces import IdempotencyRecord
from shared.domain.models import ReviewSubmission
from shared.domain.review import CodeReview


@dataclass
class _Entry:
    review: CodeReview
    submission: ReviewSubmission | None  # released at the terminal state (D-06)
    idempotency: IdempotencyRecord | None


class InMemoryReviewJobStore:
    def __init__(self, *, max_active: int, max_retained: int, ttl_seconds: int) -> None:
        self._entries: dict[UUID, _Entry] = {}
        self._keys: dict[str, IdempotencyRecord] = {}
        self._lock = asyncio.Lock()
        self._max_active = max_active
        self._max_retained = max_retained
        self._ttl = timedelta(seconds=ttl_seconds)

    async def find_by_idempotency_key(self, key: str) -> tuple[UUID, str] | None:
        async with self._lock:
            record = self._keys.get(key)
            return None if record is None else (record.review_id, record.fingerprint)

    async def create_or_replay(
        self,
        review: CodeReview,
        submission: ReviewSubmission,
        idempotency: IdempotencyRecord | None,
    ) -> tuple[CodeReview, bool]:
        """Atomically: re-check the key (§6.3 steps 6-7), admit (12) and create (13)."""
        async with self._lock:
            if idempotency is not None and idempotency.key in self._keys:
                known = self._keys[idempotency.key]
                if known.fingerprint != idempotency.fingerprint:
                    raise IdempotencyConflict()
                return self._entries[known.review_id].review, False
            if self._active() >= self._max_active:
                raise ServiceBusy()
            self._evict()
            self._entries[review.review_id] = _Entry(review, submission, idempotency)
            if idempotency is not None:
                self._keys[idempotency.key] = idempotency
            return review, True

    async def get(self, review_id: UUID) -> CodeReview | None:
        async with self._lock:
            entry = self._entries.get(review_id)
            return None if entry is None else entry.review

    async def replace(self, review: CodeReview) -> None:
        async with self._lock:
            entry = self._entries.get(review.review_id)
            if entry is None:
                return  # purged meanwhile
            entry.review = review
            if review.status.terminal:
                entry.submission = None

    async def count_active(self) -> int:
        async with self._lock:
            return self._active()

    async def active_reviews(self) -> tuple[CodeReview, ...]:
        async with self._lock:
            return tuple(e.review for e in self._entries.values() if not e.review.status.terminal)

    async def purge_expired(self, now: datetime) -> None:
        """Remove terminal reviews older than the TTL, with their idempotency records."""
        async with self._lock:
            for review_id, entry in list(self._entries.items()):
                finished = entry.review.finished_at
                if finished is not None and finished + self._ttl <= now:
                    self._remove(review_id)

    def _active(self) -> int:
        return sum(
            1
            for e in self._entries.values()
            if e.review.status in (ReviewStatus.PENDING, ReviewStatus.RUNNING)
        )

    def _evict(self) -> None:
        """Keep at most max_retained reviews, evicting the oldest terminal ones first (§6.4)."""
        terminal = sorted(
            (e for e in self._entries.values() if e.review.finished_at is not None),
            key=lambda e: e.review.finished_at or e.review.created_at,
        )
        while len(self._entries) >= self._max_retained and terminal:
            self._remove(terminal.pop(0).review.review_id)

    def _remove(self, review_id: UUID) -> None:
        entry = self._entries.pop(review_id)
        if entry.idempotency is not None:
            self._keys.pop(entry.idempotency.key, None)
