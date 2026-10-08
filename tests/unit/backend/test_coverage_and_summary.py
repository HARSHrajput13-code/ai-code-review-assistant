"""Coverage (CIS §13.2, D-45, D-57) and the generated summary (§12.8)."""

import itertools
from decimal import Decimal
from typing import Any

import pytest

from analysis.python.rules import covered_categories
from backend.review.coverage import coverage
from backend.review.scoring.policy import CATEGORY_WEIGHTS
from backend.review.summary import generated_summary
from shared.domain.enums import Category, ErrorCode, OutcomeStatus, SkipReason
from shared.domain.models import StageOutcome, StaticAnalysisResult, ToolOutcome
from tests.unit.builders import CONF_HIGH, CRITICAL, HIGH, LOW, C, S, issue

OK = StageOutcome(status=OutcomeStatus.SUCCEEDED)
FAILED = StageOutcome(status=OutcomeStatus.FAILED, error_code=ErrorCode.STATIC_ANALYSIS_FAILURE)
DISABLED = StageOutcome(status=OutcomeStatus.SKIPPED, skip_reason=SkipReason.DISABLED)
SYNTAX = StageOutcome(status=OutcomeStatus.SKIPPED, skip_reason=SkipReason.SYNTAX_ERROR)
OUTCOMES = {"succeeded": OK, "failed": FAILED, "disabled": DISABLED}
PYLINT_CATS = {Category.CORRECTNESS, Category.PERFORMANCE, Category.READABILITY,
               Category.MAINTAINABILITY, Category.BEST_PRACTICE}  # fmt: skip
BANDIT_CATS = {Category.SECURITY, Category.BEST_PRACTICE}


def static(
    pylint: StageOutcome, bandit: StageOutcome, syntax_valid: bool = True
) -> StaticAnalysisResult:
    tools = tuple(
        ToolOutcome(tool=t, tool_version="1", outcome=o)
        for t, o in (("pylint", pylint), ("bandit", bandit))
    )
    return StaticAnalysisResult(
        syntax_valid=syntax_valid, tools=tools, candidates=(), diagnostics=()
    )


@pytest.mark.parametrize(
    ("ai", "pylint", "bandit", "syntax_valid"),
    list(itertools.product(OUTCOMES, OUTCOMES, OUTCOMES, (True, False))),
)
def test_assessed_categories_and_completeness_for_every_combination(
    ai: str, pylint: str, bandit: str, syntax_valid: bool
) -> None:
    covered, assessed = coverage(
        OUTCOMES[ai], static(OUTCOMES[pylint], OUTCOMES[bandit], syntax_valid), covered_categories
    )
    expected: set[Category] = set()
    if ai == "succeeded":
        expected |= set(Category)
    if pylint == "succeeded":
        expected |= PYLINT_CATS
    if bandit == "succeeded":
        expected |= BANDIT_CATS
    if not syntax_valid:
        expected.add(Category.CORRECTNESS)
    assert assessed == expected
    weight = sum((CATEGORY_WEIGHTS[c] for c in expected), start=Decimal(0)) * 100
    assert int(weight) == sum(int(CATEGORY_WEIGHTS[c] * 100) for c in expected)
    complete = ai == pylint == bandit == "succeeded"
    assert covered.complete is complete
    missing = [
        name
        for name, state in (("ai", ai), ("pylint", pylint), ("bandit", bandit))
        if state != "succeeded"
    ]
    assert list(covered.missing_components) == missing
    assert covered.unassessed_categories == tuple(c for c in Category if c not in expected)


def test_tools_skipped_for_a_syntax_error_still_count_as_complete() -> None:
    covered, assessed = coverage(OK, static(SYNTAX, SYNTAX, syntax_valid=False), covered_categories)
    assert covered.complete and covered.missing_components == ()
    assert assessed == set(Category)


def test_disabled_component_reduces_coverage_without_failure() -> None:
    covered, _ = coverage(OK, static(DISABLED, OK), covered_categories)
    assert (covered.complete, covered.missing_components) == (False, ("pylint",))


def summary(**kw: Any) -> str:
    return generated_summary(**{"syntax_valid": True, "issues": (), "unassessed": (), **kw})


def test_summary_without_issues() -> None:
    assert summary() == (
        "AI analysis was unavailable, so this review is based on static analysis only. "
        "No issues were detected by the checks that ran."
    )


def test_summary_with_syntax_error_counts_and_unassessed_categories() -> None:
    text = summary(
        syntax_valid=False,
        issues=(issue(C, CRITICAL, CONF_HIGH), issue(S, HIGH, CONF_HIGH), issue(S, LOW, CONF_HIGH)),
        unassessed=(Category.SECURITY, Category.BEST_PRACTICE),
    )
    assert text == (
        "The code contains a syntax error, so it cannot run as written. "
        "AI analysis was unavailable, so this review is based on static analysis only. "
        "3 issue(s) were detected: 1 critical, 1 high, 1 low. "
        "Not assessed: security, best practice."
    )
