"""Settings: the only reader of environment variables (CIS §16, §3).

Other components receive typed values through their constructors. Invalid configuration fails
with a readable error listing each invalid key (BS §31). Checks that need the prompt assets
(the AI_PROMPT_VERSION MANIFEST, and the measured system-prompt sizes for the budget check) take
those values as inputs; the assets are loaded in PR-06.
"""

import ipaddress
import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai.budget import startup_budget_problems

REMOVED_KEY = "LOG_DEBUG_PAYLOADS"
REMOVED_MESSAGE = "LOG_DEBUG_PAYLOADS was removed; payload logging is not supported"


class ConfigurationError(ValueError):
    pass


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class AIProviderName(StrEnum):
    OLLAMA = "ollama"
    FAKE = "fake"


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="forbid", frozen=True
    )

    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_host: str = "127.0.0.1"
    app_allow_non_loopback: bool = False
    app_port: Annotated[int, Field(ge=1024, le=65535)] = 8000
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON
    max_source_bytes: Annotated[int, Field(ge=1000, le=100_000)] = 12_000
    max_source_lines: Annotated[int, Field(ge=10, le=5000)] = 500
    review_timeout_seconds: Annotated[int, Field(ge=30, le=1800)] = 300
    review_max_concurrent: Annotated[int, Field(ge=1, le=4)] = 1
    review_max_queued: Annotated[int, Field(ge=0, le=10)] = 2
    review_result_ttl_seconds: Annotated[int, Field(ge=60, le=86_400)] = 900
    review_max_retained: Annotated[int, Field(ge=1, le=1000)] = 50
    static_tool_timeout_seconds: Annotated[int, Field(ge=5, le=120)] = 30
    static_pylint_enabled: bool = True
    static_bandit_enabled: bool = True
    improvement_enabled: bool = True
    ai_provider: AIProviderName = AIProviderName.OLLAMA
    ai_prompt_version: Annotated[str, Field(pattern=r"^v[0-9]+$")] = "v1"
    ai_output_retry_count: Annotated[int, Field(ge=0, le=1)] = 1
    ai_allow_remote_endpoint: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{1,128}$")] | None = None
    ollama_model_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{12,64}$")] | None = None
    ollama_temperature: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    ollama_seed: Annotated[int, Field(ge=0)] = 42
    ollama_num_ctx: Annotated[int, Field(ge=2048, le=131_072)] = 16_384
    ollama_num_predict: Annotated[int, Field(ge=512, le=32_768)] = 8192
    ollama_timeout_seconds: Annotated[int, Field(ge=10, le=900)] = 180
    ollama_keep_alive: Annotated[str, Field(pattern=r"^(\d+[smh]|-1)$")] = "10m"
    persistence_enabled: bool = False
    sqlite_path: Path = Path("./data/reviews.sqlite3")

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Self:
        problems = []
        if not self.app_allow_non_loopback and not _is_loopback(self.app_host):
            problems.append("APP_HOST must be loopback unless APP_ALLOW_NON_LOOPBACK=true")
        url = urlsplit(self.ollama_base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            problems.append("OLLAMA_BASE_URL must be an http or https URL")
        elif url.username or url.password:
            problems.append("OLLAMA_BASE_URL must not contain credentials")
        elif not _is_loopback(url.hostname) and not self.ai_allow_remote_endpoint:
            problems.append("OLLAMA_BASE_URL is remote; set AI_ALLOW_REMOTE_ENDPOINT=true")
        if self.ai_provider is AIProviderName.FAKE and self.app_env is AppEnv.PRODUCTION:
            problems.append("AI_PROVIDER=fake is allowed only in development and test")
        if self.ai_provider is AIProviderName.OLLAMA and self.ollama_model is None:
            problems.append("OLLAMA_MODEL is required when AI_PROVIDER=ollama")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def max_active_reviews(self) -> int:
        return self.review_max_concurrent + self.review_max_queued


def parent_environment() -> dict[str, str]:
    """The process environment for SafeProcessRunner, which keeps only its allow-list (§8.6)."""
    return dict(os.environ)


def _env_file_defines(env_file: Path, key: str) -> bool:
    if not env_file.is_file():
        return False
    pattern = re.compile(rf"^\s*(export\s+)?{key}\s*=")
    return any(pattern.match(line) for line in env_file.read_text(encoding="utf-8").splitlines())


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Load and validate once (in create_app). Raises ConfigurationError naming each bad key."""
    if REMOVED_KEY in os.environ or (env_file and _env_file_defines(Path(env_file), REMOVED_KEY)):
        raise ConfigurationError(REMOVED_MESSAGE)
    try:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as error:
        lines = []
        for problem in error.errors():
            key = ".".join(str(part) for part in problem["loc"]).upper() or "configuration"
            lines.append(f"{key}: {problem['msg']}")
        raise ConfigurationError("Invalid configuration:\n" + "\n".join(lines)) from None


def check_context_budget(
    settings: Settings, review_system_bytes: int, improve_system_bytes: int
) -> None:
    """§9.7 startup validation with the measured rendered system prompts."""
    problems = startup_budget_problems(
        num_ctx=settings.ollama_num_ctx,
        num_predict=settings.ollama_num_predict,
        max_source_bytes=settings.max_source_bytes,
        max_source_lines=settings.max_source_lines,
        review_system_bytes=review_system_bytes,
        improve_system_bytes=improve_system_bytes,
    )
    if problems:
        raise ConfigurationError("Context budget exceeded: " + "; ".join(problems))
