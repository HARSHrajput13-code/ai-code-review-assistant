"""Test doubles for the static-analysis tests (CIS §20.2)."""

import asyncio
import json
from dataclasses import dataclass, field

from analysis.process import ProcessResult, ToolCommand
from tests.contract.tools import FIXTURES


def result(
    exit_code: int | None = 0, stdout: bytes = b"", timed_out: bool = False
) -> ProcessResult:
    return ProcessResult(exit_code, stdout, b"", timed_out, 5)


def pylint_output(*messages: object) -> bytes:
    return json.dumps({"messages": list(messages), "statistics": {}}).encode()


def bandit_output(*results: object, errors: list[object] | None = None) -> bytes:
    return json.dumps({"errors": errors or [], "results": list(results)}).encode()


PYLINT_FIXTURE = result(0, (FIXTURES / "pylint" / "json2_findings.json").read_bytes())
BANDIT_FIXTURE = result(1, (FIXTURES / "bandit" / "json_findings.json").read_bytes())
PYLINT_CLEAN = result(0, pylint_output())
BANDIT_CLEAN = result(0, bandit_output())


def tool_of(command: ToolCommand) -> str:
    return "pylint" if "pylint" in command.argv else "bandit"


@dataclass
class FakeProcessRunner:
    """Scripted results per tool, with optional per-tool delays."""

    results: dict[str, ProcessResult | Exception]
    delays: dict[str, float] = field(default_factory=dict)
    calls: list[tuple[str, tuple[str, ...], bytes, float]] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)

    async def run(self, command: ToolCommand, stdin: bytes, timeout_s: float) -> ProcessResult:
        tool = tool_of(command)
        self.calls.append((tool, command.argv, stdin, timeout_s))
        await asyncio.sleep(self.delays.get(tool, 0))
        self.finished.append(tool)
        outcome = self.results[tool]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass(frozen=True)
class FixedDeadline:
    seconds: float

    def remaining(self) -> float:
        return self.seconds
