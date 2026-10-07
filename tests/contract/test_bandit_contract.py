"""Bandit 1.9.4 contract (CIS §8.4, §8.7, §8.8). Runs the real pinned tool."""

import json
from typing import Any

from tests.contract import samples, tools
from tests.contract.cis_rules import BANDIT_SKIPS


def output(run: tools.ToolRun) -> dict[str, Any]:
    parsed = json.loads(run.stdout)
    assert isinstance(parsed, dict)
    assert isinstance(parsed["results"], list)
    assert isinstance(parsed["errors"], list)
    return parsed


def test_config_skips_exactly_the_cis_tests() -> None:
    lines = tools.BANDIT_CONFIG.read_text(encoding="utf-8").splitlines()
    skips = [line.split("#")[0].strip(" -") for line in lines if line.strip().startswith("- ")]
    assert tuple(skips) == BANDIT_SKIPS
    assert [line for line in lines if line and not line.startswith(("#", " "))] == ["skips:"]


def test_json_output_matches_the_parser_fixture() -> None:
    run = tools.bandit(samples.BANDIT_FINDINGS)
    parsed = output(run)
    del parsed["generated_at"]
    fixture = json.loads((tools.FIXTURES / "bandit" / "json_findings.json").read_text("utf-8"))
    assert parsed == fixture


def test_consumed_fields_are_present() -> None:
    for result in output(tools.bandit(samples.BANDIT_FINDINGS))["results"]:
        assert {"test_id", "issue_text", "line_number", "line_range"} <= result.keys()


def test_stdin_input_is_scanned() -> None:
    results = output(tools.bandit(samples.BANDIT_FINDINGS))["results"]
    assert {r["filename"] for r in results} == {"<stdin>"}


def test_clean_run_exits_0() -> None:
    run = tools.bandit(samples.CLEAN)
    assert run.exit_code == 0
    assert output(run)["results"] == []
    assert output(run)["errors"] == []


def test_findings_exit_1() -> None:
    run = tools.bandit(samples.BANDIT_FINDINGS)
    assert run.exit_code == 1
    assert [r["test_id"] for r in output(run)["results"]] == ["B602"]
    assert output(run)["errors"] == []


def test_syntax_invalid_input_exits_0_with_errors() -> None:
    # Exit 0 is not success: a non-empty `errors` list is a tool failure (§8.7).
    run = tools.bandit(samples.SYNTAX_INVALID)
    assert run.exit_code == 0
    assert output(run)["errors"] != []
    assert output(run)["results"] == []


def test_nosec_in_submitted_code_cannot_suppress_a_finding() -> None:
    source = samples.BANDIT_FINDINGS.replace("shell=True)", "shell=True)  # nosec")
    run = tools.bandit(source)
    assert [r["test_id"] for r in output(run)["results"]] == ["B602"]


def test_skipped_tests_are_not_reported() -> None:
    source = "import subprocess\n\nassert True\nsubprocess.run(['echo', 'x'], check=False)\n"
    run = tools.bandit(source)
    reported = {r["test_id"] for r in output(run)["results"]}
    assert reported.isdisjoint(BANDIT_SKIPS)
