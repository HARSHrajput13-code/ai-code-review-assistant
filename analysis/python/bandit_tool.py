"""Bandit 1.9.4: command and JSON parsing → `FindingCandidate` (CIS §8.4, §8.6, §8.7).

Exit 0 is not success on its own: a non-empty `errors` list is a failure (Q7). `--ignore-nosec`
is always passed, so a submitted `# nosec` cannot hide a finding.
"""

import sys
from pathlib import Path

from analysis.process import ProcessResult, ToolCommand
from analysis.python import rules
from analysis.python.candidates import candidate, completed_stdout, is_int, location
from shared.domain.enums import Confidence, Severity
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.models import FindingCandidate

NAME = "Bandit"
CONFIG = Path(__file__).resolve().parent / "config" / "bandit.yaml"
LEVELS = ("HIGH", "MEDIUM", "LOW")

COMMAND = ToolCommand(
    argv=(
        sys.executable, "-I", "-X", "utf8", "-m", "bandit",
        "-c", str(CONFIG), "-f", "json", "-q", "--ignore-nosec", "-",
    )
)  # fmt: skip

# (Bandit severity, Bandit confidence) → severity (§8.4).
SEVERITY = {
    ("HIGH", "HIGH"): Severity.HIGH, ("HIGH", "MEDIUM"): Severity.HIGH,
    ("HIGH", "LOW"): Severity.MEDIUM,
    ("MEDIUM", "HIGH"): Severity.MEDIUM, ("MEDIUM", "MEDIUM"): Severity.MEDIUM,
    ("MEDIUM", "LOW"): Severity.LOW,
    ("LOW", "HIGH"): Severity.LOW, ("LOW", "MEDIUM"): Severity.LOW, ("LOW", "LOW"): Severity.LOW,
}  # fmt: skip


def tool_confidence(bandit_confidence: str) -> Confidence:
    return Confidence.MEDIUM if bandit_confidence == "LOW" else Confidence.HIGH


def _well_formed(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    line_range = result.get("line_range")
    cwe = result.get("issue_cwe")
    return (
        isinstance(result.get("test_id"), str)
        and isinstance(result.get("test_name"), str)
        and isinstance(result.get("issue_text"), str)
        and result.get("issue_severity") in LEVELS
        and result.get("issue_confidence") in LEVELS
        and is_int(result.get("line_number"))
        and (line_range is None or (isinstance(line_range, list) and all(map(is_int, line_range))))
        and (
            cwe is None or (isinstance(cwe, dict) and (cwe.get("id") is None or is_int(cwe["id"])))
        )
    )


def parse(
    result: ProcessResult, line_count: int
) -> tuple[tuple[FindingCandidate, ...], tuple[str, ...]]:
    """Candidates and internal diagnostics, or StaticAnalysisFailed."""
    output = completed_stdout(result, NAME)
    if result.exit_code not in (0, 1):
        raise StaticAnalysisFailed(f"{NAME} did not complete")
    results = output.get("results") if isinstance(output, dict) else None
    errors = output.get("errors") if isinstance(output, dict) else None
    if not isinstance(results, list) or not isinstance(errors, list):
        raise StaticAnalysisFailed(f"{NAME} output has an unexpected structure")
    if errors:
        raise StaticAnalysisFailed(f"{NAME} could not analyse the code")
    if not all(map(_well_formed, results)):
        raise StaticAnalysisFailed(f"{NAME} output has an unexpected structure")

    candidates = []
    for found in results:
        lines = found.get("line_range") or [found["line_number"]]
        cwe_id = (found.get("issue_cwe") or {}).get("id")
        entry = rules.bandit_entry(found["test_id"], found["test_name"], cwe_id)
        severity = SEVERITY[(found["issue_severity"], found["issue_confidence"])]
        where = location(min(lines), max(lines), line_count)
        candidates.append(
            candidate(
                entry,
                severity,
                tool_confidence(found["issue_confidence"]),
                found["issue_text"],
                where,
            )
        )
    return tuple(candidates), ()
