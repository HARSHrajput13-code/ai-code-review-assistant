"""Bandit JSON parsing, severity mapping and failure detection (CIS §8.4, §8.7, §20.3)."""

from typing import Any

import pytest

from analysis.process import MAX_STDOUT_BYTES
from analysis.python import bandit_tool
from shared.domain.enums import Category, Confidence, Severity
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.models import Location
from tests.unit.analysis.fakes import BANDIT_FIXTURE, bandit_output, result


def finding(
    test_id: str = "B602", severity: str = "HIGH", confidence: str = "HIGH", **extra: Any
) -> dict[str, Any]:
    return {
        "test_id": test_id,
        "test_name": "subprocess_popen_with_shell_equals_true",
        "issue_text": "text",
        "issue_severity": severity,
        "issue_confidence": confidence,
        "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"},
        "line_number": 3,
        "line_range": [3],
        **extra,
    }


def parse_one(**fields: Any) -> Any:
    (found,), _ = bandit_tool.parse(result(1, bandit_output(finding(**fields))), line_count=10)
    return found


def test_command_uses_the_isolated_interpreter_stdin_json_and_ignore_nosec() -> None:
    argv = bandit_tool.COMMAND.argv
    assert argv[1:6] == ("-I", "-X", "utf8", "-m", "bandit")
    assert argv[6:] == ("-c", str(bandit_tool.CONFIG), "-f", "json", "-q", "--ignore-nosec", "-")
    assert bandit_tool.CONFIG.is_file()


def test_recorded_fixture_maps_to_a_candidate() -> None:
    (found,), diagnostics = bandit_tool.parse(BANDIT_FIXTURE, line_count=5)
    assert diagnostics == ()
    assert (found.rule_key, found.origin) == ("bandit:B602", "bandit")
    assert (found.severity, found.category, found.tool_confidence) == (
        Severity.HIGH,
        Category.SECURITY,
        Confidence.HIGH,
    )
    assert found.title == "Subprocess call with shell=True"
    assert found.location == Location(start_line=5, end_line=5)


@pytest.mark.parametrize(
    ("severity", "confidence", "expected"),
    [
        ("HIGH", "HIGH", Severity.HIGH),
        ("HIGH", "MEDIUM", Severity.HIGH),
        ("HIGH", "LOW", Severity.MEDIUM),
        ("MEDIUM", "HIGH", Severity.MEDIUM),
        ("MEDIUM", "MEDIUM", Severity.MEDIUM),
        ("MEDIUM", "LOW", Severity.LOW),
        ("LOW", "HIGH", Severity.LOW),
        ("LOW", "MEDIUM", Severity.LOW),
        ("LOW", "LOW", Severity.LOW),
    ],
)
def test_severity_matrix(severity: str, confidence: str, expected: Severity) -> None:
    found = parse_one(severity=severity, confidence=confidence)
    assert found.severity is expected
    assert found.tool_confidence is (Confidence.MEDIUM if confidence == "LOW" else Confidence.HIGH)
    assert found.severity is not Severity.CRITICAL


def test_try_except_rules_are_best_practice() -> None:
    assert parse_one(test_id="B110").category is Category.BEST_PRACTICE
    assert parse_one(test_id="B112").category is Category.BEST_PRACTICE


def test_test_without_catalogue_entry_uses_the_generic_texts() -> None:
    found = parse_one(
        test_id="B104", test_name="hardcoded_bind_all_interfaces", issue_cwe={"id": 605}
    )
    assert found.rule_key == "bandit:B104"
    assert found.title == "Hardcoded bind all interfaces"
    assert found.impact == "This pattern is commonly associated with a security weakness (CWE-605)."
    assert found.recommendation == "Review this usage and replace it with a safe alternative."
    assert found.category is Category.SECURITY


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"line_range": [3, 4, 5], "line_number": 3}, Location(start_line=3, end_line=5)),
        ({"line_range": [], "line_number": 7}, Location(start_line=7, end_line=7)),
        ({"line_range": None, "line_number": 2}, Location(start_line=2, end_line=2)),
        ({"line_range": [9, 12], "line_number": 9}, Location(start_line=9, end_line=10)),
        ({"line_range": [11, 12], "line_number": 11}, None),
    ],
)
def test_locations_from_line_range(fields: dict[str, Any], expected: Location | None) -> None:
    assert parse_one(**fields).location == expected


def test_clean_run_succeeds_with_zero_candidates() -> None:
    assert bandit_tool.parse(result(0, bandit_output()), line_count=5) == ((), ())


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(result(None, b"", timed_out=True), id="timeout"),
        pytest.param(result(1, b" " * (MAX_STDOUT_BYTES + 1)), id="oversize"),
        pytest.param(result(2, bandit_output()), id="exit-2"),
        pytest.param(result(None, bandit_output()), id="no-exit-code"),
        pytest.param(result(0, b"not json"), id="non-json"),
        pytest.param(result(0, b"[]"), id="not-an-object"),
        pytest.param(result(0, b'{"errors": []}'), id="no-results"),
        pytest.param(result(0, b'{"results": []}'), id="no-errors-list"),
        # Exit 0 with a non-empty errors list is a failure (Q7, §8.7).
        pytest.param(
            result(0, bandit_output(errors=[{"filename": "<stdin>", "reason": "syntax error"}])),
            id="errors-at-exit-0",
        ),
        pytest.param(
            result(1, bandit_output(finding(), errors=[{"reason": "x"}])), id="errors-with-results"
        ),
        pytest.param(
            result(1, bandit_output(finding(issue_severity="UNDEFINED"))), id="unknown-severity"
        ),
        pytest.param(result(1, bandit_output(finding(line_number="3"))), id="line-not-int"),
        pytest.param(result(1, bandit_output(finding(line_range=["3"]))), id="range-not-ints"),
        pytest.param(result(1, bandit_output({"test_id": "B602"})), id="missing-fields"),
        pytest.param(
            result(1, bandit_output(finding(), "not a result")), id="result-not-an-object"
        ),
    ],
)
def test_failures_raise_and_contribute_nothing(run: Any) -> None:
    with pytest.raises(StaticAnalysisFailed) as raised:
        bandit_tool.parse(run, line_count=5)
    assert raised.value.code.value == "STATIC_ANALYSIS_FAILURE"
    assert "text" not in raised.value.safe_message
