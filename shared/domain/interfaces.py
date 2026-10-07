"""Interfaces implemented outside `shared` (CIS §5.8).

`LanguageAdapter` lists the static-analysis members. `validate_generated_code` is added with the
improvement validation (§14.3, PR-07). The monotonic `Deadline` implementation lives in
backend/application (§7.1); analyzers depend only on this interface.
"""

from typing import Protocol

from shared.domain.enums import Category, Language
from shared.domain.models import (
    SourceText,
    StaticAnalysisResult,
    SyntaxCheck,
    ToolVersion,
)


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

    def tool_versions(self) -> tuple[ToolVersion, ...]: ...
