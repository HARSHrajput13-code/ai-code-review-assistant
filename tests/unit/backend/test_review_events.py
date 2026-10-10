"""Review lifecycle log events (CIS §19.1, §7.2, §18 D-55, §20.3).

Records are captured through the production JSON formatter, so the asserted fields are the ones
written. Time comes from FakeClock and concurrency from events; no test sleeps.
"""

import asyncio
import io
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

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
from backend.application import orchestrator as orchestrator_module
from backend.application.events import bound
from backend.application.improvement import ImprovementOperation
from backend.application.job_service import ReviewJobService
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.validation import SubmissionValidator
from backend.logging_setup import JsonFormatter
from backend.review.scoring.policy import ScoringPolicyV1
from shared.domain.enums import ReviewStatus
from shared.domain.errors import AIResponseInvalid, ImprovedCodeInvalid
from shared.domain.interfaces import AIImprovementRequest
from shared.domain.models import SourceText, SyntaxCheck
from tests.unit.ai.ollama_doubles import IMPROVEMENT, Ollama, envelope
from tests.unit.ai.ollama_doubles import MODEL as OLLAMA_MODEL
from tests.unit.ai.ollama_doubles import provider as ollama
from tests.unit.backend.api_client import CODE, KEY, REVIEWS, Api, scenario
from tests.unit.backend.pipeline_doubles import FAILED, SUBMISSION, FakeImprover, StubAdapter
from tests.unit.builders import FakeClock, ScriptedDeadline

MARK = "LEAK_SENTINEL_7F91"
STAGE_FIELDS = {
    "ts", "level", "logger", "event", "request_id", "review_id", "stage", "status", "duration_ms",
}  # fmt: skip
MODEL = {"ai_provider": "fake", "ai_model": "fake", "prompt_version": "v1"}
FRAME = re.compile(r"^[\w.]+\.py:\d+ in [\w<>]+$")
STARTED, FINISHED, DONE = "review.stage.started", "review.stage.finished", "review.finished"


class Log:
    def __init__(self, stream: io.StringIO) -> None:
        self.stream = stream

    @property
    def records(self) -> list[dict[str, Any]]:
        lines = self.stream.getvalue().splitlines()
        return [r for r in map(json.loads, lines) if r["logger"].startswith("backend.")]

    def of(self, review_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("review_id") == review_id]

    def stage(self, event: str, stage: str) -> dict[str, Any]:
        (found,) = [r for r in self.records if r["event"] == event and r.get("stage") == stage]
        return found


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


def trail(records: list[dict[str, Any]]) -> list[tuple[str, str | None, str | None]]:
    return [(r["event"], r.get("stage"), r.get("status")) for r in records]


def ran(*stages: str) -> list[tuple[str, str | None, str | None]]:
    return [step for s in stages for step in ((STARTED, s, "RUNNING"), (FINISHED, s, "SUCCEEDED"))]


ANALYSIS = ran("PARSING", "STATIC_ANALYSIS", "STATIC_NUMBERING", "AI_ANALYSIS")
FINDINGS = ran("NORMALIZATION", "DEDUPLICATION", "SCORING")
NO_IMPROVER = [
    (FINISHED, "IMPROVEMENT", "SKIPPED"),
    (FINISHED, "IMPROVEMENT_VALIDATION", "SKIPPED"),
]
# The composed improvement operation logs stage 11 inside stage 10 (§14.3, §19.1).
IMPROVED = [
    (STARTED, "IMPROVEMENT", "RUNNING"),
    *ran("IMPROVEMENT_VALIDATION"),
    (FINISHED, "IMPROVEMENT", "SUCCEEDED"),
]


def jobs(clock: FakeClock, runner: Any = None, *, max_concurrent: int = 3, **parts: Any) -> Any:
    orchestrator = runner or ReviewOrchestrator(
        parts.get("provider") or FakeAIReviewProvider(),
        clock,
        parts.get("options") or OrchestratorOptions(),
        parts.get("improver"),
        parts.get("policy"),
    )
    return ReviewJobService(
        InMemoryReviewJobStore(max_active=10, max_retained=50, ttl_seconds=900),
        SubmissionValidator(LanguageRegistry([parts.get("adapter") or StubAdapter()]), 12_000, 500),
        orchestrator,
        clock,
        max_concurrent=max_concurrent,
        review_timeout_seconds=300,
    )


async def submit(service: ReviewJobService, request_id: str, code: str = CODE) -> str:
    with bound(request_id, None):  # as the API's request context does
        review, _ = await service.submit("python", code)
    return str(review.review_id)


def reviewed(**parts: Any) -> tuple[Any, str]:
    """One review through the job service and the real orchestrator; (final review, its ID)."""

    async def body() -> tuple[Any, str]:
        service = jobs(parts.pop("clock", None) or FakeClock(), **parts)
        review_id = await submit(service, "request-0000-0001")
        await service.wait_idle()
        return await service.get(UUID(review_id)), review_id

    return asyncio.run(body())


@dataclass
class Gated(FakeAIReviewProvider):
    """Holds each AI call until `expected` calls are inside, or until released."""

    expected: int = 0
    arrived: int = 0
    inside: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)

    async def review(self, request: Any, deadline: Any) -> Any:
        self.arrived += 1
        self.inside.set()
        if self.arrived == self.expected:
            self.gate.set()
        await self.gate.wait()
        return await super().review(request, deadline)


@dataclass
class Advancing(FakeAIReviewProvider):
    clock: FakeClock = field(default_factory=FakeClock)
    seconds: float = 2.5

    async def review(self, request: Any, deadline: Any) -> Any:
        self.clock.advance(self.seconds)
        return await super().review(request, deadline)


class BrokenParser(StubAdapter):
    def check_syntax(self, source: SourceText) -> SyntaxCheck:
        raise RuntimeError(f"{MARK} {source.text}")


class ExplodingImprover(FakeImprover):
    async def improve(self, submission: Any, *args: Any) -> Any:
        raise RuntimeError(f"{MARK} {submission.source.text}")


class ExplodingPolicy(ScoringPolicyV1):
    def score(self, issues: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError(f"{MARK} {[i.summary for i in issues]}")


class ExplodingRunner:
    async def run(self, submission: Any, *args: Any) -> Any:
        raise RuntimeError(f"{MARK} {submission.source.text}")


def assert_diagnostics(record: dict[str, Any], exception_type: str) -> None:
    assert record["level"] == "ERROR" and record["exception_type"] == exception_type
    assert record["traceback"] and all(FRAME.match(frame) for frame in record["traceback"])


def assert_each_stage_once(records: list[dict[str, Any]]) -> None:
    """No stage of one review starts or finishes twice."""
    counts = Counter((r["event"], r["stage"]) for r in records if r["event"] in (STARTED, FINISHED))
    assert set(counts.values()) <= {1}


# --- the whole lifecycle -------------------------------------------------------------------


def test_a_review_over_http_logs_its_whole_lifecycle_under_its_request(log: Log) -> None:
    rid = "request-http-0001"
    found: dict[str, Any] = {}

    async def body(api: Api) -> None:
        headers = {"content-type": "application/json", "X-Request-ID": rid, "Idempotency-Key": KEY}
        payload = json.dumps({"language": "python", "source_code": CODE}).encode()
        created = await api.client.post(REVIEWS, content=payload, headers=headers)
        await api.context.jobs.wait_idle()
        replay = await api.client.post(REVIEWS, content=payload, headers=headers)
        assert (created.status_code, replay.status_code) == (202, 200)
        found.update((await api.client.get(created.headers["Location"])).json())

    scenario(body)
    records = log.of(found["review_id"])
    assert trail(records) == [
        ("review.accepted", None, None), *ANALYSIS, *FINDINGS, *IMPROVED,
        (DONE, None, "COMPLETED"), ("review.accepted", None, None),
    ]  # fmt: skip
    assert [r["replayed"] for r in records if r["event"] == "review.accepted"] == [False, True]
    assert {r["request_id"] for r in records} == {rid}
    for record in records:
        if record["event"] in (STARTED, FINISHED):
            assert STAGE_FIELDS <= set(record)
            assert isinstance(record["duration_ms"], int) and record["ts"].endswith("Z")
            assert record["level"] == "INFO" and "exception_type" not in record
    for stage in ("AI_ANALYSIS", "IMPROVEMENT"):  # both AI stages carry the model (§19.1)
        records_of = [r for r in records if r.get("stage") == stage]
        assert all(MODEL.items() <= r.items() for r in records_of)
        assert (records_of[1]["attempt"], records_of[1]["seed"]) == (1, 42)
    assert_each_stage_once(records)
    validation = log.stage(FINISHED, "IMPROVEMENT_VALIDATION")
    assert "ai_model" not in validation and "rejection_reason" not in validation

    result, finished = found["result"], next(r for r in records if r["event"] == DONE)
    assert finished["level"] == "INFO" and finished["status"] == found["status"]
    assert finished["score"] == result["score"]["overall"]
    assert finished["assessed_weight"] == result["score"]["assessed_weight"]
    assert finished["coverage"] == result["coverage"]
    assert finished["severity_counts"] == result["severity_counts"]
    assert finished["refs_rejected"] == 0 and finished["duration_ms"] == 0
    assert finished["outcomes"] == {
        "static_analysis": {"status": "SUCCEEDED"},
        "pylint": {"status": "SUCCEEDED"},
        "bandit": {"status": "SUCCEEDED"},
        "ai_analysis": {"status": "SUCCEEDED"},
        "improvement": {"status": "SUCCEEDED"},
    }


def test_a_disabled_improvement_logs_finish_only_skips(log: Log) -> None:
    found: dict[str, Any] = {}

    async def body(api: Api) -> None:
        created = await api.submit()
        await api.context.jobs.wait_idle()
        found.update((await api.client.get(created.headers["Location"])).json())

    scenario(body, improvement_enabled=False)  # IMPROVEMENT_ENABLED=false (§14.5 row 1)
    records = log.of(found["review_id"])
    assert trail(records)[-3:] == [*NO_IMPROVER, (DONE, None, "COMPLETED")]
    for stage in ("IMPROVEMENT", "IMPROVEMENT_VALIDATION"):  # neither ever starts
        skipped = log.stage(FINISHED, stage)
        assert skipped["skip_reason"] == "DISABLED" and "error_code" not in skipped


def test_durations_come_from_the_clock(log: Log) -> None:
    clock = FakeClock()
    review, _ = reviewed(clock=clock, provider=Advancing(clock=clock))
    assert review.result.analysis.ai_analysis.duration_ms == 2500
    assert log.stage(FINISHED, "AI_ANALYSIS")["duration_ms"] == 2500
    assert log.stage(FINISHED, "PARSING")["duration_ms"] == 0
    assert next(r for r in log.records if r["event"] == DONE)["duration_ms"] == 2500


def test_an_executed_improvement_logs_its_start_and_finish(log: Log) -> None:
    review, review_id = reviewed(improver=FakeImprover())
    assert review.status is ReviewStatus.COMPLETED
    # A result the operation returns has been through its own §14.3 validation, which the
    # operation logs (PR-07); the orchestrator adds no stage-11 record.
    assert trail(log.of(review_id)) == [
        *ANALYSIS, *FINDINGS, *ran("IMPROVEMENT"), (DONE, None, "COMPLETED"),
    ]  # fmt: skip


# --- failures, skips, deadlines and cancellation ------------------------------------------


def test_a_failing_static_stage_logs_safe_diagnostics(log: Log) -> None:
    error = RuntimeError(f"{MARK} {SUBMISSION.source.text}")
    review, _ = reviewed(adapter=StubAdapter(error=error))
    assert review.status is ReviewStatus.PARTIAL
    finished = log.stage(FINISHED, "STATIC_ANALYSIS")
    assert (finished["status"], finished["error_code"]) == ("FAILED", "STATIC_ANALYSIS_FAILURE")
    assert_diagnostics(finished, "RuntimeError")
    assert any(frame.startswith("pipeline_doubles.py:") for frame in finished["traceback"])
    assert MARK not in log.stream.getvalue() and "items=[]" not in log.stream.getvalue()


def test_a_failing_parser_fails_the_tools_that_never_started(log: Log) -> None:
    review, review_id = reviewed(adapter=BrokenParser())
    assert review.status is ReviewStatus.PARTIAL
    assert trail(log.of(review_id))[:3] == [
        (STARTED, "PARSING", "RUNNING"),
        (FINISHED, "PARSING", "FAILED"),
        (FINISHED, "STATIC_ANALYSIS", "FAILED"),  # no start: the tools never ran
    ]
    assert_diagnostics(log.stage(FINISHED, "PARSING"), "RuntimeError")
    assert "exception_type" not in log.stage(FINISHED, "STATIC_ANALYSIS")
    assert MARK not in log.stream.getvalue()


def test_a_failed_ai_stage_and_the_improvement_it_skips(log: Log) -> None:
    provider = FakeAIReviewProvider(review_script=[UNAVAILABLE])
    review, review_id = reviewed(provider=provider, improver=FakeImprover())
    assert review.status is ReviewStatus.PARTIAL
    ai = log.stage(FINISHED, "AI_ANALYSIS")
    assert (ai["status"], ai["error_code"], ai["level"]) == (
        "FAILED", "AI_MODEL_UNAVAILABLE", "WARNING",
    )  # fmt: skip
    assert MODEL.items() <= ai.items() and "exception_type" not in ai
    # What the provider observed for the call it made (§19.1); nothing it never saw is invented.
    assert (ai["attempt"], ai["seed"], ai["num_predict"]) == (1, 42, 8192)
    assert "prompt_eval_count" not in ai and "eval_count" not in ai
    improvement = log.stage(FINISHED, "IMPROVEMENT")
    assert (improvement["skip_reason"], improvement["error_code"]) == (
        "DEPENDENCY_FAILED", "AI_MODEL_UNAVAILABLE",
    )  # fmt: skip
    assert not [
        r for r in log.of(review_id) if r["event"] == STARTED and r["stage"] == "IMPROVEMENT"
    ]


def test_an_unexpected_improvement_error_is_logged_at_its_boundary(log: Log) -> None:
    review, _ = reviewed(improver=ExplodingImprover())
    assert review.status is ReviewStatus.PARTIAL
    finished = log.stage(FINISHED, "IMPROVEMENT")
    assert (finished["status"], finished["error_code"]) == ("FAILED", "AI_MODEL_UNAVAILABLE")
    assert_diagnostics(finished, "RuntimeError")
    assert MARK not in log.stream.getvalue()


def test_stages_skipped_by_the_deadline_log_no_start(log: Log) -> None:
    async def report(stage: Any) -> None:
        pass

    orchestrator = ReviewOrchestrator(FakeAIReviewProvider(), FakeClock(), OrchestratorOptions())
    outcome = asyncio.run(
        orchestrator.run(SUBMISSION, StubAdapter(), ScriptedDeadline([0.0]), report)
    )
    assert outcome.status is ReviewStatus.FAILED
    assert trail(log.records) == [
        *ran("PARSING"),
        (FINISHED, "STATIC_ANALYSIS", "SKIPPED"),
        *ran("STATIC_NUMBERING"),
        (FINISHED, "AI_ANALYSIS", "SKIPPED"),  # then the checkpoint fails the review
    ]
    for stage in ("STATIC_ANALYSIS", "AI_ANALYSIS"):
        skipped = log.stage(FINISHED, stage)
        assert (skipped["skip_reason"], skipped["error_code"]) == (
            "DEADLINE_EXCEEDED", "REVIEW_TIMEOUT",
        )  # fmt: skip
        assert skipped["duration_ms"] == 0


def test_the_deadline_cancelling_a_running_stage_is_a_failed_finish(log: Log) -> None:
    async def report(stage: Any) -> None:
        pass

    slow = FakeAIReviewProvider(review_script=[FakeResponse(delay_s=5)])
    orchestrator = ReviewOrchestrator(slow, FakeClock(), OrchestratorOptions(ai_start_min_s=0))
    # 300 s for the static stage, then 1 ms, which starts the AI call and cancels it.
    deadline = ScriptedDeadline([300, 0.001])
    outcome = asyncio.run(orchestrator.run(SUBMISSION, StubAdapter(), deadline, report))
    assert outcome.status is ReviewStatus.PARTIAL
    ai = [r for r in log.records if r.get("stage") == "AI_ANALYSIS"]
    assert trail(ai) == [(STARTED, "AI_ANALYSIS", "RUNNING"), (FINISHED, "AI_ANALYSIS", "FAILED")]
    assert ai[1]["error_code"] == "REVIEW_TIMEOUT" and "exception_type" not in ai[1]


def test_a_review_cancelled_at_shutdown_logs_no_finish(log: Log) -> None:
    async def body() -> tuple[str, Any]:
        provider = Gated()
        service = jobs(FakeClock(), provider=provider)
        review_id = await submit(service, "request-shutdown-01")
        await provider.inside.wait()
        await service.shutdown()
        return review_id, (await service.get(UUID(review_id))).status

    review_id, status = asyncio.run(body())
    assert status is ReviewStatus.RUNNING  # never terminal
    records = log.of(review_id)
    assert trail(records)[-1] == (STARTED, "AI_ANALYSIS", "RUNNING")
    assert not [r for r in records if r["event"] == DONE]


# --- exactly one review.finished -----------------------------------------------------------


@pytest.mark.parametrize(
    ("parts", "status", "error_code", "exception_type"),
    [
        ({}, "COMPLETED", None, None),
        ({"provider": FakeAIReviewProvider(review_script=[UNAVAILABLE])}, "PARTIAL", None, None),
        (
            {"adapter": StubAdapter(pylint=FAILED, bandit=FAILED),
             "provider": FakeAIReviewProvider(review_script=[TIMEOUT])},
            "FAILED", "REVIEW_TIMEOUT", None,
        ),
        ({"policy": ExplodingPolicy()}, "FAILED", "INTERNAL_ERROR", "ValueError"),
        ({"runner": ExplodingRunner()}, "FAILED", "INTERNAL_ERROR", "RuntimeError"),
    ],
    ids=["completed", "partial", "nothing-assessed", "scoring-bug", "runner-bug"],
)  # fmt: skip
def test_each_terminal_review_logs_exactly_one_finish(
    log: Log, parts: dict[str, Any], status: str, error_code: str | None, exception_type: str | None
) -> None:
    review, review_id = reviewed(**parts)
    records = log.of(review_id)
    finished = [r for r in records if r["event"] == DONE]
    assert len(finished) == 1 and records[-1] is finished[0]
    assert_each_stage_once(records)
    record = finished[0]
    assert (record["status"], record.get("error_code")) == (review.status, error_code)
    assert record["request_id"] == "request-0000-0001"
    if exception_type is not None:
        assert_diagnostics(record, exception_type)
    else:
        expected = "WARNING" if status == "FAILED" else "INFO"
        assert record["level"] == expected and "exception_type" not in record
    if status == "FAILED":
        assert "score" not in record and "outcomes" not in record
    if parts.get("policy"):  # the stage that broke is visible too
        scoring = log.stage(FINISHED, "SCORING")
        assert (scoring["status"], scoring["error_code"]) == ("FAILED", "INTERNAL_ERROR")
    assert MARK not in log.stream.getvalue()


def test_a_queue_timeout_logs_one_finish_under_the_creating_request(log: Log) -> None:
    async def body() -> tuple[str, str]:
        clock, provider = FakeClock(), Gated()
        service = jobs(clock, provider=provider, max_concurrent=1)
        running = await submit(service, "request-running-01")
        queued = await submit(service, "request-queued-002")
        await provider.inside.wait()
        clock.advance(301)
        with bound("request-sweeper-03", None):  # any request may run the sweep
            assert (await service.get(UUID(queued))).status is ReviewStatus.FAILED
        provider.gate.set()
        await service.wait_idle()  # the queued task wakes, finds it failed, logs nothing
        return running, queued

    running, queued = asyncio.run(body())
    finished = Counter(r["review_id"] for r in log.records if r["event"] == DONE)
    assert finished == {running: 1, queued: 1}
    (record,) = log.of(queued)  # it never started, so it has no stage records
    assert (record["event"], record["status"], record["error_code"], record["level"]) == (
        DONE, "FAILED", "REVIEW_TIMEOUT", "WARNING",
    )  # fmt: skip
    assert record["request_id"] == "request-queued-002"
    assert {r["request_id"] for r in log.of(running)} == {"request-running-01"}


# --- correlation under concurrency ---------------------------------------------------------


def test_concurrent_reviews_keep_their_own_request_ids(log: Log) -> None:
    rids = [f"request-concurrent-{n}" for n in range(3)]
    ids: dict[str, str] = {}
    provider = Gated(expected=3)  # all three are inside the AI stage at once

    async def body(api: Api) -> None:
        async def post(rid: str, n: int) -> None:
            payload = json.dumps({"language": "python", "source_code": f"x = {n}\n"}).encode()
            headers = {"content-type": "application/json", "X-Request-ID": rid}
            response = await api.client.post(REVIEWS, content=payload, headers=headers)
            ids[rid] = response.json()["review_id"]

        await asyncio.gather(*(post(rid, n) for n, rid in enumerate(rids)))
        await api.context.jobs.wait_idle()

    scenario(body, provider=provider, review_max_concurrent=3)
    stage_records = [r for r in log.records if r["event"] in (STARTED, FINISHED, DONE)]
    order = [r["review_id"] for r in stage_records]
    assert order != sorted(order, key=order.index)  # the reviews really interleaved
    for rid, review_id in ids.items():
        records = log.of(review_id)
        assert {r["request_id"] for r in records} == {rid}
        assert [r["event"] for r in records].count(DONE) == 1
    assert all(r.get("review_id") in ids.values() for r in stage_records)


def test_a_bug_in_static_numbering_fails_the_review_once(
    log: Log, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any) -> Any:
        raise ValueError(MARK)

    monkeypatch.setattr(orchestrator_module, "number_static", broken)
    review, review_id = reviewed()
    assert review.status is ReviewStatus.FAILED
    assert trail(log.of(review_id)) == [
        *ran("PARSING", "STATIC_ANALYSIS"),
        (STARTED, "STATIC_NUMBERING", "RUNNING"),
        (FINISHED, "STATIC_NUMBERING", "FAILED"),
        (DONE, None, "FAILED"),
    ]
    assert log.stage(FINISHED, "STATIC_NUMBERING")["error_code"] == "INTERNAL_ERROR"
    assert_diagnostics(log.of(review_id)[-1], "ValueError")
    assert MARK not in log.stream.getvalue()


# --- stage 11, IMPROVEMENT_VALIDATION (§7.2, §14.3, §14.5) ------------------------------------

NO_ISSUES = FakeResponse(content=json.dumps({"summary": "Nothing to change.", "issues": []}))


class BlockingImprover(FakeImprover):
    async def improve(self, *args: Any) -> Any:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class UnusableResponse(FakeImprover):
    async def improve(self, *args: Any) -> Any:
        raise AIResponseInvalid(retryable=False)


class InvalidCandidate(FakeImprover):
    """The operation validated its candidate and rejected it (§14.3)."""

    async def improve(self, *args: Any) -> Any:
        raise ImprovedCodeInvalid(f"INTERFACE_CHANGED {MARK}")


def improving(**parts: Any) -> Any:
    """Parts for `reviewed`, with an improvement operation present unless one is given."""
    return lambda clock: {"improver": FakeImprover(), **parts}


@pytest.mark.parametrize(
    ("parts", "improvement", "skip_reason", "error_code", "status"),
    [
        (lambda clock: {}, "SKIPPED", "DISABLED", None, "COMPLETED"),
        (
            improving(provider=FakeAIReviewProvider(review_script=[UNAVAILABLE])),
            "SKIPPED", "DEPENDENCY_FAILED", "AI_MODEL_UNAVAILABLE", "PARTIAL",
        ),
        (
            improving(adapter=StubAdapter(candidates=()),
                      provider=FakeAIReviewProvider(review_script=[NO_ISSUES])),
            "SKIPPED", "NOT_NEEDED", None, "COMPLETED",
        ),
        (
            improving(adapter=StubAdapter(candidates=()),
                      provider=FakeAIReviewProvider(review_script=[INVALID_JSON, INVALID_JSON])),
            "SKIPPED", "DEPENDENCY_FAILED", "AI_OUTPUT_INVALID", "PARTIAL",
        ),
        (
            lambda clock: {"improver": FakeImprover(),
                           "provider": Advancing(clock=clock, seconds=290)},  # 10 s < 15 s
            "SKIPPED", "DEADLINE_EXCEEDED", "REVIEW_TIMEOUT", "PARTIAL",
        ),
        (
            lambda clock: {"improver": BlockingImprover(),
                           "provider": Advancing(clock=clock, seconds=299.999),  # 1 ms left
                           "options": OrchestratorOptions(improvement_start_min_s=0)},
            "FAILED", "DEADLINE_EXCEEDED", "REVIEW_TIMEOUT", "PARTIAL",
        ),
        (
            improving(improver=UnusableResponse()),
            "FAILED", "DEPENDENCY_FAILED", "AI_OUTPUT_INVALID", "PARTIAL",
        ),
        (
            improving(improver=ExplodingImprover()),
            "FAILED", "DEPENDENCY_FAILED", "AI_MODEL_UNAVAILABLE", "PARTIAL",
        ),
    ],
    ids=[
        "disabled", "ai-unavailable", "not-needed", "no-issues-ai-invalid",
        "deadline-before-start", "deadline-cancels-operation", "provider-error", "operation-bug",
    ],
)  # fmt: skip
def test_validation_without_a_candidate_logs_a_finish_only_skip_for_its_cause(
    log: Log,
    parts: Any,
    improvement: str,
    skip_reason: str,
    error_code: str | None,
    status: str,
) -> None:
    clock = FakeClock()
    review, review_id = reviewed(clock=clock, **parts(clock))
    assert review.status == status
    # The review result is unchanged: the improvement outcome per §14.5, and no stage-11 entry.
    outcome = review.result.analysis.improvement
    assert (outcome.status, outcome.error_code) == (improvement, error_code)
    if improvement == "SKIPPED":
        assert outcome.skip_reason == skip_reason
    records = log.of(review_id)
    assert_each_stage_once(records)
    validation = [r for r in records if r.get("stage") == "IMPROVEMENT_VALIDATION"]
    assert [r["event"] for r in validation] == [FINISHED]  # exactly once, and never started
    record = validation[0]
    assert (record["status"], record["skip_reason"], record.get("error_code")) == (
        "SKIPPED", skip_reason, error_code,
    )  # fmt: skip
    assert record["duration_ms"] == 0 and record["level"] == "INFO"
    assert "exception_type" not in record and STAGE_FIELDS <= set(record)
    assert record["request_id"] == "request-0000-0001"
    previous = records[records.index(record) - 1]  # right after the improvement's finish
    assert (previous["event"], previous["stage"]) == (FINISHED, "IMPROVEMENT")
    finished = next(r for r in records if r["event"] == DONE)
    assert "IMPROVEMENT_VALIDATION" not in finished["outcomes"]
    assert MARK not in log.stream.getvalue()


def test_validation_inside_the_operation_is_not_logged_by_the_orchestrator(log: Log) -> None:
    review, review_id = reviewed(improver=InvalidCandidate())
    outcome = review.result.analysis.improvement
    assert (outcome.status, outcome.error_code) == ("FAILED", "IMPROVED_CODE_INVALID")
    records = log.of(review_id)
    assert not [r for r in records if r.get("stage") == "IMPROVEMENT_VALIDATION"]
    assert trail(records)[-2] == (FINISHED, "IMPROVEMENT", "FAILED")
    assert MARK not in log.stream.getvalue()  # nor the internal rejection reason


def test_a_review_stopped_at_the_checkpoint_logs_no_later_stages(log: Log) -> None:
    review, review_id = reviewed(
        adapter=StubAdapter(pylint=FAILED, bandit=FAILED),
        provider=FakeAIReviewProvider(review_script=[TIMEOUT]),
        improver=FakeImprover(),
    )
    assert review.status is ReviewStatus.FAILED and review.result is None
    stages = {r["stage"] for r in log.of(review_id) if r["event"] in (STARTED, FINISHED)}
    assert stages == {"PARSING", "STATIC_ANALYSIS", "STATIC_NUMBERING", "AI_ANALYSIS"}


def test_the_ollama_stage_record_carries_the_observed_call_metrics(log: Log) -> None:
    reply = envelope()
    reply["message"]["thinking"] = f"{MARK} private reasoning"
    reply["message"]["content"] = reply["message"]["content"].replace("One issue", MARK)
    server = Ollama(chat=[reply])
    review, review_id = reviewed(provider=ollama(server))
    assert review.status is ReviewStatus.COMPLETED
    ai = log.stage(FINISHED, "AI_ANALYSIS")
    assert ai["review_id"] == review_id and ai["status"] == "SUCCEEDED"
    assert (ai["ai_provider"], ai["ai_model"], ai["prompt_version"]) == (
        "ollama",
        OLLAMA_MODEL,
        "v1",
    )
    assert (ai["attempt"], ai["seed"]) == (1, 42)
    assert ai["num_predict"] == server.bodies[0]["options"]["num_predict"]
    assert (ai["prompt_eval_count"], ai["eval_count"], ai["total_duration"]) == (
        900,
        120,
        5_000_000,
    )
    assert ai["thinking_emitted"] == 1 and ai["token_estimate_exceeded"] in (True, False)
    assert ai["input_token_estimate"] > 0  # the A17 ratio needs est(input) (§20.8)
    text = log.stream.getvalue()
    assert MARK not in text and CODE not in text and "INSTRUCTION HIERARCHY" not in text


def test_a_review_that_cannot_fit_the_context_is_partial_with_no_request(log: Log) -> None:
    server = Ollama()
    review, _ = reviewed(provider=ollama(server, num_ctx=4096))  # the minimum output cannot fit
    assert review.status is ReviewStatus.PARTIAL and server.requests == []
    ai = log.stage(FINISHED, "AI_ANALYSIS")
    assert (ai["status"], ai["error_code"]) == ("FAILED", "AI_CONTEXT_EXCEEDED")
    assert (ai["attempt"], ai["seed"]) == (1, 42) and "num_predict" not in ai
    assert review.result.analysis.ai_analysis.error_code == "AI_CONTEXT_EXCEEDED"


# --- the composed improvement operation (§14, §7.2 stages 10-11) ------------------------------


def with_operation(provider: Any, **parts: Any) -> tuple[Any, str]:
    """A review whose improver is the real ImprovementOperation on the same provider."""
    clock, adapter = FakeClock(), parts.pop("adapter", StubAdapter())
    improver = ImprovementOperation(provider, LanguageRegistry([adapter]), clock, 12_000)
    return reviewed(clock=clock, provider=provider, adapter=adapter, improver=improver, **parts)


def candidate(code: str) -> FakeResponse:
    return FakeResponse(content=json.dumps({"improved_code": code, "notes": [MARK]}))


RENAMED = f"import os\n\n\ndef g(items=None):\n    return '{MARK}'\n"


def test_the_improvement_stage_record_carries_the_observed_call_metrics(log: Log) -> None:
    improved = {"improved_code": f"{IMPROVEMENT['improved_code']}# {MARK}\n", "notes": [MARK]}
    server = Ollama(chat=[envelope(), envelope(improved, prompt_eval_count=700, eval_count=80)])
    review, review_id = with_operation(ollama(server))
    assert review.status is ReviewStatus.COMPLETED
    assert review.result.improved_code.status == "AVAILABLE"
    assert [b["format"]["required"][0] for b in server.bodies] == ["summary", "improved_code"]
    started, finished = log.stage(STARTED, "IMPROVEMENT"), log.stage(FINISHED, "IMPROVEMENT")
    model = {"ai_provider": "ollama", "ai_model": OLLAMA_MODEL, "prompt_version": "v1"}
    assert model.items() <= started.items() and model.items() <= finished.items()
    assert (finished["status"], finished["attempt"], finished["seed"]) == ("SUCCEEDED", 1, 42)
    assert finished["num_predict"] == server.bodies[1]["options"]["num_predict"]
    assert (finished["prompt_eval_count"], finished["eval_count"]) == (700, 80)
    assert finished["thinking_emitted"] == 0 and "token_estimate_exceeded" in finished
    assert log.stage(FINISHED, "AI_ANALYSIS")["prompt_eval_count"] == 900  # its own call
    assert trail(log.of(review_id))[-5:-1] == [
        (STARTED, "IMPROVEMENT", "RUNNING"), *ran("IMPROVEMENT_VALIDATION"),
        (FINISHED, "IMPROVEMENT", "SUCCEEDED"),
    ]  # fmt: skip
    assert MARK not in log.stream.getvalue()  # neither the improved code nor its notes


def test_a_rejected_candidate_is_partial_with_the_score_unchanged(log: Log) -> None:
    accepted, _ = with_operation(FakeAIReviewProvider())
    review, review_id = with_operation(FakeAIReviewProvider(improve_script=[candidate(RENAMED)]))
    assert (accepted.status, review.status) == (ReviewStatus.COMPLETED, ReviewStatus.PARTIAL)
    assert review.result.score == accepted.result.score  # §20.3: score unchanged
    assert review.result.issues == accepted.result.issues
    improvement = review.result.analysis.improvement
    assert (improvement.status, improvement.error_code) == ("FAILED", "IMPROVED_CODE_INVALID")
    assert review.result.improved_code.model_dump() == {
        "status": "UNAVAILABLE", "code": None, "notes": (),
        "failure_code": "IMPROVED_CODE_INVALID", "message": "Improved code could not be validated.",
    }  # fmt: skip
    records = log.of(review_id)
    assert_each_stage_once(records)  # stage 11 logged once, by the operation
    assert trail(records)[-5:-1] == [
        (STARTED, "IMPROVEMENT", "RUNNING"), (STARTED, "IMPROVEMENT_VALIDATION", "RUNNING"),
        (FINISHED, "IMPROVEMENT_VALIDATION", "FAILED"), (FINISHED, "IMPROVEMENT", "FAILED"),
    ]  # fmt: skip
    validation = [r for r in records if r.get("stage") == "IMPROVEMENT_VALIDATION"][1]
    assert validation["rejection_reason"] == "INTERFACE_CHANGED"
    assert MARK not in log.stream.getvalue()


def test_invalid_ai_review_output_still_improves_from_the_static_issues(log: Log) -> None:
    provider = FakeAIReviewProvider(review_script=[INVALID_JSON, INVALID_JSON])
    review, _ = with_operation(provider)
    assert review.status is ReviewStatus.PARTIAL  # the AI review failed, not the improvement
    assert review.result.analysis.ai_analysis.error_code == "AI_OUTPUT_INVALID"
    assert review.result.analysis.improvement.status == "SUCCEEDED"
    assert review.result.improved_code.status == "AVAILABLE"
    (call,) = [c for c in provider.calls if c.operation == "improve"]
    assert isinstance(call.request, AIImprovementRequest) and call.request.issues
    assert {i.provenance for i in call.request.issues} == {"STATIC"}


def test_an_improvement_that_cannot_fit_leaves_the_review_unchanged(log: Log) -> None:
    accepted, _ = with_operation(FakeAIReviewProvider())
    review, review_id = with_operation(FakeAIReviewProvider(improve_script=[CONTEXT_EXCEEDED]))
    assert review.status is ReviewStatus.PARTIAL and review.result.score == accepted.result.score
    improvement = review.result.analysis.improvement
    assert (improvement.status, improvement.error_code) == ("FAILED", "AI_CONTEXT_EXCEEDED")
    improved = review.result.improved_code
    assert (improved.status, improved.failure_code) == ("UNAVAILABLE", "AI_CONTEXT_EXCEEDED")
    validation = [r for r in log.of(review_id) if r.get("stage") == "IMPROVEMENT_VALIDATION"]
    assert [(r["event"], r["skip_reason"]) for r in validation] == [(FINISHED, "DEPENDENCY_FAILED")]


def test_the_deadline_cancels_the_running_operation(log: Log) -> None:
    clock = FakeClock()
    provider = Advancing(  # 1 ms left when the improvement starts; its call would take 5 s
        clock=clock, seconds=299.999, improve_script=[FakeResponse(delay_s=5)]
    )
    improver = ImprovementOperation(provider, LanguageRegistry([StubAdapter()]), clock, 12_000)
    review, review_id = reviewed(
        clock=clock, provider=provider, improver=improver,
        options=OrchestratorOptions(improvement_start_min_s=0),
    )  # fmt: skip
    improvement = review.result.analysis.improvement
    assert (improvement.status, improvement.error_code) == ("FAILED", "REVIEW_TIMEOUT")
    assert review.status is ReviewStatus.PARTIAL
    validation = [r for r in log.of(review_id) if r.get("stage") == "IMPROVEMENT_VALIDATION"]
    assert [r["event"] for r in validation] == [FINISHED]  # never started: no candidate
