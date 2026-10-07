"""SafeProcessRunner isolation with real child processes (CIS §8.6, §8.9, §18)."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

from analysis.process import ENVIRONMENT_ALLOW_LIST, ProcessResult, SafeProcessRunner, ToolCommand


def python(code: str, *args: str) -> ToolCommand:
    return ToolCommand(argv=(sys.executable, "-I", "-c", code, *args))


def run(
    command: ToolCommand, stdin: bytes = b"", timeout: float = 30, env: dict[str, str] | None = None
) -> ProcessResult:
    runner = SafeProcessRunner(dict(os.environ) if env is None else env)
    return asyncio.run(runner.run(command, stdin, timeout))


def test_stdin_is_passed_and_stdout_captured() -> None:
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    payload = "x = 'é'; print($(rm -rf /)); `id` | & ; > out\n".encode()
    result = run(python(echo), payload)
    assert (result.exit_code, result.stdout, result.timed_out) == (0, payload, False)


def test_arguments_are_not_interpreted_by_a_shell() -> None:
    hostile = "a; echo injected && $(whoami) | more > f `id` %PATH%"
    result = run(python("import sys, json; print(json.dumps(sys.argv[1:]))", hostile))
    assert json.loads(result.stdout) == [hostile]


def test_child_runs_in_a_fresh_empty_directory() -> None:
    code = "import os, json; print(json.dumps([os.getcwd(), os.listdir('.')]))"
    workdir, entries = json.loads(run(python(code)).stdout)
    assert entries == []
    assert Path(workdir).resolve() != Path.cwd().resolve()
    assert not Path(workdir).exists()  # removed after the run


def test_environment_is_reduced_to_the_allow_list() -> None:
    parent = {**os.environ, "PYTHONPATH": "injected", "PYLINTRC": "injected", "SECRET_VALUE": "x"}
    code = "import os, json; print(json.dumps(sorted(os.environ)))"
    seen = set(json.loads(run(python(code), env=parent).stdout))
    expected = {name for name in ENVIRONMENT_ALLOW_LIST if name in parent} | {"PYLINTHOME"}
    # Windows may add its own process variables; nothing from the parent beyond the allow-list.
    assert expected <= seen
    assert seen.isdisjoint({"PYTHONPATH", "PYLINTRC", "SECRET_VALUE"})
    assert seen.isdisjoint(set(parent) - set(ENVIRONMENT_ALLOW_LIST))


def test_project_modules_are_not_importable_by_the_child() -> None:
    result = run(python("import analysis"))
    assert result.exit_code != 0


def test_nonzero_exit_code_is_reported() -> None:
    assert run(python("raise SystemExit(3)")).exit_code == 3


def test_timeout_kills_the_child() -> None:
    started = time.monotonic()
    result = run(python("import time; time.sleep(60)"), timeout=1)
    assert result.timed_out
    assert result.exit_code is None
    assert time.monotonic() - started < 20


def test_missing_executable_raises() -> None:
    with pytest.raises(OSError):
        run(ToolCommand(argv=("definitely-not-an-executable-xyz",)))


def test_non_positive_timeout_is_rejected() -> None:
    with pytest.raises(ValueError):
        run(python("pass"), timeout=0)


def test_runs_do_not_block_the_event_loop() -> None:
    runner = SafeProcessRunner(dict(os.environ))
    sleeper = python("import time; time.sleep(2)")

    async def both() -> float:
        started = time.monotonic()
        await asyncio.gather(runner.run(sleeper, b"", 30), runner.run(sleeper, b"", 30))
        return time.monotonic() - started

    assert asyncio.run(both()) < 3.8  # concurrent: well under two sequential runs
