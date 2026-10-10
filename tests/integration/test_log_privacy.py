"""Protected data never reaches the logs, at any level (CIS §18 D-55, §19.1, §20.3).

Unique sentinels are placed in the source (including an identifier that flows into a Pylint
message), the AI's issue text and summary, the improved code, exception messages raised by
failing components, and the Idempotency-Key. Every log record is captured at DEBUG and searched.
(The API and uvicorn loggers arrive with PR-04; this covers the application layer.)
"""

import asyncio
import json
import logging
import os
from typing import Any

import pytest

from ai.fake import FakeAIReviewProvider, FakeResponse
from analysis.process import ProcessResult, SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.application.deadline import MonotonicDeadline
from backend.application.improvement import ImprovementOperation
from backend.application.job_service import ReviewJobService, fingerprint
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.validation import SubmissionValidator
from backend.logging_setup import ConsoleFormatter, JsonFormatter
from backend.review.scoring.policy import ScoringPolicyV1
from shared.domain.enums import Language, ReviewStage, ReviewStatus
from shared.domain.errors import RequestRejected
from shared.domain.interfaces import AIReviewRequest
from shared.domain.models import ReviewSubmission, SourceText
from tests.unit.analysis.fakes import FakeProcessRunner
from tests.unit.backend.pipeline_doubles import FakeImprover, StubAdapter, candidate
from tests.unit.builders import FakeClock

MARK = "LEAK_SENTINEL_7F91"  # every sentinel contains it
SOURCE_SENTINEL = f"UNIQUE_SOURCE_{MARK}"
AI_SENTINEL = f"AI_TEXT_{MARK}"
CODE_SENTINEL = f"IMPROVED_CODE_{MARK}"
ERROR_SENTINEL = f"EXCEPTION_MESSAGE_{MARK}"
PROMPT_SENTINEL = f"PROMPT_INPUT_{MARK}"
THINKING_SENTINEL = f"THINKING_{MARK}"
STDERR_SENTINEL = f"TOOL_STDERR_{MARK}"
KEY = f"key-{MARK}-0001"

SOURCE = (
    "import subprocess\n\n\n"
    "def run(command, items=[]):\n"
    f"    {SOURCE_SENTINEL} = 'secret {SOURCE_SENTINEL}'\n"
    "    return subprocess.call(command, shell=True)\n"
)
SUBMISSION = ReviewSubmission(language=Language.PYTHON, source=SourceText.of(SOURCE))

AI_RESPONSE = json.dumps(
    {
        "summary": f"Summary {AI_SENTINEL}.",
        "issues": [
            {
                "severity": "HIGH",
                "category": "SECURITY",
                "title": f"Title {AI_SENTINEL}",
                "line": 6,
                "end_line": 6,
                "evidence": "    return subprocess.call(command, shell=True)",
                "summary": f"Summary {AI_SENTINEL}",
                "impact": f"Impact {AI_SENTINEL}",
                "recommendation": f"Recommendation {AI_SENTINEL}",
                "related_static_ids": [],
            }
        ],
    }
)
IMPROVED = json.dumps({"improved_code": f"# {CODE_SENTINEL}\n{SOURCE}", "notes": [AI_SENTINEL]})


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Everything the captured records carry (messages, arguments, exception text, every
    attribute), and each record as the production JSON and console formatters render it."""
    parts = [caplog.text]
    for record in caplog.records:
        parts += [record.getMessage(), repr(record.args), str(record.exc_info), repr(vars(record))]
        parts += [JsonFormatter().format(record), ConsoleFormatter().format(record)]
    return "\n".join(parts)


def assert_diagnosed(caplog: pytest.LogCaptureFixture, exception_type: str) -> None:
    """The failure path logged its exception (type and frames), so the check is not vacuous."""
    diagnosed = [r for r in caplog.records if getattr(r, "exception_type", None) == exception_type]
    assert diagnosed and all(getattr(r, "traceback", None) for r in diagnosed)


def assert_private(caplog: pytest.LogCaptureFixture, *, expect_records: bool) -> None:
    text = logged(caplog)
    assert MARK not in text
    assert "secret" not in text and "subprocess.call" not in text  # no source fragment
    if expect_records:  # the failure path really logged, so the check is not vacuous
        assert caplog.records
        assert all(r.exc_info is None for r in caplog.records)


def orchestrate(
    adapter: Any = None, provider: Any = None, improver: Any = None, policy: Any = None
) -> Any:
    clock = FakeClock()
    orchestrator = ReviewOrchestrator(
        provider or FakeAIReviewProvider(), clock, OrchestratorOptions(), improver, policy
    )

    async def report(stage: ReviewStage) -> None:
        pass

    return asyncio.run(
        orchestrator.run(
            SUBMISSION, adapter or StubAdapter(), MonotonicDeadline(clock, 300), report
        )
    )


@pytest.fixture(autouse=True)
def debug_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


class Exploding(FakeAIReviewProvider):
    async def review(self, request: Any, deadline: Any) -> Any:
        raise RuntimeError(f"{ERROR_SENTINEL} {request.source.text}")


class ExplodingImprover(FakeImprover):
    async def improve(self, submission: Any, *args: Any) -> Any:
        raise RuntimeError(f"{ERROR_SENTINEL} {submission.source.text}")


class ExplodingPolicy(ScoringPolicyV1):
    def score(self, issues: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError(f"{ERROR_SENTINEL} {[i.summary for i in issues]}")


def test_static_analysis_failure(caplog: pytest.LogCaptureFixture) -> None:
    orchestrate(StubAdapter(error=RuntimeError(f"{ERROR_SENTINEL} {SOURCE}")))
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "RuntimeError")


def test_ai_provider_error(caplog: pytest.LogCaptureFixture) -> None:
    orchestrate(provider=Exploding())
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "RuntimeError")


def test_ai_response_processing_error(caplog: pytest.LogCaptureFixture) -> None:
    bad = FakeResponse(content=f'{{"summary": "{AI_SENTINEL}", "issues": "{SOURCE_SENTINEL}"}}')
    outcome = orchestrate(provider=FakeAIReviewProvider(review_script=[bad, bad]))
    assert outcome.status is ReviewStatus.PARTIAL
    assert_private(caplog, expect_records=False)


def test_improvement_error(caplog: pytest.LogCaptureFixture) -> None:
    orchestrate(improver=ExplodingImprover())
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "RuntimeError")


class ExplodingRunner:
    async def run(self, submission: Any, *args: Any) -> Any:
        raise RuntimeError(f"{ERROR_SENTINEL} {submission.source.text}")


def jobs(clock: FakeClock, runner: Any, adapter: Any = None) -> ReviewJobService:
    return ReviewJobService(
        InMemoryReviewJobStore(max_active=3, max_retained=50, ttl_seconds=900),
        SubmissionValidator(LanguageRegistry([adapter or StubAdapter()]), 12_000, 500),
        runner,
        clock,
        max_concurrent=1,
        review_timeout_seconds=300,
    )


def reviewed(service: ReviewJobService) -> Any:
    """One review with the sentinel key, run to its terminal state."""

    async def body() -> Any:
        review, _ = await service.submit("python", SOURCE, KEY)
        await service.wait_idle()
        return await service.get(review.review_id)

    return asyncio.run(body())


def test_unexpected_application_error(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock()
    provider = FakeAIReviewProvider(review_script=[FakeResponse(AI_RESPONSE)])
    orchestrator = ReviewOrchestrator(
        provider, clock, OrchestratorOptions(), None, ExplodingPolicy()
    )
    assert reviewed(jobs(clock, orchestrator)).status is ReviewStatus.FAILED
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "ValueError")  # on review.finished: the review failed


def test_unexpected_job_error(caplog: pytest.LogCaptureFixture) -> None:
    assert reviewed(jobs(FakeClock(), ExplodingRunner())).status is ReviewStatus.FAILED
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "RuntimeError")


def test_prompt_input_thinking_and_fingerprint_never_reach_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    provider = FakeAIReviewProvider(
        review_script=[FakeResponse(AI_RESPONSE, thinking=f"Thinking {THINKING_SENTINEL}")]
    )
    # A static finding's text is part of the AI request: the prompt's input until PR-06 renders it.
    adapter = StubAdapter(candidates=(candidate(1, summary=f"Unused {PROMPT_SENTINEL}"),))
    improver = ExplodingImprover(provider=provider)
    orchestrator = ReviewOrchestrator(provider, clock, OrchestratorOptions(), improver)
    done = reviewed(jobs(clock, orchestrator, adapter))
    assert done.status is ReviewStatus.PARTIAL
    # The sentinels really flowed: into the AI request, the counted thinking, the issue text.
    request = provider.calls[0].request
    assert isinstance(request, AIReviewRequest)
    assert PROMPT_SENTINEL in request.static_findings[0].summary
    assert provider.thinking_emitted == 1
    assert any(AI_SENTINEL in i.title for i in done.result.issues)
    text = logged(caplog)
    assert fingerprint("python", SOURCE) not in text and KEY not in text
    assert_private(caplog, expect_records=True)
    assert_diagnosed(caplog, "RuntimeError")  # the improvement failure path really logged


def test_raw_tool_stderr_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    stderr = f"Traceback: {STDERR_SENTINEL} {SOURCE}".encode()
    runner = FakeProcessRunner(
        {
            "pylint": ProcessResult(32, b"", stderr, False, 5),
            "bandit": ProcessResult(2, b"", stderr, False, 5),
        }
    )
    versions = {"pylint": "4.1.2", "bandit": "1.9.4"}
    adapter = PythonLanguageAdapter(runner, StaticAnalysisOptions(), versions)
    outcome = orchestrate(adapter)
    tools = {t.tool: t.outcome.status.value for t in outcome.result.analysis.static_tools}
    assert (tools["pylint"], tools["bandit"]) == ("FAILED", "FAILED")
    failed = [r for r in caplog.records if getattr(r, "stage", None) == "STATIC_ANALYSIS"]
    assert [getattr(r, "status", None) for r in failed][-1] == "FAILED"
    assert_private(caplog, expect_records=True)


@pytest.mark.parametrize(
    "source",
    [f"x = '{SOURCE_SENTINEL}'\x00", f"# {SOURCE_SENTINEL}\n" * 600, f"# {SOURCE_SENTINEL}\ud800"],
    ids=["nul", "too-many-lines", "lone-surrogate"],
)
def test_validation_failures(caplog: pytest.LogCaptureFixture, source: str) -> None:
    async def body() -> None:
        service = jobs(FakeClock(), ExplodingRunner())
        with pytest.raises(RequestRejected) as raised:
            await service.submit("python", source, KEY)
        assert MARK not in str(raised.value) and MARK not in raised.value.safe_message

    asyncio.run(body())
    assert_private(caplog, expect_records=False)


def test_a_full_review_with_real_tools_logs_no_protected_data(
    caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    provider = FakeAIReviewProvider(
        review_script=[FakeResponse(AI_RESPONSE)], improve_script=[FakeResponse(IMPROVED)]
    )
    adapter = PythonLanguageAdapter(SafeProcessRunner(dict(os.environ)), StaticAnalysisOptions())
    improver = ImprovementOperation(provider, LanguageRegistry([adapter]), clock, 12_000)
    orchestrator = ReviewOrchestrator(provider, clock, OrchestratorOptions(), improver)

    async def body() -> Any:
        service = jobs(clock, orchestrator, adapter)
        review, _ = await service.submit("python", SOURCE, KEY)
        await service.wait_idle()
        return await service.get(review.review_id)

    done = asyncio.run(body())
    assert done.status is ReviewStatus.COMPLETED and done.result is not None
    # The sentinels really flowed through: a Pylint message, the AI text and the improved code.
    assert any(SOURCE_SENTINEL in i.summary for i in done.result.issues)
    assert any(AI_SENTINEL in i.title for i in done.result.issues)
    assert CODE_SENTINEL in (done.result.improved_code.code or "")
    assert_private(caplog, expect_records=False)
    out, err = capfd.readouterr()
    assert MARK not in out and MARK not in err


def test_a_rejected_improvement_logs_its_reason_but_no_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    renamed = json.dumps(
        {"improved_code": f"def {CODE_SENTINEL}():\n    pass\n", "notes": [AI_SENTINEL]}
    )
    clock = FakeClock()
    provider = FakeAIReviewProvider(improve_script=[FakeResponse(renamed)])
    adapter = StubAdapter()
    improver = ImprovementOperation(provider, LanguageRegistry([adapter]), clock, 12_000)
    outcome = orchestrate(adapter=adapter, provider=provider, improver=improver)
    assert outcome.result.analysis.improvement.error_code == "IMPROVED_CODE_INVALID"
    reasons = [getattr(r, "rejection_reason", None) for r in caplog.records]
    assert "INTERFACE_CHANGED" in reasons  # the failure path really logged
    assert_private(caplog, expect_records=True)
