"""Doubles for orchestrator and job-service tests (CIS §20.2)."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ai.fake import FakeAIReviewProvider
from analysis.python import rules
from analysis.python.syntax import check_syntax, validate_generated_code
from backend.application.orchestrator import ReviewOutcome
from shared.domain.enums import (
    Category,
    ErrorCode,
    ImprovedCodeStatus,
    Language,
    OutcomeStatus,
    ReviewStage,
    ReviewStatus,
    SkipReason,
)
from shared.domain.interfaces import AIImprovementRequest, Deadline, ProviderHealth
from shared.domain.models import (
    CodeValidation,
    FindingCandidate,
    ImprovedCode,
    Issue,
    ReviewFailure,
    ReviewSubmission,
    SourceText,
    StageOutcome,
    StaticAnalysisResult,
    SyntaxCheck,
    ToolOutcome,
    ToolVersion,
)
from tests.unit.builders import static_finding

OK = StageOutcome(status=OutcomeStatus.SUCCEEDED)
FAILED = StageOutcome(status=OutcomeStatus.FAILED, error_code=ErrorCode.STATIC_ANALYSIS_FAILURE)
DISABLED = StageOutcome(status=OutcomeStatus.SKIPPED, skip_reason=SkipReason.DISABLED)
SYNTAX = StageOutcome(status=OutcomeStatus.SKIPPED, skip_reason=SkipReason.SYNTAX_ERROR)

SOURCE = SourceText.of("import os\n\n\ndef f(items=[]):\n    return items\n")
SUBMISSION = ReviewSubmission(language=Language.PYTHON, source=SOURCE)


def candidate(n: int, rule: str = "pylint:W0102", line: int = 4, **kw: object) -> FindingCandidate:
    finding = static_finding(n, rule, line=line, **kw)  # type: ignore[arg-type]
    return FindingCandidate(**finding.model_dump(exclude={"finding_id"}))


DEFAULT_CANDIDATES = (
    candidate(1, "pylint:W0611", line=1),
    candidate(2, "bandit:B602", line=5, category=rules.S, severity=rules.HIGH),
)


@dataclass
class StubAdapter:
    """A LanguageAdapter whose static analysis is scripted; the parse-based checks are real."""

    pylint: StageOutcome = OK
    bandit: StageOutcome = OK
    candidates: tuple[FindingCandidate, ...] = DEFAULT_CANDIDATES
    error: Exception | None = None
    language: Language = Language.PYTHON
    display_name: str = "Python"
    tools_health: ProviderHealth = ProviderHealth(
        available=True, detail="pylint 4.1.2, bandit 1.9.4"
    )

    def covered_categories(self, tool: str) -> tuple[Category, ...]:
        return rules.covered_categories(tool)

    def check_syntax(self, source: SourceText) -> SyntaxCheck:
        return check_syntax(source)

    async def analyze(
        self, source: SourceText, syntax: SyntaxCheck, deadline: Deadline
    ) -> StaticAnalysisResult:
        if self.error is not None:
            raise self.error
        pylint, bandit = (SYNTAX, SYNTAX) if not syntax.valid else (self.pylint, self.bandit)
        tools = tuple(
            ToolOutcome(tool=name, tool_version="1", outcome=outcome)
            for name, outcome in (("pylint", pylint), ("bandit", bandit))
        )
        kept = tuple(
            c for c in self.candidates
            if (pylint if c.origin == "pylint" else bandit).status is OutcomeStatus.SUCCEEDED
        )  # fmt: skip
        syntax_found = (syntax.candidate,) if syntax.candidate else ()
        return StaticAnalysisResult(
            syntax_valid=syntax.valid, tools=tools, candidates=syntax_found + kept, diagnostics=()
        )

    def validate_generated_code(self, original: SourceText, generated: str) -> CodeValidation:
        return validate_generated_code(original, generated)

    def tool_versions(self) -> tuple[ToolVersion, ...]:
        return (ToolVersion(tool="pylint", version="4.1.2"),)

    def health(self) -> ProviderHealth:
        return self.tools_health


@dataclass
class FakeImprover:
    """Stands in for the PR-07 improvement operation; optionally calls the provider."""

    provider: FakeAIReviewProvider | None = None
    result: tuple[StageOutcome, ImprovedCode] | None = None
    calls: list[tuple[Issue, ...]] = field(default_factory=list)

    async def improve(
        self, submission: ReviewSubmission, issues: tuple[Issue, ...], deadline: Deadline
    ) -> tuple[StageOutcome, ImprovedCode]:
        self.calls.append(issues)
        if self.provider is not None:
            request = AIImprovementRequest(
                language=submission.language, source=submission.source, issues=issues
            )
            improved = await self.provider.improve(request, deadline)
            return OK, ImprovedCode(status=ImprovedCodeStatus.AVAILABLE, code=improved.code)
        return self.result or (OK, ImprovedCode(status=ImprovedCodeStatus.AVAILABLE, code="x = 1"))


class GatedRunner:
    """Holds every review until released; then fails it (no result needed)."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.runs = 0

    async def run(self, submission: Any, adapter: Any, deadline: Any, report: Any) -> ReviewOutcome:
        self.runs += 1
        await report(ReviewStage.GENERATING_IMPROVEMENT)
        await self.gate.wait()
        failure = ReviewFailure(code=ErrorCode.INTERNAL_ERROR, message="stub")
        return ReviewOutcome(ReviewStatus.FAILED, failure=failure)
