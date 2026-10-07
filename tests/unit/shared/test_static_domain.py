"""Domain invariants used by static analysis (CIS §5.0-§5.3, §5.6)."""

from typing import Any

import pytest
from pydantic import ValidationError

from shared.domain.enums import (
    Category,
    Confidence,
    ErrorCode,
    LocationStatus,
    OutcomeStatus,
    Provenance,
    Severity,
    SkipReason,
)
from shared.domain.models import (
    Finding,
    FindingCandidate,
    Location,
    SourceText,
    StageOutcome,
    StaticAnalysisResult,
    SyntaxCheck,
    ToolOutcome,
)

STATIC: dict[str, Any] = {
    "provenance": Provenance.STATIC,
    "origin": "pylint",
    "rule_key": "pylint:W0102",
    "severity": Severity.MEDIUM,
    "category": Category.CORRECTNESS,
    "title": "t",
    "summary": "s",
    "impact": "i",
    "recommendation": "r",
    "location": Location(start_line=2, end_line=3),
    "location_status": LocationStatus.SOURCE_MATCHED,
    "tool_confidence": Confidence.HIGH,
}
AI: dict[str, Any] = {
    **STATIC,
    "provenance": Provenance.AI,
    "origin": "ai",
    "rule_key": None,
    "tool_confidence": None,
    "ai_output_index": 0,
}


def test_valid_static_and_ai_candidates() -> None:
    FindingCandidate(**STATIC)
    FindingCandidate(**AI)


@pytest.mark.parametrize(
    "changes",
    [
        {"provenance": Provenance.HYBRID},
        {"origin": "Pylint!"},
        {"rule_key": None},
        {"tool_confidence": None},
        {"title": ""},
        {"location": None},
        {"location_status": LocationStatus.NOT_PROVIDED},
        {"location": None, "location_status": LocationStatus.UNMATCHED},
        {"related_static_ids": ("S1",)},
        {"ai_output_index": 0},
    ],
)
def test_invalid_static_candidate_is_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FindingCandidate(**{**STATIC, **changes})


@pytest.mark.parametrize(
    "changes",
    [{"rule_key": "pylint:W0102"}, {"tool_confidence": Confidence.HIGH}, {"ai_output_index": None}],
)
def test_invalid_ai_candidate_is_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FindingCandidate(**{**AI, **changes})


def test_finding_enforces_text_limits_and_id_kind() -> None:
    Finding(**STATIC, finding_id="S1")
    with pytest.raises(ValidationError):
        Finding(**{**STATIC, "title": "x" * 151}, finding_id="S1")
    with pytest.raises(ValidationError):
        Finding(**STATIC, finding_id="A1")
    with pytest.raises(ValidationError):
        Finding(**STATIC, finding_id="S0")


def test_models_are_frozen_and_reject_extra_fields() -> None:
    candidate = FindingCandidate(**STATIC)
    with pytest.raises(ValidationError):
        candidate.title = "changed"
    with pytest.raises(ValidationError):
        FindingCandidate.model_validate({**STATIC, "unexpected": 1})


def test_location_requires_ordered_positive_lines() -> None:
    with pytest.raises(ValidationError):
        Location(start_line=0, end_line=1)
    with pytest.raises(ValidationError):
        Location(start_line=3, end_line=2)


def test_source_text_measures_and_rejects_invalid_text() -> None:
    source = SourceText.of("a = 1\nb = 2\n")
    assert (source.line_count, source.line(2), source.lines()[-1]) == (3, "b = 2", "")
    for text in ("   \n", "a\x00"):
        with pytest.raises(ValidationError):
            SourceText.of(text)
    with pytest.raises(ValidationError):
        SourceText(text="a\nb", byte_size=3, line_count=1)


@pytest.mark.parametrize(
    ("fields", "valid"),
    [
        ({"status": OutcomeStatus.SUCCEEDED}, True),
        ({"status": OutcomeStatus.SUCCEEDED, "error_code": ErrorCode.INTERNAL_ERROR}, False),
        ({"status": OutcomeStatus.FAILED}, False),
        ({"status": OutcomeStatus.FAILED, "error_code": ErrorCode.STATIC_ANALYSIS_FAILURE}, True),
        ({"status": OutcomeStatus.SKIPPED, "skip_reason": SkipReason.DISABLED}, True),
        ({"status": OutcomeStatus.SKIPPED}, False),
        (
            {
                "status": OutcomeStatus.SKIPPED,
                "skip_reason": SkipReason.DISABLED,
                "error_code": ErrorCode.REVIEW_TIMEOUT,
            },
            False,
        ),
        ({"status": OutcomeStatus.SKIPPED, "skip_reason": SkipReason.DEADLINE_EXCEEDED}, False),
        (
            {
                "status": OutcomeStatus.SKIPPED,
                "skip_reason": SkipReason.DEADLINE_EXCEEDED,
                "error_code": ErrorCode.REVIEW_TIMEOUT,
            },
            True,
        ),
    ],
)
def test_stage_outcome_invariants(fields: dict[str, Any], valid: bool) -> None:
    if valid:
        StageOutcome(**fields)
    else:
        with pytest.raises(ValidationError):
            StageOutcome(**fields)


def test_syntax_check_carries_a_candidate_only_when_invalid() -> None:
    with pytest.raises(ValidationError):
        SyntaxCheck(valid=True, candidate=FindingCandidate(**STATIC))
    with pytest.raises(ValidationError):
        SyntaxCheck(valid=False, candidate=None)


def outcome(tool: str, status: OutcomeStatus) -> ToolOutcome:
    fields: dict[str, Any] = {"status": status}
    if status is OutcomeStatus.FAILED:
        fields["error_code"] = ErrorCode.STATIC_ANALYSIS_FAILURE
    return ToolOutcome(tool=tool, tool_version="1", outcome=StageOutcome(**fields))


def test_static_result_is_usable_only_when_a_tool_succeeded_or_syntax_is_invalid() -> None:
    parser = outcome("python-parser", OutcomeStatus.SUCCEEDED)
    failed = (
        parser,
        outcome("pylint", OutcomeStatus.FAILED),
        outcome("bandit", OutcomeStatus.FAILED),
    )
    one_ok = (
        parser,
        outcome("pylint", OutcomeStatus.FAILED),
        outcome("bandit", OutcomeStatus.SUCCEEDED),
    )
    assert not StaticAnalysisResult(
        syntax_valid=True, tools=failed, candidates=(), diagnostics=()
    ).usable
    assert StaticAnalysisResult(
        syntax_valid=True, tools=one_ok, candidates=(), diagnostics=()
    ).usable
    assert StaticAnalysisResult(
        syntax_valid=False, tools=failed, candidates=(), diagnostics=()
    ).usable


def test_severity_rank_and_canonical_category_order() -> None:
    assert [s.rank for s in Severity] == [4, 3, 2, 1]
    assert [c.value for c in Category] == [
        "CORRECTNESS", "SECURITY", "PERFORMANCE", "READABILITY", "MAINTAINABILITY", "BEST_PRACTICE",
    ]  # fmt: skip
