"""Pylint json2 parsing and failure detection (CIS §8.6, §8.7, §20.3)."""

from typing import Any

import pytest

from analysis.process import MAX_STDOUT_BYTES
from analysis.python import pylint_tool
from shared.domain.enums import Category, Confidence, LocationStatus, Provenance, Severity
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.models import Location
from tests.unit.analysis.fakes import PYLINT_FIXTURE, pylint_output, result


def message(msg_id: str = "W0102", line: Any = 4, end: Any = 4, **extra: Any) -> dict[str, Any]:
    return {
        "type": "warning",
        "messageId": msg_id,
        "message": "msg",
        "line": line,
        "endLine": end,
        **extra,
    }


def test_command_uses_the_isolated_interpreter_stdin_and_json2() -> None:
    argv = pylint_tool.COMMAND.argv
    assert argv[1:6] == ("-I", "-X", "utf8", "-m", "pylint")
    assert argv[6] == f"--rcfile={pylint_tool.RCFILE}"
    assert argv[7:] == ("--output-format=json2", "--from-stdin", "submission.py")
    assert pylint_tool.RCFILE.is_file()


def test_recorded_fixture_maps_to_candidates() -> None:
    candidates, diagnostics = pylint_tool.parse(PYLINT_FIXTURE, line_count=7)
    assert diagnostics == ()
    by_key = {c.rule_key: c for c in candidates}
    assert set(by_key) == {"pylint:W0102", "pylint:W0611"}
    default = by_key["pylint:W0102"]
    assert (default.provenance, default.origin, default.tool_confidence) == (
        Provenance.STATIC,
        "pylint",
        Confidence.HIGH,
    )
    assert (default.severity, default.category) == (Severity.MEDIUM, Category.CORRECTNESS)
    assert default.title == "Mutable default argument"
    assert default.summary == "Dangerous default value [] as argument"
    assert default.location == Location(start_line=4, end_line=4)
    assert by_key["pylint:W0611"].location == Location(start_line=1, end_line=1)


@pytest.mark.parametrize("exit_code", [0, 2, 4, 8, 16, 30])
def test_findings_and_message_bits_are_not_failures(exit_code: int) -> None:
    candidates, _ = pylint_tool.parse(result(exit_code, pylint_output(message())), line_count=5)
    assert len(candidates) == 1


def test_clean_run_succeeds_with_zero_candidates() -> None:
    assert pylint_tool.parse(result(0, pylint_output()), line_count=5) == ((), ())


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(result(None, b"", timed_out=True), id="timeout"),
        pytest.param(result(0, b" " * (MAX_STDOUT_BYTES + 1)), id="oversize"),
        pytest.param(result(1, pylint_output(message())), id="fatal-bit"),
        pytest.param(result(32, pylint_output()), id="usage-bit"),
        pytest.param(result(33, pylint_output()), id="fatal-and-usage"),
        pytest.param(result(32, b"usage: pylint ..."), id="usage-text"),
        pytest.param(result(0, b"not json"), id="non-json"),
        pytest.param(result(0, b"\xff\xfe"), id="non-utf8"),
        pytest.param(result(0, b"[]"), id="not-an-object"),
        pytest.param(result(0, b'{"statistics": {}}'), id="no-messages"),
        pytest.param(result(0, b'{"messages": {}}'), id="messages-not-a-list"),
        pytest.param(
            result(
                0,
                pylint_output(
                    {
                        "type": "fatal",
                        "messageId": "F0001",
                        "message": "m",
                        "line": 1,
                        "endLine": None,
                    }
                ),
            ),
            id="fatal-message",
        ),
        pytest.param(
            result(0, pylint_output(message(), "not a message")), id="message-not-an-object"
        ),
        pytest.param(
            result(0, pylint_output({"type": "warning", "messageId": "W0102", "line": 1})),
            id="missing-fields",
        ),
        pytest.param(result(0, pylint_output(message(line="4"))), id="line-not-int"),
        pytest.param(result(0, pylint_output(message(line=True))), id="line-bool"),
        pytest.param(result(0, pylint_output(message(end="x"))), id="end-not-int"),
    ],
)
def test_failures_raise_and_contribute_nothing(run: Any) -> None:
    with pytest.raises(StaticAnalysisFailed) as raised:
        pylint_tool.parse(run, line_count=5)
    assert raised.value.code.value == "STATIC_ANALYSIS_FAILURE"
    assert "msg" not in raised.value.safe_message  # never echoes tool text


def test_one_malformed_message_fails_the_whole_run() -> None:
    with pytest.raises(StaticAnalysisFailed):
        pylint_tool.parse(result(0, pylint_output(message(), message(line=None))), line_count=5)


def test_unknown_and_system_messages_are_dropped_with_a_diagnostic() -> None:
    run = result(0, pylint_output(message("E0011"), message("W9999"), message("W0611")))
    candidates, diagnostics = pylint_tool.parse(run, line_count=5)
    assert [c.rule_key for c in candidates] == ["pylint:W0611"]
    assert diagnostics == (
        "dropped unexpected Pylint message E0011",
        "dropped unexpected Pylint message W9999",
    )


@pytest.mark.parametrize(
    ("line", "end", "expected"),
    [
        (2, None, Location(start_line=2, end_line=2)),
        (3, 1, Location(start_line=3, end_line=3)),
        (4, 9, Location(start_line=4, end_line=5)),  # partly out of range: clamped
        (0, 2, Location(start_line=1, end_line=2)),
        (6, 8, None),  # entirely out of range: dropped
        (0, 0, None),
    ],
)
def test_locations_are_clamped_or_dropped(
    line: int, end: int | None, expected: Location | None
) -> None:
    (found,), _ = pylint_tool.parse(
        result(0, pylint_output(message(line=line, end=end))), line_count=5
    )
    assert found.location == expected
    expected_status = LocationStatus.SOURCE_MATCHED if expected else LocationStatus.NOT_PROVIDED
    assert found.location_status is expected_status
