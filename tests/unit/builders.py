"""Builders and time doubles for the review-pipeline tests (CIS §20.2)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.domain.enums import (
    Category,
    Confidence,
    LocationStatus,
    Provenance,
    Severity,
    SeveritySource,
)
from shared.domain.models import Finding, Issue, IssueSource, Location

C, S, P = Category.CORRECTNESS, Category.SECURITY, Category.PERFORMANCE
R, M, B = Category.READABILITY, Category.MAINTAINABILITY, Category.BEST_PRACTICE
CRITICAL, HIGH, MEDIUM, LOW = Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW
CONF_HIGH, CONF_MEDIUM, CONF_LOW = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW


def loc(start: int, end: int | None = None) -> Location:
    return Location(start_line=start, end_line=end or start)


def static_finding(
    n: int,
    rule: str = "pylint:W0611",
    category: Category = M,
    severity: Severity = LOW,
    line: int | None = 1,
    confidence: Confidence = CONF_HIGH,
    **texts: str,
) -> Finding:
    where = loc(line) if line else None
    return Finding(
        finding_id=f"S{n}",
        provenance=Provenance.STATIC,
        origin=rule.split(":")[0],
        rule_key=rule,
        severity=severity,
        category=category,
        title=texts.get("title", "Unused import"),
        summary=texts.get("summary", "Unused import os"),
        impact=texts.get("impact", "Clutter."),
        recommendation=texts.get("recommendation", "Remove it."),
        location=where,
        location_status=LocationStatus.SOURCE_MATCHED if where else LocationStatus.NOT_PROVIDED,
        tool_confidence=confidence,
    )


def ai_finding(
    n: int,
    category: Category = M,
    severity: Severity = LOW,
    line: int | None = 1,
    status: LocationStatus | None = None,
    related: tuple[str, ...] = (),
    **texts: str,
) -> Finding:
    if status is None:
        status = LocationStatus.SOURCE_MATCHED if line else LocationStatus.NOT_PROVIDED
    where = loc(line) if line and status is LocationStatus.SOURCE_MATCHED else None
    return Finding(
        finding_id=f"A{n}",
        provenance=Provenance.AI,
        origin="ai",
        rule_key=None,
        severity=severity,
        category=category,
        title=texts.get("title", "Unused import of os module"),
        summary=texts.get("summary", "The os module is imported but unused."),
        impact=texts.get("impact", "Clutter."),
        recommendation=texts.get("recommendation", "Remove it."),
        location=where,
        location_status=status,
        tool_confidence=None,
        related_static_ids=related,
        ai_output_index=n - 1,
    )


_counter = iter(range(1, 10**6))


def issue(
    category: Category,
    severity: Severity,
    confidence: Confidence,
    *,
    provenance: Provenance = Provenance.STATIC,
    line: int | None = None,
    **fields: Any,
) -> Issue:
    n = next(_counter)
    values: dict[str, Any] = {
        "issue_id": "ISS-001",
        "severity": severity,
        "severity_source": SeveritySource.AI
        if provenance is Provenance.AI
        else SeveritySource.STATIC,
        "category": category,
        "title": f"Issue {n}",
        "summary": "s",
        "impact": "i",
        "recommendation": "r",
        "location": loc(line) if line else None,
        "additional_locations": (),
        "occurrence_count": 1,
        "provenance": provenance,
        "confidence": confidence,
        "sources": (IssueSource(finding_id=f"S{n}", origin="pylint", rule_key=f"pylint:X{n}"),),
    }
    return Issue(**{**values, **fields})


@dataclass
class FakeClock:
    """Monotonic seconds and UTC wall time that only move when told to."""

    seconds: float = 1000.0
    start: datetime = field(default_factory=lambda: datetime(2026, 10, 8, tzinfo=UTC))

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.seconds - 1000.0)

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


@dataclass
class ScriptedDeadline:
    """Returns the scripted remaining() values in order, then repeats the last one."""

    values: list[float]

    def remaining(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]
