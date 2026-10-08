"""FakeAIReviewProvider: deterministic and scriptable, for tests and offline development (CIS §9.6).

It returns raw model text through the same AIResponseProcessor as the real provider, so tests
exercise the real validation path. It never calls Ollama and is not a model-selection substitute.
"""

import asyncio
import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from ai.provider import attempt_timeout, with_retry
from ai.validation import process_improvement, process_review
from shared.domain.errors import (
    AIContextExceeded,
    AIProviderTimeout,
    AIProviderUnavailable,
    AIResponseInvalid,
)
from shared.domain.interfaces import (
    AIImprovementRequest,
    AIImprovementResult,
    AIReviewRequest,
    Deadline,
    ProviderDescriptor,
    ProviderHealth,
)
from shared.domain.models import AIReviewResult, SourceText

DEFAULT_SUMMARY = "Automated test review."


@dataclass(frozen=True)
class FakeResponse:
    """One scripted provider call. `content=None` means the deterministic default."""

    content: str | None = None
    error: Literal["unavailable", "timeout", "context_exceeded"] | None = None
    done_reason: str = "stop"
    thinking: str | None = None  # injected; ignored, only counted (D-77)
    delay_s: float = 0.0


INVALID_JSON = FakeResponse(content="{not json")
UNAVAILABLE = FakeResponse(error="unavailable")
TIMEOUT = FakeResponse(error="timeout")
CONTEXT_EXCEEDED = FakeResponse(error="context_exceeded")
INVALID_THEN_VALID = (INVALID_JSON, FakeResponse())


@dataclass(frozen=True)
class FakeCall:
    operation: Literal["review", "improve"]
    attempt: int
    seed: int
    timeout_s: float
    request: AIReviewRequest | AIImprovementRequest


def default_review(source: SourceText) -> str:
    line = next(n for n, text in enumerate(source.lines(), start=1) if text.strip())
    issue = {
        "severity": "LOW",
        "category": "READABILITY",
        "title": "Readability could be improved",
        "line": line,
        "end_line": line,
        "evidence": source.line(line),
        "summary": "This line could be written more clearly.",
        "impact": "Code that is hard to read is easier to get wrong.",
        "recommendation": "Consider clearer names and simpler expressions.",
        "related_static_ids": [],
    }
    return json.dumps({"summary": DEFAULT_SUMMARY, "issues": [issue]})


def default_improvement(source: SourceText) -> str:
    return json.dumps({"improved_code": "# Reviewed\n" + source.text, "notes": []})


@dataclass
class FakeAIReviewProvider:
    review_script: Iterable[FakeResponse] = ()
    improve_script: Iterable[FakeResponse] = ()
    retry_count: int = 1
    seed: int = 42
    attempt_limit_s: float = 180.0
    prompt_version: str = "v1"
    calls: list[FakeCall] = field(default_factory=list)
    thinking_emitted: int = 0

    def __post_init__(self) -> None:
        self._scripts = {"review": deque(self.review_script), "improve": deque(self.improve_script)}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(provider="fake", model="fake", prompt_version=self.prompt_version)

    async def check_health(self, timeout_s: float) -> ProviderHealth:
        return ProviderHealth(available=True, detail="Fake provider")

    async def review(self, request: AIReviewRequest, deadline: Deadline) -> AIReviewResult:
        async def attempt(number: int) -> AIReviewResult:
            raw = await self._call(
                "review", number, request, deadline, default_review(request.source)
            )
            processed = process_review(raw, request)
            return AIReviewResult(
                summary=processed.summary,
                candidates=processed.candidates,
                dropped_issue_count=processed.dropped_issue_count,
                attempts=number,
            )

        result, _ = await with_retry(attempt, self.retry_count, deadline)
        return result

    async def improve(
        self, request: AIImprovementRequest, deadline: Deadline
    ) -> AIImprovementResult:
        async def attempt(number: int) -> AIImprovementResult:
            raw = await self._call(
                "improve", number, request, deadline, default_improvement(request.source)
            )
            code, notes = process_improvement(raw)
            return AIImprovementResult(code=code, notes=notes, attempts=number)

        result, _ = await with_retry(attempt, self.retry_count, deadline)
        return result

    async def _call(
        self,
        operation: Literal["review", "improve"],
        number: int,
        request: AIReviewRequest | AIImprovementRequest,
        deadline: Deadline,
        default: str,
    ) -> str:
        script = self._scripts[operation]
        response = script.popleft() if script else FakeResponse()
        timeout = attempt_timeout(self.attempt_limit_s, deadline)
        self.calls.append(FakeCall(operation, number, self.seed + number - 1, timeout, request))
        if response.delay_s:
            await asyncio.sleep(response.delay_s)
        match response.error:
            case "unavailable":
                raise AIProviderUnavailable()
            case "timeout":
                raise AIProviderTimeout()
            case "context_exceeded":
                raise AIContextExceeded()
        if response.thinking is not None:
            self.thinking_emitted += 1
        if response.done_reason == "length":
            raise AIContextExceeded()
        if response.done_reason != "stop":
            raise AIResponseInvalid(retryable=False)
        return default if response.content is None else response.content
