"""Worst-case prompt inputs for the §9.7 / §10.2 byte-limit tests."""

from shared.domain.enums import Category, Confidence, Language, Severity
from shared.domain.interfaces import AIImprovementRequest, AIReviewRequest
from shared.domain.models import Finding, Issue, SourceText
from tests.unit.builders import issue, static_finding

# One character of each serialization class: plain, two-byte escapes (\" and \\), \uXXXX escapes
# (a control character, Latin-1, CJK) and a non-BMP character escaped as a surrogate pair.
CHARACTERS = {
    "ascii": "a",
    "quote": '"',
    "backslash": "\\",
    "control": "\x01",
    "latin": "é",
    "cjk": "变",
    "astral": "\U0001f600",
}
LONGEST_RULE = "python-parser:syntax-error"  # the longest catalogue rule key


def max_source() -> SourceText:
    """Exactly MAX_SOURCE_BYTES (12,000) and MAX_SOURCE_LINES (500) at the V1 defaults."""
    source = SourceText.of("\n".join(["a" * 23] * 499 + ["a" * 24]))
    assert (source.byte_size, source.line_count) == (12_000, 500)
    return source


def worst_finding(n: int, character: str) -> Finding:
    return static_finding(
        n,
        rule=LONGEST_RULE,
        category=Category.MAINTAINABILITY,
        severity=Severity.CRITICAL,
        line=500,
        title=character * 150,
        summary=character * 600,
    )


def worst_issue(character: str) -> Issue:
    return issue(
        Category.MAINTAINABILITY,
        Severity.CRITICAL,
        Confidence.LOW,
        line=500,
        issue_id="ISS-100000",
        title=character * 150,
        recommendation=character * 800,
    )


def worst_review(character: str = "\U0001f600", extra: int = 0) -> AIReviewRequest:
    """A maximum-size source with 25 worst-case findings, plus `extra` that are omitted."""
    findings = tuple(worst_finding(n, character) for n in range(1, 26))
    filler = (static_finding(26),) * extra
    return AIReviewRequest(
        language=Language.PYTHON, source=max_source(), static_findings=findings + filler
    )


def worst_improvement(character: str = "\U0001f600") -> AIImprovementRequest:
    issues = tuple(worst_issue(character) for _ in range(20))
    return AIImprovementRequest(language=Language.PYTHON, source=max_source(), issues=issues)
