"""Capabilities, health, readiness, docs and startup (CIS §6.2, §6.5, §9.5, §16, D-31, D-54)."""

import asyncio
import logging
from typing import Any

import pytest

import backend.main
from ai.fake import FakeAIReviewProvider
from ai.ollama.provider import OllamaAIReviewProvider
from backend.composition import build_context, project_version
from backend.config import AIProviderName, AppEnv, ConfigurationError
from backend.main import create_app
from shared.domain.interfaces import ProviderHealth
from tests.unit.backend.api_client import Api, settings
from tests.unit.backend.api_client import scenario as run
from tests.unit.backend.pipeline_doubles import StubAdapter

MISSING_TOOL = ProviderHealth(available=False, detail="bandit not installed")


class UnavailableProvider(FakeAIReviewProvider):
    async def check_health(self, timeout_s: float) -> ProviderHealth:
        return ProviderHealth(available=False, detail="Configured model is not installed")


class BrokenProvider(FakeAIReviewProvider):
    async def check_health(self, timeout_s: float) -> ProviderHealth:
        raise RuntimeError(r"C:\secret\path\ollama.py exploded with SENTINEL_7F91")


@pytest.mark.parametrize("enabled", [True, False])
def test_capabilities_come_from_configuration(enabled: bool) -> None:
    async def body(api: Api) -> None:
        response = await api.client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "languages": [{"id": "python", "display_name": "Python", "monaco_language": "python"}],
            "limits": {"max_source_bytes": 4000, "max_source_lines": 200},
            "review_timeout_seconds": 120,
            "improvement_enabled": enabled,  # IMPROVEMENT_ENABLED (§16)
        }

    run(
        body,
        max_source_bytes=4000,
        max_source_lines=200,
        review_timeout_seconds=120,
        improvement_enabled=enabled,
    )


def test_health_is_liveness_only() -> None:
    async def body(api: Api) -> None:
        response = await api.client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": project_version()}

    run(body, provider=BrokenProvider(), adapter=StubAdapter(tools_health=MISSING_TOOL))


def test_readiness_with_the_fake_provider() -> None:
    async def body(api: Api) -> None:
        response = await api.client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "components": {
                "ai_provider": {"available": True, "detail": "fake provider"},
                "static_tools": {"available": True, "detail": "pylint 4.1.2, bandit 1.9.4"},
                "persistence": {"available": True, "detail": "disabled"},
            },
        }

    run(body)


@pytest.mark.parametrize(
    ("provider", "adapter", "component", "detail"),
    [
        (UnavailableProvider(), None, "ai_provider", "Configured model is not installed"),
        (BrokenProvider(), None, "ai_provider", "AI provider check failed"),
        (None, StubAdapter(tools_health=MISSING_TOOL), "static_tools", "bandit not installed"),
    ],
)
def test_degraded_readiness_is_503(
    provider: Any, adapter: Any, component: str, detail: str
) -> None:
    async def body(api: Api) -> None:
        response = await api.client.get("/api/v1/health/ready")
        assert response.status_code == 503
        found = response.json()
        assert found["status"] == "degraded"
        assert found["components"][component] == {"available": False, "detail": detail}
        assert "secret" not in response.text and "SENTINEL" not in response.text
        assert ".py" not in response.text

    run(body, provider=provider, adapter=adapter)


def test_docs_only_outside_production() -> None:
    async def body(api: Api) -> None:
        assert (await api.client.get("/docs")).status_code == 200
        assert (await api.client.get("/openapi.json")).status_code == 200

    run(body)

    production = settings(
        app_env=AppEnv.PRODUCTION, ai_provider=AIProviderName.OLLAMA, ollama_model="m"
    )

    async def hidden() -> None:
        import httpx

        context = build_context(settings(), adapter=StubAdapter())
        app = create_app(production, context)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            assert (await client.get("/docs")).status_code == 404
            assert (await client.get("/openapi.json")).status_code == 404
            assert (await client.get("/api/v1/health")).status_code == 200

    asyncio.run(hidden())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"persistence_enabled": True}, "PR-10"),
        ({"ollama_num_predict": 4000}, "OLLAMA_NUM_PREDICT"),  # the §9.7 startup check
        ({"ai_prompt_version": "v99"}, "missing an asset"),  # §10.1: a missing file fails
        (  # §10.1: a draft prompt version is refused in production
            {
                "app_env": AppEnv.PRODUCTION,
                "ai_provider": AIProviderName.OLLAMA,
                "ollama_model": "m",
            },
            "draft",
        ),
    ],
)
def test_startup_rejects_what_cannot_run(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        build_context(settings(**overrides))


def test_ai_provider_ollama_builds_the_ollama_provider_and_closes_its_client() -> None:
    async def body() -> None:
        ollama = settings(ai_provider=AIProviderName.OLLAMA, ollama_model="m")
        context = build_context(ollama, adapter=StubAdapter())
        provider = context.readiness._provider
        assert isinstance(provider, OllamaAIReviewProvider)
        assert provider.descriptor.model == "m" and provider.descriptor.prompt_version == "v1"
        client = provider._client
        app = create_app(ollama, context)
        async with app.router.lifespan_context(app):  # Ollama is not running: degraded only
            assert not client.is_closed
        assert client.is_closed

    asyncio.run(body())


def test_the_default_composition_uses_the_real_adapter_and_fake_provider() -> None:
    context = build_context(settings())
    health = context.readiness._static_tools()
    assert health.available and "pylint 4.1.2" in health.detail and "bandit 1.9.4" in health.detail


def test_lifespan_probes_readiness_and_shuts_down(caplog: pytest.LogCaptureFixture) -> None:
    async def body() -> None:
        context = build_context(
            settings(),
            adapter=StubAdapter(tools_health=MISSING_TOOL),
            provider=FakeAIReviewProvider(),
        )
        app = create_app(settings(), context)
        async with app.router.lifespan_context(app):
            pass

    with caplog.at_level(logging.WARNING):
        asyncio.run(body())
    assert "startup.readiness_degraded" in caplog.text


def test_app_is_created_on_first_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.chdir(backend.main.__file__.rsplit("backend", 1)[0] + "tests")  # no .env here
    assert backend.main.app.title == "AI Code Review Assistant"
    with pytest.raises(AttributeError):
        _ = backend.main.not_an_attribute
