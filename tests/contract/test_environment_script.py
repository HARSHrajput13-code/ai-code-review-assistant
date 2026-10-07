"""scripts/check_environment.py and the environment record (CIS §20.10, §21.3, §21.4)."""

import json
from typing import Any

from scripts import check_environment as env

PINS = {"python": "3.14.8", "node": "24.21.0", "pylint": "4.1.2", "bandit": "1.9.4"}
INSTALLED: dict[str, str | None] = {
    **PINS,
    "npm": "11.19.0",
    "uv": "0.12.23",
    "ollama": None,
}
RECORD: dict[str, Any] = {"versions": dict(INSTALLED)}


def statuses(checks: list[env.Check]) -> dict[str, str]:
    return {check.name: check.status for check in checks}


def test_pins_are_read_from_the_project_files() -> None:
    assert env.pinned_versions() == PINS


def test_matching_environment_passes() -> None:
    checks = env.evaluate(PINS, INSTALLED, RECORD)
    assert not any(check.failed for check in checks)


def test_pin_mismatch_fails() -> None:
    checks = env.evaluate(PINS, {**INSTALLED, "pylint": "4.1.3"}, RECORD)
    assert statuses(checks)["pylint"] == "mismatch"
    assert any(check.failed for check in checks)


def test_missing_required_tool_fails() -> None:
    checks = env.evaluate(PINS, {**INSTALLED, "node": None}, RECORD)
    assert statuses(checks)["node"] == "missing"
    assert any(check.failed for check in checks)


def test_recorded_version_mismatch_fails() -> None:
    checks = env.evaluate(PINS, {**INSTALLED, "npm": "11.20.0"}, RECORD)
    assert statuses(checks)["npm"] == "mismatch"


def test_unreachable_ollama_is_an_environment_condition_not_a_failure() -> None:
    recorded = {"versions": {**INSTALLED, "ollama": "0.12.0"}}
    checks = env.evaluate(PINS, INSTALLED, recorded)
    assert statuses(checks)["ollama"] == "unavailable"
    assert not any(check.failed for check in checks)


def test_different_ollama_version_fails() -> None:
    recorded = {"versions": {**INSTALLED, "ollama": "0.12.0"}}
    checks = env.evaluate(PINS, {**INSTALLED, "ollama": "0.13.0"}, recorded)
    assert statuses(checks)["ollama"] == "mismatch"


def test_ollama_installed_after_the_record_needs_recording() -> None:
    checks = env.evaluate(PINS, {**INSTALLED, "ollama": "0.13.0"}, RECORD)
    assert statuses(checks)["ollama"] == "unrecorded"
    assert not any(check.failed for check in checks)


def keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys(v)}
    return set()


def test_committed_record_is_complete_and_matches_the_pins() -> None:
    record = json.loads(env.RECORD.read_text(encoding="utf-8"))
    assert set(record) == {"hardware", "os", "versions", "git_commit"}
    assert set(record["hardware"]) == {"cpu", "ram_bytes", "gpus"}
    assert set(record["os"]) == {"edition", "build", "architecture"}
    assert set(record["versions"]) == {"python", "node", "npm", "uv", "ollama", "pylint", "bandit"}
    assert {name: record["versions"][name] for name in PINS} == PINS
    for forbidden in ("serial", "uuid", "device", "product"):
        assert not any(forbidden in key.lower() for key in keys(record))
