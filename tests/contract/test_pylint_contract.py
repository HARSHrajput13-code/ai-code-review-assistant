"""Pylint 4.1.2 contract (CIS §8.3, §8.7, §8.8). Runs the real pinned tool."""

import configparser
import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.contract import samples, tools
from tests.contract.cis_rules import PYLINT_RULES, PYLINT_SYSTEM_MESSAGES

FATAL_BIT = 1
USAGE_BIT = 32


def messages(run: tools.ToolRun) -> list[dict[str, Any]]:
    output = json.loads(run.stdout)
    assert isinstance(output, dict)
    assert isinstance(output["messages"], list)
    return output["messages"]


def message_ids(run: tools.ToolRun) -> list[str]:
    return [m["messageId"] for m in messages(run)]


def test_every_enabled_message_exists_in_the_pinned_version() -> None:
    run = tools.run_module(["pylint", f"--rcfile={tools.PYLINTRC}", "--list-msgs-enabled"])
    assert run.exit_code == 0
    enabled_section = run.stdout.split("Enabled messages:")[1].split("Disabled messages:")[0]
    enabled = dict(
        (msg_id, symbol)
        for symbol, msg_id in re.findall(
            r"^\s+([a-z0-9-]+) \(([A-Z]\d{4})\)$", enabled_section, re.M
        )
    )
    for msg_id, symbol, _category in PYLINT_RULES:
        assert enabled.get(msg_id) == symbol, f"{msg_id} {symbol} is not enabled"
    # Nothing else is enabled except the messages Pylint always keeps on.
    assert set(enabled) - {msg_id for msg_id, _, _ in PYLINT_RULES} == PYLINT_SYSTEM_MESSAGES


def test_rcfile_values_match_the_cis() -> None:
    rc = configparser.ConfigParser()
    rc.read(tools.PYLINTRC, encoding="utf-8")
    assert dict(rc["MAIN"]) == {
        "jobs": "1",
        "persistent": "no",
        "load-plugins": "",
        "unsafe-load-any-extension": "no",
        "extension-pkg-allow-list": "",
        "fail-under": "0",
    }
    assert rc["MESSAGES CONTROL"]["disable"] == "all"
    enabled = [s.strip() for s in rc["MESSAGES CONTROL"]["enable"].split(",") if s.strip()]
    assert enabled == [symbol for _, symbol, _ in PYLINT_RULES]
    assert dict(rc["REPORTS"]) == {"reports": "no", "score": "no"}
    assert rc["FORMAT"]["max-line-length"] == "100"
    assert dict(rc["DESIGN"]) == {
        "max-args": "6",
        "max-returns": "6",
        "max-branches": "12",
        "max-statements": "50",
    }
    assert rc["REFACTORING"]["max-nested-blocks"] == "5"


def test_rcfile_has_no_removed_options() -> None:
    assert "suggestion-mode" not in tools.PYLINTRC.read_text(encoding="utf-8")  # D-63


def test_rcfile_loads_without_configuration_messages() -> None:
    run = tools.pylint(samples.CLEAN)
    assert messages(run) == []


def test_unknown_rcfile_option_is_reported_as_e0015_not_through_exit_code(tmp_path: Path) -> None:
    # Configuration validity is read from the messages, never from the exit code alone (§8.7).
    bad = tmp_path / "pylintrc"
    bad.write_text(
        tools.PYLINTRC.read_text(encoding="utf-8").replace(
            "[MAIN]\n", "[MAIN]\nno-such-option=1\n"
        ),
        encoding="utf-8",
    )
    run = tools.pylint(samples.CLEAN, rcfile=bad)
    assert run.exit_code == 0
    assert message_ids(run) == ["E0015"]


def test_json2_output_matches_the_parser_fixture() -> None:
    run = tools.pylint(samples.PYLINT_FINDINGS)
    output = json.loads(run.stdout)
    for message in output["messages"]:
        del message["absolutePath"]  # the temporary directory differs per run
    fixture = json.loads((tools.FIXTURES / "pylint" / "json2_findings.json").read_text("utf-8"))
    assert output == fixture


def test_consumed_json2_fields_are_present() -> None:
    for message in messages(tools.pylint(samples.PYLINT_FINDINGS)):
        assert {"messageId", "line", "endLine", "type", "message"} <= message.keys()


def test_stdin_input_is_linted_as_the_named_module() -> None:
    found = messages(tools.pylint(samples.PYLINT_FINDINGS))
    assert {m["path"] for m in found} == {"submission.py"}
    assert {m["module"] for m in found} == {"submission"}


@pytest.mark.parametrize(
    ("source", "expected_exit", "expected_ids"),
    [
        pytest.param(samples.CLEAN, 0, [], id="clean"),
        # Findings never set the exit code under fail-under=0 (§8.7).
        pytest.param(samples.PYLINT_FINDINGS, 0, ["W0102", "W0611"], id="findings"),
        # A syntax error sets neither the fatal nor the usage bit.
        pytest.param(samples.SYNTAX_INVALID, 2, ["E0001"], id="syntax-error"),
    ],
)
def test_exit_codes_for_stdin_runs(
    source: str, expected_exit: int, expected_ids: list[str]
) -> None:
    run = tools.pylint(source)
    assert run.exit_code == expected_exit
    assert run.exit_code & (FATAL_BIT | USAGE_BIT) == 0
    assert message_ids(run) == expected_ids


def test_usage_error_sets_the_usage_bit() -> None:
    run = tools.run_module(["pylint", f"--rcfile={tools.PYLINTRC}", "--no-such-flag"])
    assert run.exit_code == USAGE_BIT


def test_fatal_error_sets_the_fatal_bit_and_a_fatal_message() -> None:
    run = tools.run_module(
        ["pylint", f"--rcfile={tools.PYLINTRC}", "--output-format=json2", "missing_module_xyz"]
    )
    assert run.exit_code == FATAL_BIT
    assert [m["type"] for m in messages(run)] == ["fatal"]


def test_submitted_inline_option_can_raise_a_system_message() -> None:
    # E0011 is outside the allow-list; the adapter must drop it (§8.7).
    run = tools.pylint("x = 1  # pylint: foo=bar\n")
    assert run.exit_code == 0
    assert message_ids(run) == ["E0011"]
