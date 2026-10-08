"""The AIReviewProvider request and result types, and the rules every provider shares.

The types are defined once, in shared/domain (the Protocol in `shared` refers to them, and
`shared` imports nothing internal, §3.1). This module re-exports the same objects as the `ai`
layer's public API (§4), alongside the retry and timeout rules (§7.1, §7.3, §9.4).
"""

from collections.abc import Awaitable, Callable

from shared.domain.errors import AIResponseInvalid
from shared.domain.interfaces import (
    AIImprovementRequest,
    AIImprovementResult,
    AIReviewProvider,
    AIReviewRequest,
    Deadline,
    ProviderDescriptor,
    ProviderHealth,
)
from shared.domain.models import AIReviewResult

__all__ = [
    "RETRY_MIN_REMAINING_S",
    "AIImprovementRequest",
    "AIImprovementResult",
    "AIReviewProvider",
    "AIReviewRequest",
    "AIReviewResult",
    "ProviderDescriptor",
    "ProviderHealth",
    "attempt_timeout",
    "with_retry",
]

RETRY_MIN_REMAINING_S = 20.0  # a retry is not started with less time left (§9.4)


def attempt_timeout(limit_s: float, deadline: Deadline) -> float:
    """Each attempt gets min(per-attempt limit, remaining overall deadline) (D-48)."""
    return min(limit_s, deadline.remaining())


async def with_retry[T](
    attempt: Callable[[int], Awaitable[T]], retry_count: int, deadline: Deadline
) -> tuple[T, int]:
    """Run attempt 1; retry once on a retryable invalid response (at most 2 calls, D-41)."""
    if retry_count not in (0, 1):
        raise ValueError("AI_OUTPUT_RETRY_COUNT is 0 or 1")
    try:
        return await attempt(1), 1
    except AIResponseInvalid as error:
        if not (error.retryable and retry_count and deadline.remaining() >= RETRY_MIN_REMAINING_S):
            raise
    return await attempt(2), 2
