"""The improvement operation (CIS §14, §7.2 stages 10-11, §9.7, §19.1, §20.3).

Stage 10 goes through a real provider: the fake (§9.6) or the Ollama adapter over
httpx.MockTransport. Stage 11 uses the real §14.3 steps and the real bounded parse and interface
check. No test needs Ollama, and no generated code is executed.
"""

import asyncio
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from ai.fake import (
    CONTEXT_EXCEEDED,
    INVALID_JSON,
    TIMEOUT,
    UNAVAILABLE,
    FakeAIReviewProvider,
    FakeResponse,
)
from analysis.registry import LanguageRegistry
from backend.application.improvement import ImprovementOperation
from backend.logging_setup import JsonFormatter
from backend.review.improvement import EMPTY_OR_INVALID_TEXT, TOO_LARGE, UNCHANGED
from shared.domain.enums import Category, Confidence, ErrorCode, Severity
from shared.domain.errors import (
    AIContextExceeded,
    AIProviderTimeout,
    AIProviderUnavailable,
    AIResponseInvalid,
    ImprovedCodeInvalid,
)
from shared.domain.interfaces import AIImprovementRequest
from shared.domain.models import CodeValidation, SourceText
from tests.unit.ai.ollama_doubles import IMPROVEMENT, Ollama, envelope
from tests.unit.ai.ollama_doubles import provider as ollama
from tests.unit.backend.pipeline_doubles import SOURCE, SUBMISSION, StubAdapter
from tests.unit.builders import FakeClock, ScriptedDeadline, issue

MARK = "IMPROVED_SENTINEL_51C3"
ISSUES = (
    issue(Category.CORRECTNESS, Severity.MEDIUM, Confidence.HIGH, line=4),
    issue(Category.MAINTAINABILITY, Severity.LOW, Confidence.HIGH, line=1),
)
GOOD = "import os\n\n\ndef f(items=None):\n    return items or []\n"
DEADLINE = 300.0


def answer(code: str, notes: tuple[str, ...] = ()) -> FakeResponse:
    return FakeResponse(content=json.dumps({"improved_code": code, "notes": list(notes)}))


class Log:
    def __init__(self, stream: io.StringIO) -> None:
        self.stream = stream

    @property
    def records(self) -> list[dict[str, Any]]:
        lines = self.stream.getvalue().splitlines()
        return [r for r in map(json.loads, lines) if r["logger"].startswith("backend.")]

    def stage(self, stage: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("stage") == stage]


@pytest.fixture
def log() -> Iterator[Log]:
    """Every record, at DEBUG, as the production JSON formatter writes it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield Log(stream)
    root.removeHandler(handler)
    root.setLevel(previous)


def operation(
    provider: Any, adapter: Any = None, clock: FakeClock | None = None, max_bytes: int = 12_000
) -> ImprovementOperation:
    registry = LanguageRegistry([adapter or StubAdapter()])
    return ImprovementOperation(provider, registry, clock or FakeClock(), max_bytes)


def run(op: ImprovementOperation, issues: Any = ISSUES) -> Any:
    return asyncio.run(op.improve(SUBMISSION, issues, ScriptedDeadline([DEADLINE])))


# --- success --------------------------------------------------------------------------------


def test_a_valid_candidate_is_available_with_its_notes(log: Log) -> None:
    provider = FakeAIReviewProvider(improve_script=[answer(GOOD, ("Avoids a shared default.",))])
    outcome, improved = run(operation(provider))
    assert (outcome.status, outcome.error_code) == ("SUCCEEDED", None)
    assert (improved.status, improved.code, improved.notes) == (
        "AVAILABLE", GOOD, ("Avoids a shared default.",),
    )  # fmt: skip
    assert (improved.failure_code, improved.message) == (None, None)
    (call,) = provider.calls  # one improve call, with exactly the issues it was given (§14.1)
    assert (call.operation, call.attempt, call.seed) == ("improve", 1, 42)
    assert isinstance(call.request, AIImprovementRequest)
    assert call.request.source == SOURCE and call.request.issues == ISSUES
    assert "<<<ISSUES_" in call.prompt.user and "<<<SOURCE_" in call.prompt.user
    started, finished = log.stage("IMPROVEMENT_VALIDATION")
    assert (started["event"], finished["event"]) == (
        "review.stage.started",
        "review.stage.finished",
    )
    assert finished["status"] == "SUCCEEDED" and "rejection_reason" not in finished


def test_the_fakes_default_candidate_passes_the_real_checks() -> None:
    _, improved = run(operation(FakeAIReviewProvider()))
    assert improved.code == "# Reviewed\n" + SOURCE.text


def test_a_fenced_crlf_candidate_is_returned_without_its_fence() -> None:
    fenced = "```python\r\n" + GOOD.replace("\n", "\r\n") + "```"
    _, improved = run(operation(FakeAIReviewProvider(improve_script=[answer(fenced)])))
    assert improved.code == GOOD


def test_durations_come_from_the_clock(log: Log) -> None:
    clock = FakeClock()

    class Slow(FakeAIReviewProvider):
        async def improve(self, request: Any, deadline: Any) -> Any:
            clock.advance(2.5)
            return await super().improve(request, deadline)

    outcome, _ = run(operation(Slow(), clock=clock))
    assert outcome.duration_ms == 2_500
    assert log.stage("IMPROVEMENT_VALIDATION")[1]["duration_ms"] == 0


def test_the_ollama_adapter_carries_the_improvement_request(log: Log) -> None:
    server = Ollama(chat=[envelope(IMPROVEMENT)])
    original = SourceText.of("def f(items=[]):\n    return items\n")
    op = operation(ollama(server))
    submission = SUBMISSION.model_copy(update={"source": original})
    outcome, improved = asyncio.run(
        op.improve(submission, ISSUES[:1], ScriptedDeadline([DEADLINE]))
    )
    assert outcome.status == "SUCCEEDED" and improved.code == IMPROVEMENT["improved_code"]
    (body,) = server.bodies
    assert body["think"] is False and body["stream"] is False
    assert body["format"]["required"] == ["improved_code", "notes"]
    assert "<<<ISSUES_" in body["messages"][1]["content"]


# --- stage 11 rejections (§14.3) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        ("   \n", EMPTY_OR_INVALID_TEXT),
        (GOOD + "\x00", EMPTY_OR_INVALID_TEXT),
        ("#" + "x" * 2_001 + "\n" + GOOD, TOO_LARGE),
        (SOURCE.text + "\n\n", UNCHANGED),
        (f"def f(items=None:\n    return '{MARK}'\n", "DOES_NOT_PARSE"),
        (f"import os\n\n\ndef g(items=None):\n    return '{MARK}'\n", "INTERFACE_CHANGED"),
    ],
    ids=["blank", "nul", "too-large", "unchanged", "does-not-parse", "interface-changed"],
)
def test_each_rejection_is_improved_code_invalid_with_its_reason_logged(
    log: Log, candidate: str, reason: str
) -> None:
    provider = FakeAIReviewProvider(improve_script=[answer(candidate, (MARK,))])
    with pytest.raises(ImprovedCodeInvalid) as error:
        run(operation(provider, max_bytes=1_000))
    assert (error.value.code, error.value.reason) == (ErrorCode.IMPROVED_CODE_INVALID, reason)
    assert len(provider.calls) == 1  # a validation failure is not retried
    started, finished = log.stage("IMPROVEMENT_VALIDATION")
    assert started["status"] == "RUNNING"
    assert (finished["status"], finished["error_code"], finished["rejection_reason"]) == (
        "FAILED", "IMPROVED_CODE_INVALID", reason,
    )  # fmt: skip
    assert finished["level"] == "WARNING" and "exception_type" not in finished
    assert MARK not in log.stream.getvalue()  # neither the code nor the notes are logged


def test_an_unparseable_original_skips_only_the_interface_check() -> None:
    broken = SUBMISSION.model_copy(update={"source": SourceText.of("def f(:\n    pass\n")})
    provider = FakeAIReviewProvider(improve_script=[answer("def g():\n    pass\n")])
    _, improved = asyncio.run(
        operation(provider).improve(broken, ISSUES, ScriptedDeadline([DEADLINE]))
    )
    assert improved.code == "def g():\n    pass\n"


class BrokenAdapter(StubAdapter):
    def validate_generated_code(self, original: SourceText, generated: str) -> CodeValidation:
        raise RuntimeError(f"{MARK} {generated}")


def test_an_unexpected_validation_error_is_improved_code_invalid(log: Log) -> None:
    with pytest.raises(ImprovedCodeInvalid):
        run(operation(FakeAIReviewProvider(), adapter=BrokenAdapter()))
    (_, finished) = log.stage("IMPROVEMENT_VALIDATION")
    assert (finished["status"], finished["error_code"]) == ("FAILED", "IMPROVED_CODE_INVALID")
    assert finished["level"] == "ERROR" and finished["exception_type"] == "RuntimeError"
    assert MARK not in log.stream.getvalue() and "Reviewed" not in log.stream.getvalue()


def test_a_submission_without_an_adapter_is_improved_code_invalid(log: Log) -> None:
    op = ImprovementOperation(FakeAIReviewProvider(), LanguageRegistry([]), FakeClock(), 12_000)
    with pytest.raises(ImprovedCodeInvalid):
        run(op)
    assert log.stage("IMPROVEMENT_VALIDATION")[1]["exception_type"] == "LookupError"


# --- stage 10: provider errors, retry, budget (§9.4, §9.7, D-41) ----------------------------


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (UNAVAILABLE, AIProviderUnavailable),
        (TIMEOUT, AIProviderTimeout),
        (CONTEXT_EXCEEDED, AIContextExceeded),
        (FakeResponse(done_reason="length"), AIContextExceeded),
    ],
    ids=["unavailable", "timeout", "context-exceeded", "length"],
)
def test_provider_errors_propagate_without_validation(
    log: Log, response: FakeResponse, error: type[Exception]
) -> None:
    provider = FakeAIReviewProvider(improve_script=[response])
    with pytest.raises(error):
        run(operation(provider))
    assert len(provider.calls) == 1  # none of these is retried (§9.4)
    assert log.stage("IMPROVEMENT_VALIDATION") == []  # no candidate: the orchestrator logs it


def test_a_schema_violation_is_retried_once_with_the_next_seed() -> None:
    provider = FakeAIReviewProvider(improve_script=[INVALID_JSON, answer(GOOD)])
    _, improved = run(operation(provider))
    assert improved.code == GOOD
    assert [(c.attempt, c.seed) for c in provider.calls] == [(1, 42), (2, 43)]


@pytest.mark.parametrize("retry_count", [1, 0])
def test_schema_violations_end_as_ai_output_invalid(log: Log, retry_count: int) -> None:
    provider = FakeAIReviewProvider(
        improve_script=[INVALID_JSON, INVALID_JSON], retry_count=retry_count
    )
    with pytest.raises(AIResponseInvalid):
        run(operation(provider))
    assert len(provider.calls) == retry_count + 1  # at most 2 calls (§7.3)
    assert log.stage("IMPROVEMENT_VALIDATION") == []


def test_no_retry_starts_with_under_20_seconds_left() -> None:
    provider = FakeAIReviewProvider(improve_script=[INVALID_JSON, answer(GOOD)])
    op = operation(provider)
    with pytest.raises(AIResponseInvalid):
        asyncio.run(op.improve(SUBMISSION, ISSUES, ScriptedDeadline([19.0])))
    assert len(provider.calls) == 1


def test_a_request_that_cannot_fit_makes_no_call() -> None:
    provider = FakeAIReviewProvider(num_ctx=2_048)  # the 1,024-token minimum output cannot fit
    with pytest.raises(AIContextExceeded):
        run(operation(provider))
    assert provider.calls == []


def test_the_ollama_adapter_sends_nothing_when_the_improvement_cannot_fit() -> None:
    server = Ollama(chat=[envelope(IMPROVEMENT)])
    with pytest.raises(AIContextExceeded):
        run(operation(ollama(server, num_ctx=2_048)))
    assert server.requests == []


def test_an_exhausted_deadline_sends_nothing() -> None:
    server = Ollama(chat=[envelope(IMPROVEMENT)])
    op = operation(ollama(server))
    with pytest.raises(AIProviderTimeout):
        asyncio.run(op.improve(SUBMISSION, ISSUES, ScriptedDeadline([0.0])))
    assert server.requests == []
