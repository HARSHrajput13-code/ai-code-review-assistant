"""OllamaAIReviewProvider over httpx.MockTransport (CIS §9.3–9.7, §20.3, D-41, D-65, D-77, D-90).

No test here needs a running Ollama: every response is scripted in process.
"""

import asyncio
import itertools
import json
from collections.abc import Awaitable
from typing import Any

import httpx
import pytest

from ai.budget import (
    CONTEXT_SAFETY_MARGIN_TOKENS,
    REVIEW_MIN_OUTPUT_TOKENS,
    est,
    improve_min_output_tokens,
)
from ai.ollama.provider import (
    DIGEST_MISMATCH,
    MAX_RESPONSE_BYTES,
    MODEL_ABSENT,
    MODEL_READY,
    NOT_REACHABLE,
    model_names,
)
from ai.prompts.renderer import schema_text
from ai.schemas import AIImprovementOutput, AIReviewOutput
from shared.domain.enums import Category, Confidence, ErrorCode, Language, Severity
from shared.domain.errors import ReviewError
from shared.domain.interfaces import AIImprovementRequest, AIReviewRequest
from shared.domain.metrics import AICallMetrics, collect_ai_metrics
from shared.domain.models import SourceText
from tests.unit.ai.ollama_doubles import IMPROVEMENT, MODEL, REVIEW, Ollama, envelope, provider
from tests.unit.ai.prompt_limits import worst_improvement, worst_review
from tests.unit.analysis.fakes import FixedDeadline
from tests.unit.builders import issue, static_finding

SENTINEL = "SOURCE_SENTINEL_5C2E"
SOURCE = SourceText.of(f"def f(items=[]):\n    return items  # {SENTINEL}\n")
REQUEST = AIReviewRequest(language=Language.PYTHON, source=SOURCE, static_findings=())
IMPROVE = AIImprovementRequest(
    language=Language.PYTHON,
    source=SOURCE,
    issues=(issue(Category.CORRECTNESS, Severity.MEDIUM, Confidence.HIGH, line=1),),
)


def call[T](operation: Awaitable[T]) -> tuple[T, AICallMetrics]:
    async def body() -> tuple[T, AICallMetrics]:
        with collect_ai_metrics() as metrics:
            return await operation, metrics

    return asyncio.run(body())


def review(server: Ollama, remaining: float = 300, **options: Any) -> Any:
    return call(provider(server, **options).review(REQUEST, FixedDeadline(remaining)))


def failure(server: Ollama, remaining: float = 300, **options: Any) -> tuple[ReviewError, Any]:
    async def body() -> tuple[ReviewError, AICallMetrics]:
        with collect_ai_metrics() as metrics:
            with pytest.raises(ReviewError) as caught:
                await provider(server, **options).review(REQUEST, FixedDeadline(remaining))
            return caught.value, metrics

    return asyncio.run(body())


# --- the request (§9.3) --------------------------------------------------------------------------


def test_the_chat_request_follows_the_adapter_contract() -> None:
    server = Ollama()
    result, metrics = review(server)
    (body,) = server.bodies
    assert (server.requests[0].method, server.requests[0].url.path) == ("POST", "/api/chat")
    assert body["model"] == MODEL and body["stream"] is False and body["think"] is False
    assert "tools" not in body and body["keep_alive"] == "10m"
    assert body["format"] == AIReviewOutput.model_json_schema()
    system, user = body["messages"]
    assert (system["role"], user["role"]) == ("system", "user")
    assert schema_text(AIReviewOutput.model_json_schema()) in system["content"]  # D-66
    assert "working on Python code" in system["content"]
    size = len(system["content"].encode()) + len(user["content"].encode())
    expected = min(8192, 16_384 - est(size) - CONTEXT_SAFETY_MARGIN_TOKENS)
    assert body["options"] == {
        "temperature": 0.0,
        "num_ctx": 16_384,
        "num_predict": expected,
        "seed": 42,
    }
    assert result.attempts == 1 and result.summary == "One issue was found."
    (found,) = result.candidates
    assert (found.severity, found.category) == (Severity.MEDIUM, Category.CORRECTNESS)
    assert metrics.fields() == {
        "attempt": 1,
        "seed": 42,
        "num_predict": expected,
        "prompt_eval_count": 900,
        "eval_count": 120,
        "total_duration": 5_000_000,
        "input_token_estimate": est(size),  # the A17 ratio's denominator (§20.8)
        "token_estimate_exceeded": 900 > est(size),
        "thinking_emitted": 0,
    }


def test_keep_alive_minus_one_is_sent_as_a_number() -> None:
    server = Ollama()
    review(server, keep_alive="-1")
    assert server.bodies[0]["keep_alive"] == -1


def test_the_source_is_numbered_and_delimited_with_a_fresh_nonce() -> None:
    server = Ollama(chat=[envelope("{not json"), envelope()])
    nonces = iter(["a" * 16, "b" * 16])
    call(provider(server, nonce=lambda: next(nonces)).review(REQUEST, FixedDeadline(300)))
    first, second = (b["messages"][1]["content"] for b in server.bodies)
    assert "<<<SOURCE_aaaaaaaaaaaaaaaa\n1 | def f(items=[]):\n2 |     return items" in first
    assert (
        "SOURCE_aaaaaaaaaaaaaaaa>>>" in first
        and "<<<STATIC_FINDINGS_aaaaaaaaaaaaaaaa\n(none)" in first
    )
    assert "SOURCE_bbbbbbbbbbbbbbbb>>>" in second and "aaaaaaaaaaaaaaaa" not in second


def test_a_missing_token_count_is_not_invented() -> None:
    reply = envelope()
    del reply["prompt_eval_count"], reply["eval_count"], reply["total_duration"]
    _, metrics = review(Ollama(chat=[reply]))
    fields = metrics.fields()
    assert not {
        "prompt_eval_count",
        "eval_count",
        "total_duration",
        "token_estimate_exceeded",
    } & set(fields)
    assert (fields["attempt"], fields["seed"]) == (1, 42)


def test_a_token_count_above_the_estimate_is_flagged() -> None:
    _, metrics = review(Ollama(chat=[envelope(prompt_eval_count=10**6)]))
    assert metrics.token_estimate_exceeded is True


def test_thinking_is_ignored_and_only_counted(caplog: pytest.LogCaptureFixture) -> None:
    reply = envelope()
    reply["message"]["thinking"] = "THINKING_SENTINEL reasoning"
    result, metrics = review(Ollama(chat=[reply]))
    assert result.summary == "One issue was found." and metrics.thinking_emitted == 1
    assert "THINKING_SENTINEL" not in caplog.text


# --- completion and errors (§9.4, D-65, D-90) ----------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (envelope(done_reason="length"), ErrorCode.AI_CONTEXT_EXCEEDED),
        (envelope(done_reason="load"), ErrorCode.AI_OUTPUT_INVALID),
        (envelope(done_reason=None), ErrorCode.AI_OUTPUT_INVALID),
        ({k: v for k, v in envelope().items() if k != "done_reason"}, ErrorCode.AI_OUTPUT_INVALID),
        (envelope() | {"message": {"role": "assistant"}}, ErrorCode.AI_OUTPUT_INVALID),
        (envelope() | {"message": "text"}, ErrorCode.AI_OUTPUT_INVALID),
        (httpx.Response(200, text="not json"), ErrorCode.AI_OUTPUT_INVALID),
        (httpx.Response(200, json=[1, 2]), ErrorCode.AI_OUTPUT_INVALID),
        (httpx.Response(404, json={"error": "model not found"}), ErrorCode.AI_MODEL_UNAVAILABLE),
        (httpx.Response(500, text=f"boom {SENTINEL}"), ErrorCode.AI_MODEL_UNAVAILABLE),
        (httpx.ConnectError("refused"), ErrorCode.AI_MODEL_UNAVAILABLE),
        (httpx.ConnectTimeout("slow"), ErrorCode.AI_MODEL_UNAVAILABLE),
        (httpx.RemoteProtocolError("dropped"), ErrorCode.AI_MODEL_UNAVAILABLE),
        (httpx.ReadTimeout("slow"), ErrorCode.REVIEW_TIMEOUT),
    ],
)
def test_each_failure_maps_to_its_code_and_is_not_retried(reply: Any, code: ErrorCode) -> None:
    server = Ollama(chat=[reply, envelope()])
    error, _ = failure(server)
    assert error.code is code
    assert len(server.bodies) == 1  # none of these is retried
    assert SENTINEL not in str(error) and "refused" not in str(error)


def test_a_missing_model_has_its_own_message() -> None:
    error, _ = failure(Ollama(chat=[httpx.Response(404, json={"error": "not found"})]))
    assert str(error) == "The configured AI model is not installed."


def test_a_length_stop_is_context_exceeded_even_with_parseable_json() -> None:
    error, metrics = failure(Ollama(chat=[envelope(REVIEW, done_reason="length")]))
    assert error.code is ErrorCode.AI_CONTEXT_EXCEEDED and metrics.attempt == 1


def test_an_oversized_response_is_invalid() -> None:
    big = httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
    error, _ = failure(Ollama(chat=[big]))
    assert error.code is ErrorCode.AI_OUTPUT_INVALID


# --- retry (§9.4) --------------------------------------------------------------------------------


def test_invalid_output_is_retried_once_with_the_next_seed() -> None:
    server = Ollama(chat=[envelope("{not json"), envelope()])
    result, metrics = review(server)
    assert result.attempts == 2 and [b["options"]["seed"] for b in server.bodies] == [42, 43]
    assert (metrics.attempt, metrics.seed) == (2, 43)


def test_a_schema_violation_is_retried_then_fails() -> None:
    long_title = json.loads(json.dumps(REVIEW))
    long_title["issues"][0]["title"] = "x" * 151  # over the schema safety bound (D-76)
    server = Ollama(chat=[envelope(long_title)])
    error, metrics = failure(server)
    assert error.code is ErrorCode.AI_OUTPUT_INVALID and len(server.bodies) == 2
    assert metrics.attempt == 2


@pytest.mark.parametrize(("options", "remaining"), [({"retry_count": 0}, 300), ({}, 19.9)])
def test_no_retry_when_disabled_or_too_little_time(
    options: dict[str, Any], remaining: float
) -> None:
    server = Ollama(chat=[envelope("{not json"), envelope()])
    error, _ = failure(server, remaining, **options)
    assert error.code is ErrorCode.AI_OUTPUT_INVALID and len(server.bodies) == 1


# --- time (§9.3 step 5, D-48) --------------------------------------------------------------------


def test_the_attempt_is_bounded_by_the_remaining_deadline() -> None:
    server = Ollama(delay_s=5.0)
    error, _ = failure(server, remaining=0.05)
    assert error.code is ErrorCode.REVIEW_TIMEOUT


def test_the_attempt_is_bounded_by_the_ollama_timeout() -> None:
    server = Ollama(delay_s=5.0)
    error, _ = failure(server, remaining=300, timeout_s=0.05)
    assert error.code is ErrorCode.REVIEW_TIMEOUT


def test_an_exhausted_deadline_sends_nothing() -> None:
    server = Ollama()
    error, _ = failure(server, remaining=0)
    assert error.code is ErrorCode.REVIEW_TIMEOUT and server.requests == []


# --- request-time budget (§9.7, D-92) ------------------------------------------------------------


def input_tokens(request: AIReviewRequest = REQUEST) -> int:
    """The estimate for the messages the provider renders, read back from a recorded call."""
    server = Ollama()
    review_call = provider(server).review(request, FixedDeadline(300))
    call(review_call)
    system, user = server.bodies[0]["messages"]
    return est(len(system["content"].encode()) + len(user["content"].encode()))


def test_an_exact_fit_sends_the_minimum_output() -> None:
    num_ctx = input_tokens() + CONTEXT_SAFETY_MARGIN_TOKENS + 4096
    server = Ollama()
    review(server, num_ctx=num_ctx)
    assert server.bodies[0]["options"]["num_predict"] == 4096


def test_one_token_short_raises_context_exceeded_with_no_request() -> None:
    num_ctx = input_tokens() + CONTEXT_SAFETY_MARGIN_TOKENS + 4095
    server = Ollama()
    error, metrics = failure(server, num_ctx=num_ctx)
    assert error.code is ErrorCode.AI_CONTEXT_EXCEEDED and server.requests == []
    assert "num_predict" not in metrics.fields()  # no call was made with any budget


def test_num_predict_is_capped_by_the_configuration() -> None:
    server = Ollama()
    review(server, num_predict=5000)
    assert server.bodies[0]["options"]["num_predict"] == 5000


def test_the_budget_counts_the_rendered_static_findings() -> None:
    findings = tuple(
        static_finding(n, summary="m" * 200, title="t" * 100, line=1) for n in range(1, 31)
    )
    many = AIReviewRequest(language=Language.PYTHON, source=SOURCE, static_findings=findings)
    assert input_tokens(many) > input_tokens() + 25 * 300 // 3  # 25 rendered lines, not 30
    server = Ollama()
    result, _ = call(provider(server).review(many, FixedDeadline(300)))
    user = server.bodies[0]["messages"][1]["content"]
    assert user.count('{"id":"S') == 25 and "(5 further findings omitted)" in user
    assert result.static_findings_omitted == 5


# --- improvement transport (the operation itself is PR-07) ---------------------------------------


def test_improve_uses_its_own_schema_minimum_and_blocks() -> None:
    server = Ollama(chat=[envelope(IMPROVEMENT)])
    result, metrics = call(provider(server).improve(IMPROVE, FixedDeadline(300)))
    (body,) = server.bodies
    assert body["format"] == AIImprovementOutput.model_json_schema()
    user = body["messages"][1]["content"]
    assert "<<<ISSUES_" in user and "<<<SOURCE_" in user and "1 | def" not in user
    assert body["options"]["num_predict"] >= improve_min_output_tokens(SOURCE.byte_size) >= 1024
    assert result.code == IMPROVEMENT["improved_code"] and result.notes == ("Fixed.",)
    assert metrics.attempt == 1


# --- readiness (§6.5, §9.5, D-54, D-90) ----------------------------------------------------------


def health(server: Ollama, **options: Any) -> Any:
    return asyncio.run(provider(server, **options).check_health(3.0))


def test_names_match_exactly_or_with_the_implicit_latest_tag() -> None:
    assert model_names("qwen") == {"qwen", "qwen:latest"}
    assert model_names("qwen:4b") == {"qwen:4b"}
    assert model_names("host:5000/qwen") == {"host:5000/qwen", "host:5000/qwen:latest"}


@pytest.mark.parametrize(
    ("configured", "listed", "expected"),
    [
        (MODEL, [{"name": MODEL, "digest": "ab" * 32}], MODEL_READY),
        (MODEL, [{"model": MODEL, "digest": "ab" * 32}], MODEL_READY),
        ("qwen", [{"name": "qwen:latest", "digest": "ab" * 32}], MODEL_READY),
        ("qwen", [{"name": "qwen:7b", "digest": "ab" * 32}], MODEL_ABSENT),
        (MODEL, [], MODEL_ABSENT),
    ],
)
def test_readiness_requires_the_configured_model(
    configured: str, listed: list[dict[str, str]], expected: Any
) -> None:
    server = Ollama(tags={"models": listed})
    assert health(server, model=configured) == expected
    assert server.requests[0].url.path == "/api/tags"


@pytest.mark.parametrize(
    ("digest", "expected"), [("ab" * 6, MODEL_READY), ("cd" * 6, DIGEST_MISMATCH)]
)
def test_a_configured_digest_must_prefix_the_installed_one(digest: str, expected: Any) -> None:
    assert health(Ollama(), model_digest=digest) == expected


@pytest.mark.parametrize(
    "reply",
    [
        httpx.ConnectError(r"C:\Users\someone\ollama refused"),
        httpx.Response(500, json={"error": "x"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"models": "nope"}),
        httpx.Response(200, json={}),
    ],
)
def test_an_unreachable_or_broken_ollama_is_reported_safely(reply: Any) -> None:
    found = health(Ollama(tags=reply))
    assert found == NOT_REACHABLE and "\\" not in found.detail and "refused" not in found.detail


def test_a_slow_ollama_is_not_ready() -> None:
    server = Ollama(delay_s=5.0)

    async def body() -> Any:
        return await provider(server).check_health(0.05)

    assert asyncio.run(body()) == NOT_REACHABLE


def test_providers_render_independent_nonces() -> None:
    server = Ollama()
    for _ in itertools.repeat(None, 3):
        review(server)
    nonces = {b["messages"][1]["content"].split("<<<SOURCE_")[1][:16] for b in server.bodies}
    assert len(nonces) == 3


def test_a_worst_case_maximum_review_is_admitted_at_the_defaults() -> None:
    server = Ollama(chat=[envelope({"summary": "S.", "issues": []})])
    call(provider(server).review(worst_review(extra=1000), FixedDeadline(300)))
    (body,) = server.bodies
    assert body["options"]["num_predict"] >= REVIEW_MIN_OUTPUT_TOKENS  # sent, not refused


def test_a_worst_case_maximum_improvement_is_admitted_at_the_defaults() -> None:
    server = Ollama(chat=[envelope(IMPROVEMENT)])
    call(provider(server).improve(worst_improvement(), FixedDeadline(300)))
    (body,) = server.bodies
    assert body["options"]["num_predict"] >= improve_min_output_tokens(12_000) == 5_112
