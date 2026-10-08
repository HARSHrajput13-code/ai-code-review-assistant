"""In-process API harness (CIS §20.2): httpx.ASGITransport, FakeClock, the fake AI. No server."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI

from ai.fake import FakeAIReviewProvider
from backend.api.routes import ApiContext
from backend.composition import build_context
from backend.config import AIProviderName, AppEnv, Settings
from backend.main import create_app
from tests.unit.backend.pipeline_doubles import StubAdapter
from tests.unit.builders import FakeClock

CODE = "import os\n\n\ndef f(items=[]):\n    return items\n"
KEY = "key-0123456789abcdef"
REVIEWS = "/api/v1/reviews"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": AppEnv.TEST, "ai_provider": AIProviderName.FAKE}
    return Settings(_env_file=None, **{**values, **overrides})  # type: ignore[call-arg]


@dataclass
class Api:
    app: FastAPI
    context: ApiContext
    clock: FakeClock
    client: httpx.AsyncClient

    async def submit(
        self, code: str = CODE, key: str | None = None, language: str = "python"
    ) -> httpx.Response:
        headers = {"content-type": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        # ensure_ascii escapes a lone surrogate as \ud800, as a JSON client would send it.
        body = json.dumps({"language": language, "source_code": code}, ensure_ascii=True)
        return await self.client.post(REVIEWS, content=body.encode(), headers=headers)


def scenario(
    test: Callable[[Api], Awaitable[None]],
    *,
    adapter: Any = None,
    provider: Any = None,
    runner: Any = None,
    **overrides: Any,
) -> None:
    async def body() -> None:
        clock = FakeClock()
        configured = settings(**overrides)
        context = build_context(
            configured,
            clock=clock,
            adapter=adapter or StubAdapter(),
            provider=provider or FakeAIReviewProvider(),
            runner=runner,
        )
        app = create_app(configured, context)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await test(Api(app, context, clock, client))
        await context.jobs.shutdown()

    asyncio.run(body())


def error_of(response: httpx.Response) -> dict[str, Any]:
    """The §6.6 error body, checked for its exact shape and the echoed request ID."""
    body = response.json()
    assert set(body) == {"error"}
    error: dict[str, Any] = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"] == response.headers["X-Request-ID"]
    return error
