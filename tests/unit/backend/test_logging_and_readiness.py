"""Structured logging (CIS §19.1, D-55) and the readiness service (§6.5, D-54)."""

import asyncio
import json
import logging
import sys
from typing import Any

import pytest

from ai.fake import FakeAIReviewProvider
from backend.application import readiness
from backend.application.readiness import ReadinessService
from backend.config import AIProviderName, LogFormat, LogLevel, Settings
from backend.logging_setup import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
    request_context,
)
from shared.domain.interfaces import ProviderHealth

OK = ProviderHealth(available=True, detail="ok")


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, ai_provider=AIProviderName.FAKE, **overrides)  # type: ignore[call-arg]


def record(message: str = "review.accepted", **extra: Any) -> logging.LogRecord:
    made = logging.LogRecord("backend.api", logging.INFO, __file__, 1, message, None, None)
    made.__dict__.update(extra)
    return made


def test_json_records_carry_whitelisted_metadata_and_the_request_id() -> None:
    with request_context("request-id-0001"):
        line = JsonFormatter().format(record(review_id="r1", replayed=False, body="x = 1"))
    found = json.loads(line)
    assert found["ts"].endswith("Z")
    assert {k: found[k] for k in ("level", "logger", "event", "request_id", "review_id")} == {
        "level": "INFO", "logger": "backend.api", "event": "review.accepted",
        "request_id": "request-id-0001", "review_id": "r1",
    }  # fmt: skip
    assert found["replayed"] is False
    assert "body" not in found  # not whitelisted, so never logged


def test_exceptions_are_rendered_without_message_or_locals() -> None:
    sentinel = "SENTINEL_7F91 user input"
    try:
        raise ValueError(sentinel)
    except ValueError:
        failed = record("request.failed")
        failed.exc_info = sys.exc_info()
    for formatter in (JsonFormatter(), ConsoleFormatter()):
        text = formatter.format(failed)
        assert "ValueError" in text and "test_logging_and_readiness.py" in text
        assert "SENTINEL" not in text


def test_the_console_format() -> None:
    assert (
        ConsoleFormatter()
        .format(record(stage="ANALYZING"))
        .endswith("INFO backend.api review.accepted stage=ANALYZING")
    )


def test_configure_logging_installs_one_handler_and_follows_settings() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging(settings(log_level=LogLevel.WARNING, log_format=LogFormat.CONSOLE))
        configure_logging(settings(log_level=LogLevel.DEBUG))
        ours = [h for h in root.handlers if h.get_name() == "ai-code-review-assistant"]
        assert len(ours) == 1 and isinstance(ours[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
    finally:
        root.handlers[:] = before


class Hanging(FakeAIReviewProvider):
    async def check_health(self, timeout_s: float) -> ProviderHealth:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_a_slow_ai_check_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness, "AI_HEALTH_TIMEOUT_S", 0.001)
    found = asyncio.run(ReadinessService(Hanging(), lambda: OK, lambda: OK).check())
    assert found.ai_provider == readiness.AI_CHECK_FAILED and not found.ready


def test_ready_only_when_every_component_is_available() -> None:
    down = ProviderHealth(available=False, detail="down")
    service = ReadinessService(FakeAIReviewProvider(), lambda: OK, lambda: down)
    found = asyncio.run(service.check())
    assert (found.ai_provider.detail, found.ready) == ("fake provider", False)
