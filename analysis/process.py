"""SafeProcessRunner: the only subprocess call site in the application (CIS §8.6, §18).

Fixed argv built from constants, `shell=False`, source only on stdin, a fresh empty working
directory, an allow-listed environment, and a timeout that kills the child. Output size is
checked by the adapters against `MAX_STDOUT_BYTES`.

The runner never reads the process environment itself: only backend/config.py does (§16.1).
The composition root passes the parent environment in, and the runner keeps only the allow-list.
"""

import asyncio
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

MAX_STDOUT_BYTES = 2 * 1024 * 1024
ENVIRONMENT_ALLOW_LIST = ("PATH", "SYSTEMROOT", "TEMP", "TMP")


@dataclass(frozen=True)
class ToolCommand:
    argv: tuple[str, ...]


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None  # None when the run timed out
    stdout: bytes
    stderr: bytes  # never parsed, never logged (D-55)
    timed_out: bool
    duration_ms: int


class ProcessRunner(Protocol):
    async def run(self, command: ToolCommand, stdin: bytes, timeout_s: float) -> ProcessResult: ...


class SafeProcessRunner:
    def __init__(self, parent_environment: Mapping[str, str]) -> None:
        self._environment = {
            name: parent_environment[name]
            for name in ENVIRONMENT_ALLOW_LIST
            if name in parent_environment
        }

    async def run(self, command: ToolCommand, stdin: bytes, timeout_s: float) -> ProcessResult:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        return await asyncio.to_thread(self._run, command, stdin, timeout_s)

    def _run(self, command: ToolCommand, stdin: bytes, timeout_s: float) -> ProcessResult:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with tempfile.TemporaryDirectory() as workdir:
            environment = {**self._environment, "PYLINTHOME": workdir}
            started = time.monotonic()
            try:
                completed = subprocess.run(  # noqa: S603 - fixed argv from constants
                    command.argv,
                    input=stdin,
                    capture_output=True,
                    shell=False,
                    check=False,
                    timeout=timeout_s,
                    cwd=workdir,
                    env=environment,
                    creationflags=creationflags,
                )
            except subprocess.TimeoutExpired as expired:  # the child has been killed
                return ProcessResult(
                    exit_code=None,
                    stdout=expired.stdout or b"",
                    stderr=expired.stderr or b"",
                    timed_out=True,
                    duration_ms=_elapsed_ms(started),
                )
        return ProcessResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=False,
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
