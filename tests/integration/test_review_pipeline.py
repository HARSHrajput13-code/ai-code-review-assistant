"""Real static tools plus the fake AI through the job service (CIS §22.5 PR-03 acceptance)."""

import asyncio
import os
from typing import Any

from ai.fake import FakeAIReviewProvider
from analysis.process import SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.application.job_service import ReviewJobService
from backend.application.job_store import InMemoryReviewJobStore
from backend.application.orchestrator import OrchestratorOptions, ReviewOrchestrator
from backend.application.validation import SubmissionValidator
from shared.domain.enums import Provenance, ReviewStatus, ScoreCap
from shared.domain.models import ReviewResult
from tests.unit.builders import FakeClock

SOURCE = (
    "import os\n"
    "import subprocess\n\n\n"
    "def run(command, items=[]):\n"
    "    return subprocess.call(command, shell=True)\n"
)


def review(source: str) -> tuple[ReviewStatus, ReviewResult | None]:
    async def body() -> tuple[ReviewStatus, ReviewResult | None]:
        clock = FakeClock()
        adapter = PythonLanguageAdapter(
            SafeProcessRunner(dict(os.environ)), StaticAnalysisOptions()
        )
        jobs = ReviewJobService(
            InMemoryReviewJobStore(max_active=3, max_retained=50, ttl_seconds=900),
            SubmissionValidator(LanguageRegistry([adapter]), 12_000, 500),
            ReviewOrchestrator(FakeAIReviewProvider(), clock, OrchestratorOptions()),
            clock,
            max_concurrent=1,
            review_timeout_seconds=300,
        )
        created, _ = await jobs.submit("python", source, "integration-key-0001")
        await jobs.wait_idle()
        done = await jobs.get(created.review_id)
        return done.status, done.result

    return asyncio.run(body())


def test_real_static_analysis_and_fake_ai_produce_a_complete_review() -> None:
    status, result = review(SOURCE)
    assert status is ReviewStatus.COMPLETED and result is not None
    assert result.coverage.complete
    rules = {s.rule_key for i in result.issues for s in i.sources}
    assert {"bandit:B602", "pylint:W0102", "pylint:W0611"} <= rules
    assert Provenance.AI in {i.provenance for i in result.issues}
    assert result.score.caps_applied == (ScoreCap.HIGH_CORRECTNESS_OR_SECURITY,)
    assert result.score.overall == 70


def test_the_review_is_deterministic() -> None:
    _, first = review(SOURCE)
    _, second = review(SOURCE)
    assert first is not None and second is not None
    # Everything except measured wall-clock timings is identical.
    timing: Any = {
        "metadata": True,
        "analysis": {"static_tools": {"__all__": {"outcome": {"duration_ms"}}}},
    }
    assert first.model_dump(exclude=timing) == second.model_dump(exclude=timing)


def test_a_syntax_error_review_is_scored_at_most_20() -> None:
    status, result = review("def broken(:\n    pass\n")
    assert status is ReviewStatus.COMPLETED and result is not None
    assert result.score.overall <= 20
    assert ScoreCap.UNPARSEABLE_SOURCE in result.score.caps_applied
