"""OllamaAIReviewProvider: `POST /api/chat` and `GET /api/tags` over httpx (CIS §9.3–9.5, §9.7).

Every call is rendered from the versioned prompts, checked against the request-time context
budget before any HTTP request, constrained by the generated output schema, and returned as raw
model text to the shared AIResponseProcessor. Nothing from a request or a response is logged:
only numbers reach the stage record, through the metrics collector (§19.1, D-55).
"""

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ai.budget import (
    REVIEW_MIN_OUTPUT_TOKENS,
    actual_output_budget,
    estimated_input_tokens,
    improve_min_output_tokens,
    require_output_budget,
)
from ai.prompts.renderer import (
    Operation,
    PromptSet,
    RenderedPrompt,
    new_nonce,
    omitted_static_findings,
    output_schemas,
    render_improvement,
    render_review,
    render_system,
    schema_text,
)
from ai.provider import attempt_timeout, with_retry
from ai.validation import process_improvement, process_review
from shared.domain.enums import Language
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
from shared.domain.metrics import current_ai_metrics
from shared.domain.models import AIReviewResult

MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # §9.4
CONNECT_TIMEOUT_S, WRITE_TIMEOUT_S, POOL_TIMEOUT_S = 5.0, 10.0, 5.0  # §9.3 step 5
MODEL_NOT_INSTALLED = "The configured AI model is not installed."
NOT_REACHABLE = ProviderHealth(available=False, detail="Ollama is not reachable")
MODEL_ABSENT = ProviderHealth(available=False, detail="Configured model is not installed")
DIGEST_MISMATCH = ProviderHealth(
    available=False, detail="Installed model differs from the frozen model"
)
MODEL_READY = ProviderHealth(available=True, detail="Configured model is installed")


@dataclass(frozen=True)
class OllamaOptions:
    model: str
    model_digest: str | None = None
    temperature: float = 0.0
    seed: int = 42
    num_ctx: int = 16_384
    num_predict: int = 8_192
    timeout_s: float = 180.0
    keep_alive: str = "10m"
    retry_count: int = 1


def model_names(model: str) -> set[str]:
    """The configured name, plus `:latest` when it has no tag (§6.5)."""
    untagged = ":" not in model.rsplit("/", 1)[-1]
    return {model, f"{model}:latest"} if untagged else {model}


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class OllamaAIReviewProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        options: OllamaOptions,
        prompts: PromptSet,
        language_names: Mapping[Language, str],
        nonce: Callable[[], str] = new_nonce,
    ) -> None:
        self._client = client
        self._options = options
        self._prompts = prompts
        self._nonce = nonce
        self._schemas = output_schemas()
        self._systems = {  # rendered once: they depend only on the language and the schema
            (operation, language): render_system(prompts, operation, name, schema_text(schema))
            for operation, schema in self._schemas.items()
            for language, name in language_names.items()
        }

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider="ollama", model=self._options.model, prompt_version=self._prompts.version
        )

    async def review(self, request: AIReviewRequest, deadline: Deadline) -> AIReviewResult:
        system = self._systems[("review", request.language)]

        async def attempt(number: int) -> AIReviewResult:
            rendered = render_review(self._prompts, request, system, self._nonce())
            raw = await self._chat("review", rendered, REVIEW_MIN_OUTPUT_TOKENS, number, deadline)
            processed = process_review(raw, request)
            return AIReviewResult(
                summary=processed.summary,
                candidates=processed.candidates,
                dropped_issue_count=processed.dropped_issue_count,
                attempts=number,
                static_findings_omitted=omitted_static_findings(request),
            )

        result, _ = await with_retry(attempt, self._options.retry_count, deadline)
        return result

    async def improve(
        self, request: AIImprovementRequest, deadline: Deadline
    ) -> AIImprovementResult:
        system = self._systems[("improve", request.language)]
        minimum = improve_min_output_tokens(request.source.byte_size)

        async def attempt(number: int) -> AIImprovementResult:
            rendered = render_improvement(self._prompts, request, system, self._nonce())
            raw = await self._chat("improve", rendered, minimum, number, deadline)
            code, notes = process_improvement(raw)
            return AIImprovementResult(code=code, notes=notes, attempts=number)

        result, _ = await with_retry(attempt, self._options.retry_count, deadline)
        return result

    async def check_health(self, timeout_s: float) -> ProviderHealth:
        """§6.5 and §9.5: reachable, the model listed, and the digest when one is configured."""
        try:
            async with asyncio.timeout(timeout_s):
                response = await self._client.get("/api/tags", timeout=timeout_s)
            models = response.json()["models"] if response.status_code == 200 else None
        except httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError:
            return NOT_REACHABLE
        if not isinstance(models, list):
            return NOT_REACHABLE
        wanted = model_names(self._options.model)
        entry = next(
            (
                m
                for m in models
                if isinstance(m, dict) and (m.get("name") in wanted or m.get("model") in wanted)
            ),
            None,
        )
        if entry is None:
            return MODEL_ABSENT
        digest = self._options.model_digest
        if digest is not None and not str(entry.get("digest", "")).startswith(digest):
            return DIGEST_MISMATCH
        return MODEL_READY

    async def _chat(
        self,
        operation: Operation,
        prompt: RenderedPrompt,
        required_min_output: int,
        number: int,
        deadline: Deadline,
    ) -> str:
        options = self._options
        metrics = current_ai_metrics()
        seed = options.seed + number - 1  # 42, then 43 on the retry (§9.4)
        metrics.attempt, metrics.seed = number, seed
        metrics.num_predict = metrics.prompt_eval_count = metrics.eval_count = None
        metrics.total_duration = metrics.token_estimate_exceeded = None
        metrics.input_token_estimate = estimated_input_tokens(prompt.system, prompt.user)
        budget = actual_output_budget(
            prompt.system, prompt.user, num_ctx=options.num_ctx, num_predict=options.num_predict
        )
        num_predict = require_output_budget(budget, required_min_output)  # no call if it fails
        metrics.num_predict = num_predict
        timeout_s = attempt_timeout(options.timeout_s, deadline)
        if timeout_s <= 0:
            raise AIProviderTimeout()
        body = {
            "model": options.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "stream": False,
            "think": False,  # always sent (D-77)
            "format": self._schemas[operation],
            "options": {
                "temperature": options.temperature,
                "num_ctx": options.num_ctx,
                "num_predict": num_predict,
                "seed": seed,
            },
            "keep_alive": -1 if options.keep_alive == "-1" else options.keep_alive,
        }
        envelope = await self._post(body, timeout_s)
        message = envelope.get("message")
        thinking = isinstance(message, dict) and bool(message.get("thinking"))  # never logged
        metrics.thinking_emitted = (metrics.thinking_emitted or 0) + thinking
        metrics.prompt_eval_count = _count(envelope.get("prompt_eval_count"))
        metrics.eval_count = _count(envelope.get("eval_count"))
        metrics.total_duration = _count(envelope.get("total_duration"))
        if metrics.prompt_eval_count is not None:  # A17
            metrics.token_estimate_exceeded = (
                metrics.prompt_eval_count > metrics.input_token_estimate
            )
        done_reason = envelope.get("done_reason")
        if done_reason == "length":
            raise AIContextExceeded()
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or done_reason != "stop":
            raise AIResponseInvalid(retryable=False)  # D-65: only "stop" completes
        return content

    async def _post(self, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        timeout = httpx.Timeout(
            connect=CONNECT_TIMEOUT_S, read=timeout_s, write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S
        )
        try:
            async with (
                asyncio.timeout(timeout_s),
                self._client.stream("POST", "/api/chat", json=body, timeout=timeout) as response,
            ):
                if response.status_code == 404:
                    raise AIProviderUnavailable(MODEL_NOT_INSTALLED)
                if not response.is_success:
                    raise AIProviderUnavailable()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise AIResponseInvalid(retryable=False)
        except httpx.ReadTimeout, TimeoutError:
            raise AIProviderTimeout() from None
        except httpx.HTTPError:
            raise AIProviderUnavailable() from None
        try:
            envelope = json.loads(raw)
        except ValueError:
            raise AIResponseInvalid(retryable=False) from None
        if not isinstance(envelope, dict):
            raise AIResponseInvalid(retryable=False)
        return envelope
