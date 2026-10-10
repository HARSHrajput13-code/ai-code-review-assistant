"""Composition root: builds the concrete objects from Settings, once (CIS §3, §16.1). Wiring only.

Until the SQLite repository (PR-10) exists, PERSISTENCE_ENABLED=true is rejected at startup rather
than started half-wired.
"""

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

import httpx

from ai.fake import FakeAIReviewProvider
from ai.ollama.provider import OllamaAIReviewProvider, OllamaOptions
from ai.prompts.renderer import (
    PromptAssetError,
    PromptSet,
    load_prompts,
    output_schemas,
    render_system,
    schema_text,
)
from analysis.process import SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.api.routes import ApiContext
from backend.application.deadline import Clock, SystemClock
from backend.application.improvement import ImprovementOperation
from backend.application.job_service import ReviewJobService, ReviewRunner
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.readiness import ReadinessService
from backend.application.validation import SubmissionValidator
from backend.config import (
    AIProviderName,
    AppEnv,
    ConfigurationError,
    Settings,
    check_context_budget,
    parent_environment,
)
from shared.domain.enums import Language
from shared.domain.interfaces import AIReviewProvider, LanguageAdapter, ProviderHealth

ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE_DISABLED = ProviderHealth(available=True, detail="disabled")


class StaticToolsAdapter(LanguageAdapter, Protocol):
    def health(self) -> ProviderHealth: ...


def project_version() -> str:
    """The version declared in pyproject.toml, the project's only version source."""
    with (ROOT / "pyproject.toml").open("rb") as file:
        version: str = tomllib.load(file)["project"]["version"]
    return version


def build_context(
    settings: Settings,
    *,
    clock: Clock | None = None,
    adapter: StaticToolsAdapter | None = None,
    provider: AIReviewProvider | None = None,
    runner: ReviewRunner | None = None,
) -> ApiContext:
    """Build every component. The keyword arguments replace components in tests (§20.2)."""
    if settings.persistence_enabled:
        raise ConfigurationError(
            "PERSISTENCE_ENABLED=true is not available yet (the SQLite repository is PR-10)"
        )
    prompts = prompt_set(settings)

    clock = clock or SystemClock()
    adapter = adapter or PythonLanguageAdapter(
        SafeProcessRunner(parent_environment()),
        StaticAnalysisOptions(
            pylint_enabled=settings.static_pylint_enabled,
            bandit_enabled=settings.static_bandit_enabled,
            tool_timeout_s=settings.static_tool_timeout_seconds,
        ),
    )
    language_names = {adapter.language: adapter.display_name}
    # §9.7 startup check, on the measured rendered system prompts (the largest per language).
    check_context_budget(settings, *system_prompt_bytes(prompts, language_names.values()))
    close = None
    if provider is None and settings.ai_provider is AIProviderName.OLLAMA:
        client = httpx.AsyncClient(base_url=settings.ollama_base_url)  # one, shared (§9.3)
        provider, close = ollama_provider(settings, client, prompts, language_names), client.aclose
    provider = provider or FakeAIReviewProvider(
        retry_count=settings.ai_output_retry_count,
        seed=settings.ollama_seed,
        attempt_limit_s=settings.ollama_timeout_seconds,
        prompt_version=settings.ai_prompt_version,
        prompts=prompts,
        language_names=language_names,
        num_ctx=settings.ollama_num_ctx,
        num_predict=settings.ollama_num_predict,
    )
    registry = LanguageRegistry([adapter])
    improver = ImprovementOperation(provider, registry, clock, settings.max_source_bytes)
    orchestrator = ReviewOrchestrator(
        provider,
        clock,
        OrchestratorOptions(improvement_enabled=settings.improvement_enabled),
        improver,
    )
    jobs = ReviewJobService(
        InMemoryReviewJobStore(
            max_active=settings.max_active_reviews,
            max_retained=settings.review_max_retained,
            ttl_seconds=settings.review_result_ttl_seconds,
        ),
        SubmissionValidator(registry, settings.max_source_bytes, settings.max_source_lines),
        runner or orchestrator,
        clock,
        max_concurrent=settings.review_max_concurrent,
        review_timeout_seconds=settings.review_timeout_seconds,
    )
    return ApiContext(
        jobs=jobs,
        readiness=ReadinessService(provider, adapter.health, lambda: PERSISTENCE_DISABLED),
        languages=((adapter.language, adapter.display_name),),
        max_source_bytes=settings.max_source_bytes,
        max_source_lines=settings.max_source_lines,
        review_timeout_seconds=settings.review_timeout_seconds,
        improvement_enabled=settings.improvement_enabled,
        version=project_version(),
        close=close,
    )


def prompt_set(settings: Settings) -> PromptSet:
    """The AI_PROMPT_VERSION assets, loaded once; a draft is refused in production (§10.1)."""
    try:
        prompts = load_prompts(settings.ai_prompt_version)
    except PromptAssetError as error:
        raise ConfigurationError(str(error)) from None
    if prompts.status == "draft" and settings.app_env is AppEnv.PRODUCTION:
        raise ConfigurationError(
            f"AI_PROMPT_VERSION {prompts.version} is a draft; drafts are not allowed in production"
        )
    return prompts


def system_prompt_bytes(prompts: PromptSet, language_names: Iterable[str]) -> tuple[int, int]:
    """The largest rendered review and improvement system prompts, in UTF-8 bytes."""
    names = tuple(language_names)
    sizes = {
        operation: max(
            len(render_system(prompts, operation, name, schema_text(schema)).encode("utf-8"))
            for name in names
        )
        for operation, schema in output_schemas().items()
    }
    return sizes["review"], sizes["improve"]


def ollama_provider(
    settings: Settings,
    client: httpx.AsyncClient,
    prompts: PromptSet,
    language_names: dict[Language, str],
) -> OllamaAIReviewProvider:
    if settings.ollama_model is None:  # Settings already requires it for AI_PROVIDER=ollama
        raise ConfigurationError("OLLAMA_MODEL is required when AI_PROVIDER=ollama")
    options = OllamaOptions(
        model=settings.ollama_model,
        model_digest=settings.ollama_model_digest,
        temperature=settings.ollama_temperature,
        seed=settings.ollama_seed,
        num_ctx=settings.ollama_num_ctx,
        num_predict=settings.ollama_num_predict,
        timeout_s=settings.ollama_timeout_seconds,
        keep_alive=settings.ollama_keep_alive,
        retry_count=settings.ai_output_retry_count,
    )
    return OllamaAIReviewProvider(client, options, prompts, language_names)
