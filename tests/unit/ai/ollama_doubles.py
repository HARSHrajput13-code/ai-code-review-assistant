"""An in-process Ollama for provider tests: httpx.MockTransport, scripted replies (CIS §20.2)."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ai.ollama.provider import OllamaAIReviewProvider, OllamaOptions
from ai.prompts.renderer import load_prompts
from shared.domain.enums import Language

MODEL = "qwen-test:4b-q4"
REVIEW = {
    "summary": "One issue was found.",
    "issues": [
        {
            "severity": "MEDIUM",
            "category": "CORRECTNESS",
            "title": "Mutable default argument",
            "line": 1,
            "end_line": 1,
            "evidence": "def f(items=[]):",
            "summary": "The default list is shared between calls.",
            "impact": "Calls can see each other's items.",
            "recommendation": "Default to None and create the list inside.",
            "related_static_ids": [],
        }
    ],
}
IMPROVEMENT = {"improved_code": "def f(items=None):\n    return items or []\n", "notes": ["Fixed."]}


def envelope(
    content: object = REVIEW, done_reason: object = "stop", **fields: Any
) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    reply: dict[str, Any] = {
        "model": MODEL,
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": done_reason,
        "prompt_eval_count": 900,
        "eval_count": 120,
        "total_duration": 5_000_000,
    }
    return reply | fields


Reply = httpx.Response | dict[str, Any] | Exception | Callable[[httpx.Request], Any]


@dataclass
class Ollama:
    """Answers /api/chat from `chat` in order (the last repeats) and /api/tags from `tags`."""

    chat: list[Reply] = field(default_factory=lambda: [envelope()])
    tags: Reply = field(default_factory=lambda: {"models": [{"name": MODEL, "digest": "ab" * 32}]})
    requests: list[httpx.Request] = field(default_factory=list)
    delay_s: float = 0.0

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/api/chat"]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/tags":
            reply = self.tags
        else:
            reply = self.chat.pop(0) if len(self.chat) > 1 else self.chat[0]
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if callable(reply) and not isinstance(reply, httpx.Response):
            reply = reply(request)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handle), base_url="http://127.0.0.1:11434"
        )


def provider(
    server: Ollama, nonce: Callable[[], str] | None = None, **options: Any
) -> OllamaAIReviewProvider:
    extra = {"nonce": nonce} if nonce else {}
    return OllamaAIReviewProvider(
        server.client(),
        OllamaOptions(**({"model": MODEL} | options)),
        load_prompts("v1"),
        {Language.PYTHON: "Python"},
        **extra,
    )
