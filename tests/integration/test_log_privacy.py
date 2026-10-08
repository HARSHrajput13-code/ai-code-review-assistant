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
from analysis.process import SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.application.deadline import MonotonicDeadline
from backend.application.job_service import ReviewJobService
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.validation import SubmissionValidator
from backend.review.scoring.policy import ScoringPolicyV1
from shared.domain.enums import Language, ReviewStage, ReviewStatus
from shared.domain.errors import RequestRejected
from shared.domain.models import ReviewSubmission, SourceText
from tests.unit.backend.pipeline_doubles import FakeImprover, StubAdapter
from tests.unit.builders import FakeClock

MARK = "LEAK_SENTINEL_7F91"  # every sentinel contains it
SOURCE_SENTINEL = f"UNIQUE_SOURCE_{MARK}"
AI_SENTINEL = f"AI_TEXT_{MARK}"
CODE_SENTINEL = f"IMPROVED_CODE_{MARK}"
ERROR_SENTINEL = f"EXCEPTION_MESSAGE_{MARK}"
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
    """Everything the captured records carry: messages, arguments and exception text."""
    parts = [caplog.text]
    for record in caplog.records:
        parts += [record.getMessage(), repr(record.args), str(record.exc_info)]
    return "\n".join(parts)


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


def test_ai_provider_error(caplog: pytest.LogCaptureFixture) -> None:
    orchestrate(provider=Exploding())
    assert_private(caplog, expect_records=True)


def test_ai_response_processing_error(caplog: pytest.LogCaptureFixture) -> None:
    bad = FakeResponse(content=f'{{"summary": "{AI_SENTINEL}", "issues": "{SOURCE_SENTINEL}"}}')
    outcome = orchestrate(provider=FakeAIReviewProvider(review_script=[bad, bad]))
    assert outcome.status is ReviewStatus.PARTIAL
    assert_private(caplog, expect_records=False)


def test_improvement_error(caplog: pytest.LogCaptureFixture) -> None:
    orchestrate(improver=ExplodingImprover())
    assert_private(caplog, expect_records=True)


def test_unexpected_application_error(caplog: pytest.LogCaptureFixture) -> None:
    outcome = orchestrate(provider=FakeAIReviewProvider(review_script=[FakeResponse(AI_RESPONSE)]),
                          policy=ExplodingPolicy())  # fmt: skip
    assert outcome.status is ReviewStatus.FAILED
    assert_private(caplog, expect_records=True)


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


def test_unexpected_job_error(caplog: pytest.LogCaptureFixture) -> None:
    async def body() -> ReviewStatus:
        service = jobs(FakeClock(), ExplodingRunner())
        review, _ = await service.submit("python", SOURCE, KEY)
        await service.wait_idle()
        return (await service.get(review.review_id)).status

    assert asyncio.run(body()) is ReviewStatus.FAILED
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
    orchestrator = ReviewOrchestrator(
        provider, clock, OrchestratorOptions(), FakeImprover(provider=provider)
    )

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
