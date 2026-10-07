"""The bounded ast syntax check (CIS §8.2)."""

import warnings
from pathlib import Path

import pytest

from analysis.python.syntax import UNPARSEABLE_SUMMARY, check_syntax
from shared.domain.enums import Category, Confidence, LocationStatus, Severity
from shared.domain.models import Location, SourceText


def test_valid_source() -> None:
    check = check_syntax(SourceText.of("def add(a, b):\n    return a + b\n"))
    assert check.valid
    assert check.candidate is None


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("def f(:\n    return 1\n", 1),
        ("def f():\nreturn 1\n", 2),  # IndentationError
        ("def f():\n\tif x:\n        return 1\n", 3),  # TabError
        ("x = (\n", 1),
        ("print 'python 2'\n", 1),
    ],
)
def test_syntax_error_becomes_a_located_candidate(text: str, line: int) -> None:
    check = check_syntax(SourceText.of(text))
    assert not check.valid
    found = check.candidate
    assert found is not None
    assert found.rule_key == "python-parser:syntax-error"
    assert (found.severity, found.category, found.tool_confidence) == (
        Severity.CRITICAL,
        Category.CORRECTNESS,
        Confidence.HIGH,
    )
    assert found.title == "Syntax error"
    assert found.location == Location(start_line=line, end_line=line)
    assert found.summary.endswith(f"(line {line})")


def test_too_complex_source_is_unparseable() -> None:
    check = check_syntax(SourceText.of("x = " + "-" * 200_000 + "1\n"))
    assert check.candidate is not None
    assert check.candidate.rule_key == "python-parser:unparseable"
    assert check.candidate.location_status is LocationStatus.NOT_PROVIDED
    assert check.candidate.summary == UNPARSEABLE_SUMMARY


def test_syntax_warning_is_suppressed(capfd: pytest.CaptureFixture[str]) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert check_syntax(SourceText.of("pattern = '\\d+'\n")).valid
    assert caught == []
    assert capfd.readouterr().err == ""


def test_source_is_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    source = f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(1)\n"
    assert check_syntax(SourceText.of(source)).valid
    assert not marker.exists()
