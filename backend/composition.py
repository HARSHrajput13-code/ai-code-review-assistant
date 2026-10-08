"""Composition root: builds the concrete objects from Settings, once (CIS §3, §16.1). Wiring only.

Until the Ollama provider (PR-06) and the SQLite repository (PR-10) exist, a configuration that
needs them is rejected at startup rather than started half-wired.
"""

import tomllib
from pathlib import Path
from typing import Protocol

from ai.budget import SYSTEM_PROMPT_MAX_BYTES
from ai.fake import FakeAIReviewProvider
from analysis.process import SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.api.routes import ApiContext
from backend.application.deadline import Clock, SystemClock
from backend.application.job_service import ReviewJobService, ReviewRunner
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.readiness import ReadinessService
from backend.application.validation import SubmissionValidator
from backend.config import (
    AIProviderName,
    ConfigurationError,
    Settings,
    check_context_budget,
    parent_environment,
)
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
    if settings.ai_provider is not AIProviderName.FAKE:
        raise ConfigurationError(
            "AI_PROVIDER=ollama is not available yet (the Ollama provider is PR-06); "
            "use AI_PROVIDER=fake with APP_ENV=development or test"
        )
    if settings.persistence_enabled:
        raise ConfigurationError(
            "PERSISTENCE_ENABLED=true is not available yet (the SQLite repository is PR-10)"
        )
    # §9.7 startup check. Until PR-06 measures the rendered system prompts, the largest allowed
    # size (SYSTEM_PROMPT_MAX_BYTES) is used, so any real prompt within the cap also fits.
    check_context_budget(settings, SYSTEM_PROMPT_MAX_BYTES, SYSTEM_PROMPT_MAX_BYTES)

    clock = clock or SystemClock()
    adapter = adapter or PythonLanguageAdapter(
        SafeProcessRunner(parent_environment()),
        StaticAnalysisOptions(
            pylint_enabled=settings.static_pylint_enabled,
            bandit_enabled=settings.static_bandit_enabled,
            tool_timeout_s=settings.static_tool_timeout_seconds,
        ),
    )
    provider = provider or FakeAIReviewProvider(
        retry_count=settings.ai_output_retry_count,
        seed=settings.ollama_seed,
        attempt_limit_s=settings.ollama_timeout_seconds,
        prompt_version=settings.ai_prompt_version,
    )
    registry = LanguageRegistry([adapter])
    # No Improver until PR-07: improvement reports SKIPPED(DISABLED) (§14.5).
    orchestrator = ReviewOrchestrator(
        provider, clock, OrchestratorOptions(improvement_enabled=settings.improvement_enabled)
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
        improvement_enabled=False,  # no improvement operation until PR-07
        version=project_version(),
    )
