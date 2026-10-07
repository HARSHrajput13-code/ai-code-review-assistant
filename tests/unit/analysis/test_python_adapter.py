"""PythonLanguageAdapter orchestration of the tools (CIS §5.6, §7.1, §7.2, §8.6, §20.3)."""

import asyncio
from typing import Any

import pytest

from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.review.numbering import number_static
from shared.domain.enums import Category, ErrorCode, Language, OutcomeStatus, SkipReason
from shared.domain.interfaces import LanguageAdapter
from shared.domain.models import SourceText, StaticAnalysisResult, ToolOutcome
from tests.unit.analysis.fakes import (
    BANDIT_CLEAN,
    BANDIT_FIXTURE,
    PYLINT_CLEAN,
    PYLINT_FIXTURE,
    FakeProcessRunner,
    FixedDeadline,
    result,
)

SOURCE = SourceText.of(
    "import os\n\n\ndef collect(item, items=[]):\n    items.append(item)\n    return items\n"
)
VERSIONS = {"pylint": "4.1.2", "bandit": "1.9.4"}


def adapter(
    runner: FakeProcessRunner, versions: dict[str, Any] | None = None, **options: Any
) -> PythonLanguageAdapter:
    return PythonLanguageAdapter(
        runner, StaticAnalysisOptions(**options), VERSIONS if versions is None else versions
    )


def analyze(
    runner: FakeProcessRunner, source: SourceText = SOURCE, deadline: float = 300, **kw: Any
) -> StaticAnalysisResult:
    subject = adapter(runner, **kw)
    return asyncio.run(
        subject.analyze(source, subject.check_syntax(source), FixedDeadline(deadline))
    )


def tools(analysis: StaticAnalysisResult) -> dict[str, ToolOutcome]:
    return {t.tool: t for t in analysis.tools}


def both(
    pylint: Any = PYLINT_FIXTURE,
    bandit: Any = BANDIT_FIXTURE,
    delays: dict[str, float] | None = None,
) -> FakeProcessRunner:
    return FakeProcessRunner({"pylint": pylint, "bandit": bandit}, delays or {})


def test_both_tools_succeed() -> None:
    analysis = analyze(both())
    assert [t.outcome.status for t in analysis.tools] == [OutcomeStatus.SUCCEEDED] * 3
    assert [t.tool for t in analysis.tools] == ["python-parser", "pylint", "bandit"]
    assert [c.origin for c in analysis.candidates] == ["pylint", "pylint", "bandit"]
    assert analysis.syntax_valid and analysis.usable


def test_zero_findings_is_success_not_failure() -> None:
    analysis = analyze(both(PYLINT_CLEAN, BANDIT_CLEAN))
    assert analysis.candidates == ()
    assert {t.outcome.status for t in analysis.tools} == {OutcomeStatus.SUCCEEDED}


def test_syntax_error_skips_the_tools_and_keeps_the_parser_candidate() -> None:
    runner = both()
    analysis = analyze(runner, SourceText.of("def broken(:\n"))
    assert runner.calls == []
    assert tools(analysis)["pylint"].outcome.skip_reason is SkipReason.SYNTAX_ERROR
    assert tools(analysis)["bandit"].outcome.skip_reason is SkipReason.SYNTAX_ERROR
    assert [c.rule_key for c in analysis.candidates] == ["python-parser:syntax-error"]
    assert not analysis.syntax_valid and analysis.usable


def test_disabled_tool_is_skipped_without_a_subprocess() -> None:
    runner = both()
    analysis = analyze(runner, pylint_enabled=False)
    assert tools(analysis)["pylint"].outcome.skip_reason is SkipReason.DISABLED
    assert tools(analysis)["pylint"].outcome.error_code is None
    assert [call[0] for call in runner.calls] == ["bandit"]
    assert {c.origin for c in analysis.candidates} == {"bandit"}


def test_failed_tool_contributes_nothing_and_does_not_affect_the_other() -> None:
    analysis = analyze(both(pylint=result(None, b"", timed_out=True)))
    pylint = tools(analysis)["pylint"].outcome
    assert (pylint.status, pylint.error_code, pylint.message) == (
        OutcomeStatus.FAILED, ErrorCode.STATIC_ANALYSIS_FAILURE, "Pylint timed out",
    )  # fmt: skip
    assert tools(analysis)["bandit"].outcome.status is OutcomeStatus.SUCCEEDED
    assert {c.origin for c in analysis.candidates} == {"bandit"}
    assert analysis.usable


@pytest.mark.parametrize(
    ("pylint", "message"),
    [
        (result(0, b"garbage"), "Pylint output could not be parsed"),
        (result(32, b""), "Pylint output could not be parsed"),
        (result(1, b'{"messages": []}'), "Pylint did not complete"),
        (OSError("spawn failed"), "Pylint could not be run"),
        (RuntimeError("unexpected"), "Pylint could not be run"),
    ],
)
def test_tool_failures_are_mapped_at_the_adapter_boundary(pylint: Any, message: str) -> None:
    outcome = tools(analyze(both(pylint=pylint)))["pylint"].outcome
    assert (outcome.status, outcome.error_code, outcome.message) == (
        OutcomeStatus.FAILED, ErrorCode.STATIC_ANALYSIS_FAILURE, message,
    )  # fmt: skip


def test_both_tools_failing_leaves_static_analysis_unusable() -> None:
    analysis = analyze(both(result(0, b"x"), result(2, b"")))
    assert analysis.candidates == ()
    assert not analysis.usable


def test_missing_tool_fails_without_a_subprocess() -> None:
    runner = both()
    analysis = analyze(runner, versions={"pylint": None, "bandit": "1.9.4"})
    pylint = tools(analysis)["pylint"]
    assert (pylint.tool_version, pylint.outcome.message) == (None, "Pylint is not installed")
    assert [call[0] for call in runner.calls] == ["bandit"]


@pytest.mark.parametrize(("remaining", "expected"), [(300, 30), (12.5, 12.5)])
def test_effective_timeout_is_bounded_by_the_deadline(remaining: float, expected: float) -> None:
    runner = both()
    analyze(runner, deadline=remaining)
    assert {call[3] for call in runner.calls} == {expected}


def test_exhausted_deadline_skips_the_tools() -> None:
    runner = both()
    outcome = tools(analyze(runner, deadline=0))["bandit"].outcome
    assert (outcome.status, outcome.skip_reason, outcome.error_code) == (
        OutcomeStatus.SKIPPED, SkipReason.DEADLINE_EXCEEDED, ErrorCode.REVIEW_TIMEOUT,
    )  # fmt: skip
    assert runner.calls == []


def test_timeout_caused_by_the_overall_deadline_is_a_review_timeout() -> None:
    outcome = tools(analyze(both(pylint=result(None, b"", timed_out=True)), deadline=5))[
        "pylint"
    ].outcome
    assert outcome.error_code is ErrorCode.REVIEW_TIMEOUT


def test_source_travels_only_on_stdin_and_argv_is_constant() -> None:
    hostile = SourceText.of(
        'import os\nos.system("rm -rf / ; $(id) | `whoami` > out")\nx = "\'\\""\n'
    )
    runners = [both(), both()]
    analyze(runners[0])
    analyze(runners[1], hostile)
    assert [c[1] for c in runners[0].calls] == [c[1] for c in runners[1].calls]
    for tool, argv, stdin, _ in runners[1].calls:
        assert stdin == hostile.text.encode("utf-8")
        assert not any("rm -rf" in arg or "whoami" in arg for arg in argv), tool


def test_results_do_not_depend_on_tool_completion_order() -> None:
    slow_pylint, slow_bandit = both(delays={"pylint": 0.05}), both(delays={"bandit": 0.05})
    bandit_first, pylint_first = analyze(slow_pylint), analyze(slow_bandit)
    assert (slow_pylint.finished, slow_bandit.finished) == (
        ["bandit", "pylint"],
        ["pylint", "bandit"],
    )
    assert bandit_first.candidates == pylint_first.candidates
    line_count = SOURCE.line_count
    assert number_static(bandit_first.candidates, line_count) == number_static(
        pylint_first.candidates, line_count
    )


def test_adapter_interface_and_registry() -> None:
    subject: LanguageAdapter = adapter(both())
    assert (subject.language, subject.display_name) == (Language.PYTHON, "Python")
    assert subject.covered_categories("bandit") == (Category.SECURITY, Category.BEST_PRACTICE)
    assert [(v.tool, v.version) for v in subject.tool_versions()][1:] == [
        ("pylint", "4.1.2"),
        ("bandit", "1.9.4"),
    ]
    registry = LanguageRegistry([subject])
    assert registry.get(Language.PYTHON) is subject
    assert registry.languages() == (Language.PYTHON,)
    with pytest.raises(ValueError):
        LanguageRegistry([subject, subject])


def test_installed_versions_are_read_from_package_metadata() -> None:
    subject = PythonLanguageAdapter(both(), StaticAnalysisOptions())
    assert {v.tool: v.version for v in subject.tool_versions()}.items() >= {
        "pylint": "4.1.2",
        "bandit": "1.9.4",
    }.items()
