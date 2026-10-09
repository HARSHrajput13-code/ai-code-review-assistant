"""Live Ollama smoke test (CIS §20.1): opt-in, never part of the default run or CI.

    RUN_LIVE_LLM=1 OLLAMA_MODEL=<installed tag> uv run pytest -m live_llm

It checks only that the provider works through the unchanged API and pipeline with a real local
model. It is not an evaluation: it measures no quality, compares no models and selects nothing
(model selection is the PR-08 procedure, §20.9).
"""

import asyncio
import os
from typing import Any

import httpx
import pytest

from backend.composition import build_context
from backend.config import AIProviderName, AppEnv, Settings
from backend.main import create_app

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_LLM") != "1" or not os.environ.get("OLLAMA_MODEL"),
        reason="needs RUN_LIVE_LLM=1, OLLAMA_MODEL and a running Ollama",
    ),
]

SOURCE = (
    "import subprocess\n\n\n"
    "def run(command, items=[]):\n"
    "    items.append(command)\n"
    "    return subprocess.call(command, shell=True)\n"
)


def live_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=AppEnv.DEVELOPMENT,  # the v1 prompts are a draft (§10.1)
        ai_provider=AIProviderName.OLLAMA,
        ollama_model=os.environ["OLLAMA_MODEL"],
    )


def run_review() -> tuple[dict[str, Any], dict[str, Any]]:
    async def body() -> tuple[dict[str, Any], dict[str, Any]]:
        settings = live_settings()
        context = build_context(settings)
        app = create_app(settings, context)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            ready = (await client.get("/api/v1/health/ready")).json()
            created = await client.post(
                "/api/v1/reviews",
                json={"language": "python", "source_code": SOURCE},
                headers={"Idempotency-Key": "live-smoke-key-0001"},
            )
            await context.jobs.wait_idle()
            done = (await client.get(created.headers["Location"])).json()
            return ready, done

    return asyncio.run(body())


def test_a_review_runs_through_the_pipeline_with_the_local_model() -> None:
    ready, done = run_review()
    assert ready["components"]["ai_provider"]["available"] is True
    assert done["status"] == "COMPLETED", done.get("failure") or done["result"]["analysis"]
    result = done["result"]
    assert result["analysis"]["ai_analysis"]["status"] == "SUCCEEDED"
    metadata = result["metadata"]
    assert (metadata["ai_provider"], metadata["ai_model"], metadata["prompt_version"]) == (
        "ollama", os.environ["OLLAMA_MODEL"], "v1",
    )  # fmt: skip
    assert result["summary"]["text"]
