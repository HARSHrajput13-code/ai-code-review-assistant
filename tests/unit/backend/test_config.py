"""Configuration (CIS §16, §9.7 startup check, §20.3)."""

from pathlib import Path

import pytest

from backend.config import ConfigurationError, check_context_budget, load_settings

MODEL = {"OLLAMA_MODEL": "qwen3:4b-instruct-2507-q4_K_M"}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith(("APP_", "LOG_", "MAX_SOURCE", "REVIEW_", "STATIC_", "AI_", "OLLAMA_")):
            monkeypatch.delenv(key)
    for key in ("IMPROVEMENT_ENABLED", "PERSISTENCE_ENABLED", "SQLITE_PATH"):
        monkeypatch.delenv(key, raising=False)


def load(monkeypatch: pytest.MonkeyPatch, **env: str):  # type: ignore[no-untyped-def]
    for key, value in {**MODEL, **env}.items():
        monkeypatch.setenv(key, value)
    return load_settings(None)


def test_defaults_are_valid_and_pass_the_budget_check(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load(monkeypatch)
    assert (settings.max_source_bytes, settings.max_source_lines) == (12_000, 500)
    assert (settings.ollama_num_ctx, settings.ollama_num_predict) == (16_384, 8_192)
    assert (settings.ollama_temperature, settings.ollama_seed) == (0.0, 42)
    assert (settings.review_timeout_seconds, settings.ollama_timeout_seconds) == (300, 180)
    check_context_budget(settings, 9_000, 9_000)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("APP_PORT", "80"),
        ("AI_OUTPUT_RETRY_COUNT", "2"),
        ("MAX_SOURCE_BYTES", "999"),
        ("OLLAMA_MODEL_DIGEST", "XYZ"),
        ("OLLAMA_KEEP_ALIVE", "forever"),
        ("REVIEW_MAX_CONCURRENT", "5"),
        ("APP_ENV", "staging"),
    ],
)
def test_each_invalid_key_is_reported_by_name(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    with pytest.raises(ConfigurationError, match=key):
        load(monkeypatch, **{key: value})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AI_PROVIDER": "fake", "APP_ENV": "production"}, "AI_PROVIDER=fake"),
        ({"OLLAMA_BASE_URL": "http://10.0.0.5:11434"}, "AI_ALLOW_REMOTE_ENDPOINT"),
        ({"OLLAMA_BASE_URL": "http://user:pw@127.0.0.1:11434"}, "credentials"),
        ({"APP_HOST": "0.0.0.0"}, "APP_ALLOW_NON_LOOPBACK"),  # noqa: S104
    ],
)
def test_cross_field_rules(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigurationError, match=message):
        load(monkeypatch, **env)


def test_remote_endpoint_is_allowed_with_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    load(monkeypatch, OLLAMA_BASE_URL="https://ai.example.com", AI_ALLOW_REMOTE_ENDPOINT="true")


def test_ollama_model_is_required_for_ollama_only(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError, match="OLLAMA_MODEL"):
        load_settings(None)
    monkeypatch.setenv("AI_PROVIDER", "fake")
    assert load_settings(None).ollama_model is None


def test_removed_payload_logging_key_fails_from_environment_or_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOG_DEBUG_PAYLOADS", "true")
    with pytest.raises(ConfigurationError, match="LOG_DEBUG_PAYLOADS was removed"):
        load(monkeypatch)
    monkeypatch.delenv("LOG_DEBUG_PAYLOADS")
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_DEBUG_PAYLOADS=false\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="LOG_DEBUG_PAYLOADS was removed"):
        load_settings(env_file)


def test_unknown_keys_in_the_env_file_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_MODEL=m\nNOT_A_SETTING=1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="NOT_A_SETTING"):
        load_settings(env_file)


def test_v02_source_limits_fail_the_budget_check_with_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load(monkeypatch, MAX_SOURCE_BYTES="20000", MAX_SOURCE_LINES="800")
    with pytest.raises(ConfigurationError, match="19575"):
        check_context_budget(settings, 9_000, 9_000)


def test_num_predict_below_the_largest_output_reserve_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigurationError, match="OLLAMA_NUM_PREDICT"):
        check_context_budget(load(monkeypatch, OLLAMA_NUM_PREDICT="5000"), 9_000, 9_000)


def test_short_overall_timeout_with_long_stage_limits_starts_without_warning(
    monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    settings = load(
        monkeypatch,
        REVIEW_TIMEOUT_SECONDS="30",
        STATIC_TOOL_TIMEOUT_SECONDS="120",
        OLLAMA_TIMEOUT_SECONDS="900",
    )
    assert settings.review_timeout_seconds == 30
    assert len(recwarn) == 0
