"""Exact runtime and tool baselines (CIS §8.8, §21.1, D-79 to D-81)."""

import json
import tomllib
from importlib.metadata import version

import pytest

from tests.contract import tools

PYPROJECT = tomllib.loads((tools.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
LOCK = tomllib.loads((tools.ROOT / "uv.lock").read_text(encoding="utf-8"))
PINS = {"pylint": "4.1.2", "bandit": "1.9.4"}


def test_python_baseline_files() -> None:
    assert (tools.ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14.8"
    assert PYPROJECT["project"]["requires-python"] == ">=3.14.8,<3.15"


def test_node_baseline_files() -> None:
    frontend = tools.ROOT / "frontend"
    assert (frontend / ".nvmrc").read_text(encoding="utf-8").strip() == "24.21.0"
    package = json.loads((frontend / "package.json").read_text(encoding="utf-8"))
    assert package["engines"] == {"node": ">=24.21.0 <25"}


@pytest.mark.parametrize(("name", "pin"), PINS.items())
def test_tool_is_pinned_exactly_in_pyproject(name: str, pin: str) -> None:
    assert f"{name}=={pin}" in PYPROJECT["project"]["dependencies"]


@pytest.mark.parametrize(("name", "pin"), PINS.items())
def test_tool_is_locked_at_the_pin(name: str, pin: str) -> None:
    assert [p["version"] for p in LOCK["package"] if p["name"] == name] == [pin]


@pytest.mark.parametrize(("name", "pin"), PINS.items())
def test_installed_tool_metadata_equals_the_pin(name: str, pin: str) -> None:
    assert version(name) == pin


def test_pylint_executable_reports_the_pin() -> None:
    run = tools.run_module(["pylint", "--version"])
    assert run.exit_code == 0
    assert run.stdout.splitlines()[0] == "pylint 4.1.2"


def test_bandit_executable_reports_the_pin() -> None:
    run = tools.run_module(["bandit", "--version"])
    assert run.exit_code == 0
    first_line = run.stdout.splitlines()[0]  # e.g. "python.exe -m bandit 1.9.4"
    assert "bandit" in first_line
    assert first_line.split()[-1] == "1.9.4"
