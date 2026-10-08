"""Interfaces implemented outside `shared` (CIS §5.8), and the values that cross them.

The AI request and result types are defined here because the `AIReviewProvider` Protocol refers
to them and `shared` imports nothing internal (§3.1); `ai.provider` re-exports them (§4).
`ReviewRecordRepository` is implemented in `backend/persistence` (§19.2).
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import Field

from shared.domain.enums import Category, Language
from shared.domain.models import (
    AIReviewResult,
    CodeValidation,
    DomainModel,
    Finding,
    Issue,
    ReviewRecord,
    ReviewSubmission,
    SourceText,
    StaticAnalysisResult,
    SyntaxCheck,
    ToolVersion,
)
from shared.domain.review import CodeReview


class Deadline(Protocol):
    def remaining(self) -> float:
        """Seconds left before the overall review deadline; may be zero or negative."""
        ...


class LanguageAdapter(Protocol):
    @property
    def language(self) -> Language: ...

    @property
    def display_name(self) -> str: ...

    def covered_categories(self, tool: str) -> tuple[Category, ...]: ...

    def check_syntax(self, source: SourceText) -> SyntaxCheck: ...

    async def analyze(
        self, source: SourceText, syntax: SyntaxCheck, deadline: Deadline
    ) -> StaticAnalysisResult: ...

    def validate_generated_code(self, original: SourceText, generated: str) -> CodeValidation:
        """Structural checks on generated code (§14.3 steps 5-6); it is never executed."""
        ...

    def tool_versions(self) -> tuple[ToolVersion, ...]: ...


class AIReviewRequest(DomainModel):
    language: Language
    source: SourceText
    static_findings: tuple[Finding, ...]  # numbered (§12.1)


class AIImprovementRequest(DomainModel):
    language: Language
    source: SourceText
    issues: tuple[Issue, ...]


class AIImprovementResult(DomainModel):
    code: str  # the raw candidate; validated by §14.3
    notes: tuple[str, ...] = Field(max_length=10)
    attempts: int = Field(ge=1, le=2)


class ProviderDescriptor(DomainModel):
    provider: str
    model: str
    prompt_version: str


class ProviderHealth(DomainModel):
    available: bool
    detail: str


class AIReviewProvider(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    async def review(self, request: AIReviewRequest, deadline: Deadline) -> AIReviewResult: ...

    async def improve(
        self, request: AIImprovementRequest, deadline: Deadline
    ) -> AIImprovementResult: ...

    async def check_health(self, timeout_s: float) -> ProviderHealth: ...


class ReviewRecordRepository(Protocol):
    async def save(self, record: ReviewRecord) -> None:
        """Best effort: a failure is logged as a WARNING and never changes the review."""
        ...

    def check_health(self) -> bool: ...


class IdempotencyRecord(DomainModel):
    key: str
    fingerprint: str  # in memory only; never logged or persisted (§6.3)
    review_id: UUID


class ReviewJobStore(Protocol):
    async def find_by_idempotency_key(self, key: str) -> tuple[UUID, str] | None: ...

    async def create_or_replay(
        self,
        review: CodeReview,
        submission: ReviewSubmission,
        idempotency: IdempotencyRecord | None,
    ) -> tuple[CodeReview, bool]: ...

    async def get(self, review_id: UUID) -> CodeReview | None: ...

    async def replace(self, review: CodeReview) -> None: ...

    async def count_active(self) -> int: ...

    async def active_reviews(self) -> tuple[CodeReview, ...]: ...

    async def purge_expired(self, now: datetime) -> None: ...
