"""Readiness of the review dependencies (CIS §6.5, §9.5, D-54).

Each component reports `{available, detail}` through an interface; the application knows no
provider, tool or storage details. A failed or slow check is unavailable with a fixed detail,
never an exception text.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from shared.domain.interfaces import AIReviewProvider, ProviderHealth

logger = logging.getLogger(__name__)

AI_HEALTH_TIMEOUT_S = 3.0  # §6.5: GET /api/tags must answer within 3 s
AI_CHECK_FAILED = ProviderHealth(available=False, detail="AI provider check failed")


@dataclass(frozen=True)
class Readiness:
    ai_provider: ProviderHealth
    static_tools: ProviderHealth
    persistence: ProviderHealth

    @property
    def ready(self) -> bool:
        return all(c.available for c in (self.ai_provider, self.static_tools, self.persistence))


class ReadinessService:
    def __init__(
        self,
        provider: AIReviewProvider,
        static_tools: Callable[[], ProviderHealth],
        persistence: Callable[[], ProviderHealth],
    ) -> None:
        self._provider = provider
        self._static_tools = static_tools
        self._persistence = persistence

    async def check(self) -> Readiness:
        return Readiness(
            ai_provider=await self._ai(),
            static_tools=self._static_tools(),
            persistence=self._persistence(),
        )

    async def _ai(self) -> ProviderHealth:
        try:
            async with asyncio.timeout(AI_HEALTH_TIMEOUT_S):
                return await self._provider.check_health(AI_HEALTH_TIMEOUT_S)
        except Exception as error:  # reported as unavailable, never with its text
            logger.warning("readiness.ai_check_failed: %s", type(error).__name__)
            return AI_CHECK_FAILED
