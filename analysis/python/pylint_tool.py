"""Pylint 4.1.2: command and `json2` parsing → `FindingCandidate` (CIS §8.3, §8.6, §8.7).

Findings never set the exit code under fail-under=0: only the fatal bit (1), the usage bit (32)
or a `fatal` message mean failure. A failed run contributes no candidates.
"""

import sys
from pathlib import Path

from analysis.process import ProcessResult, ToolCommand
from analysis.python import rules
from analysis.python.candidates import candidate, completed_stdout, is_int, location
from shared.domain.enums import Confidence
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.models import FindingCandidate

NAME = "Pylint"
RCFILE = Path(__file__).resolve().parent / "config" / "pylintrc"
FATAL_BIT = 1
USAGE_BIT = 32

COMMAND = ToolCommand(
    argv=(
        sys.executable, "-I", "-X", "utf8", "-m", "pylint",
        f"--rcfile={RCFILE}", "--output-format=json2", "--from-stdin", "submission.py",
    )
)  # fmt: skip


def parse(
    result: ProcessResult, line_count: int
) -> tuple[tuple[FindingCandidate, ...], tuple[str, ...]]:
    """Candidates and internal diagnostics, or StaticAnalysisFailed."""
    output = completed_stdout(result, NAME)
    if result.exit_code is None or result.exit_code & (FATAL_BIT | USAGE_BIT):
        raise StaticAnalysisFailed(f"{NAME} did not complete")
    messages = output.get("messages") if isinstance(output, dict) else None
    if not isinstance(messages, list):
        raise StaticAnalysisFailed(f"{NAME} output has an unexpected structure")

    parsed = []
    for message in messages:
        if not (
            isinstance(message, dict)
            and isinstance(message.get("type"), str)
            and isinstance(message.get("messageId"), str)
            and isinstance(message.get("message"), str)
            and is_int(message.get("line"))
            and (message.get("endLine") is None or is_int(message["endLine"]))
        ):
            raise StaticAnalysisFailed(f"{NAME} output has an unexpected structure")
        if message["type"] == "fatal":
            raise StaticAnalysisFailed(f"{NAME} reported a fatal error")
        parsed.append(message)

    candidates, diagnostics = [], []
    for message in parsed:
        entry = rules.lookup(f"{rules.PYLINT}:{message['messageId']}")
        if entry is None or entry.severity is None:
            # System messages (for example E0011) are outside the allow-list (§8.7).
            diagnostics.append(f"dropped unexpected Pylint message {message['messageId']}")
            continue
        where = location(message["line"], message["endLine"], line_count)
        candidates.append(
            candidate(entry, entry.severity, Confidence.HIGH, message["message"], where)
        )
    return tuple(candidates), tuple(diagnostics)
