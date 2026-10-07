"""Shared helpers turning tool output into `FindingCandidate`s (CIS §8.7).

Tool-native structures never leave the tool modules; only domain objects cross the boundary.
"""

import json
from typing import Any

from analysis.process import MAX_STDOUT_BYTES, ProcessResult
from analysis.python.rules import RuleEntry
from shared.domain.enums import Confidence, LocationStatus, Provenance, Severity
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.models import FindingCandidate, Location


def location(start: int | None, end: int | None, line_count: int) -> Location | None:
    """Clamp a partly out-of-range location; drop one entirely out of range (§8.7)."""
    if start is None:
        return None
    if end is None or end < start:
        end = start
    if end < 1 or start > line_count:
        return None
    return Location(start_line=max(1, start), end_line=min(line_count, end))


def candidate(
    entry: RuleEntry,
    severity: Severity,
    tool_confidence: Confidence,
    summary: str,
    where: Location | None,
) -> FindingCandidate:
    return FindingCandidate(
        provenance=Provenance.STATIC,
        origin=entry.tool,
        rule_key=entry.rule_key,
        severity=severity,
        category=entry.category,
        title=entry.title,
        summary=summary or entry.title,
        impact=entry.impact,
        recommendation=entry.recommendation,
        location=where,
        location_status=LocationStatus.SOURCE_MATCHED if where else LocationStatus.NOT_PROVIDED,
        tool_confidence=tool_confidence,
    )


def completed_stdout(result: ProcessResult, tool: str) -> Any:
    """The parsed JSON stdout of a completed run. Timeouts, oversize and bad JSON are failures."""
    if result.timed_out:
        raise StaticAnalysisFailed(f"{tool} timed out")
    if len(result.stdout) > MAX_STDOUT_BYTES:
        raise StaticAnalysisFailed(f"{tool} output exceeded the size limit")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        raise StaticAnalysisFailed(f"{tool} output could not be parsed") from None


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
