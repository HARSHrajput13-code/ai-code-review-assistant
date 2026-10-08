"""Claims, issue building, grouping, sorting and numbering (CIS §12.4, §12.6, §12.7, D-44)."""

from dataclasses import dataclass
from decimal import Decimal

from backend.review.matching import Corroboration, number
from backend.review.normalization import truncate
from backend.review.scoring.policy import CONFIDENCE_MULTIPLIER, SEVERITY_DEDUCTION
from shared.domain.enums import (
    Category,
    Confidence,
    LocationStatus,
    Provenance,
    Severity,
    SeveritySource,
)
from shared.domain.models import (
    MAX_ADDITIONAL_LOCATIONS,
    TEXT_LIMITS,
    Finding,
    Issue,
    IssueSource,
    Location,
)

ESCALABLE = frozenset({Category.CORRECTNESS, Category.SECURITY})
_CATEGORY_ORDER = {category: index for index, category in enumerate(Category)}


@dataclass(frozen=True)
class Claim:
    severity: Severity
    confidence: Confidence
    source: SeveritySource

    @property
    def weight(self) -> Decimal:
        return SEVERITY_DEDUCTION[self.severity] * CONFIDENCE_MULTIPLIER[self.confidence]


def static_claim(finding: Finding) -> Claim:
    if finding.tool_confidence is None:
        raise ValueError("a static finding always has a tool confidence")
    return Claim(finding.severity, finding.tool_confidence, SeveritySource.STATIC)


def ai_claim(finding: Finding) -> Claim:
    """MEDIUM, or LOW when the location claim failed to match (§12.4); never higher."""
    unmatched = finding.location_status is LocationStatus.UNMATCHED
    return Claim(
        finding.severity, Confidence.LOW if unmatched else Confidence.MEDIUM, SeveritySource.AI
    )


def _source(finding: Finding) -> IssueSource:
    return IssueSource(
        finding_id=finding.finding_id, origin=finding.origin, rule_key=finding.rule_key
    )


def _source_key(source: IssueSource) -> tuple[int, int]:
    return (0 if source.finding_id.startswith("S") else 1, int(source.finding_id[1:]))


def _locations_key(location: Location) -> tuple[int, int]:
    return (location.start_line, location.end_line)


def _issue(
    finding: Finding,
    claim: Claim,
    provenance: Provenance,
    location: Location | None,
    *,
    category: Category | None = None,
    additional: tuple[Location, ...] = (),
    sources: tuple[Finding, ...] = (),
    fallback: Finding | None = None,
) -> Issue:
    """Texts come from `finding`; a blank one falls back to `fallback` (§12.6)."""

    def text(field: str) -> str:
        value: str = getattr(finding, field)
        return getattr(fallback, field) if fallback is not None and not value.strip() else value

    return Issue(
        issue_id="ISS-000",  # numbered after sorting
        severity=claim.severity,
        severity_source=claim.source,
        category=category or finding.category,
        title=text("title"),
        summary=text("summary"),
        impact=text("impact"),
        recommendation=text("recommendation"),
        location=location,
        additional_locations=additional,
        occurrence_count=1 + len(additional) if location else 1,
        provenance=provenance,
        confidence=claim.confidence,
        sources=tuple(sorted((_source(f) for f in sources or (finding,)), key=_source_key)),
    )


def hybrid_issue(ai: Finding, group: tuple[Finding, ...]) -> Issue:
    """§12.6: category from the primary static finding; the AI may raise only escalable rules."""
    primary = max(group, key=lambda s: (static_claim(s).weight, -number(s)))
    static, model = static_claim(primary), ai_claim(ai)
    claim = model if primary.category in ESCALABLE and model.weight > static.weight else static
    location = primary.location or ai.location
    others = {f.location for f in (*group, ai) if f.location and f.location != location}
    additional = tuple(sorted(others, key=_locations_key))[:MAX_ADDITIONAL_LOCATIONS]
    return _issue(
        ai,
        claim,
        Provenance.HYBRID,
        location,
        category=primary.category,
        additional=additional,
        sources=(ai, *group),
        fallback=primary,
    )


def static_issue(finding: Finding) -> Issue:
    return _issue(finding, static_claim(finding), Provenance.STATIC, finding.location)


def ai_issue(finding: Finding) -> Issue:
    claim = ai_claim(finding)
    if (claim.severity, claim.confidence) == (Severity.CRITICAL, Confidence.LOW):
        claim = Claim(Severity.HIGH, Confidence.LOW, SeveritySource.AI)  # the only adjustment
    return _issue(finding, claim, Provenance.AI, finding.location)


def group_static(issues: list[Issue], static: dict[str, Finding]) -> list[Issue]:
    """§12.7 step 1: repeated LOW static issues of one rule become one issue."""
    by_rule: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.provenance is Provenance.STATIC and issue.severity is Severity.LOW:
            by_rule.setdefault(issue.sources[0].rule_key or "", []).append(issue)
    result = [
        i for i in issues if not (i.provenance is Provenance.STATIC and i.severity is Severity.LOW)
    ]
    for occurrences in by_rule.values():
        if len(occurrences) < 2:
            result.extend(occurrences)
            continue
        occurrences.sort(key=lambda i: _locations_key(i.location) if i.location else (10**9, 10**9))
        first = occurrences[0]
        located = [i.location for i in occurrences[1:] if i.location]
        more = f" (and {len(occurrences) - 1} more occurrences)"
        summary = truncate(
            static[first.sources[0].finding_id].summary, TEXT_LIMITS["summary"] - len(more)
        )
        result.append(
            first.model_copy(
                update={
                    "location": first.location,
                    "additional_locations": tuple(sorted(located, key=_locations_key))[
                        :MAX_ADDITIONAL_LOCATIONS
                    ],
                    "occurrence_count": len(occurrences),
                    "confidence": min((i.confidence for i in occurrences), key=lambda c: c.rank),
                    "summary": summary + more,
                    "sources": tuple(
                        sorted((s for i in occurrences for s in i.sources), key=_source_key)
                    ),
                }
            )
        )
    return result


def _sort_key(issue: Issue) -> tuple[int, int, float, int, str, tuple[int, int]]:
    return (
        -issue.severity.rank,
        -issue.confidence.rank,
        issue.location.start_line if issue.location else float("inf"),
        _CATEGORY_ORDER[issue.category],
        issue.title.casefold(),
        min(_source_key(s) for s in issue.sources),
    )


def build_issues(
    static: tuple[Finding, ...], ai: tuple[Finding, ...], corroboration: Corroboration
) -> tuple[Issue, ...]:
    """§12.6 and §12.7: build, group, sort and number every issue (before truncation)."""
    merged = {s.finding_id for group in corroboration.groups.values() for s in group}
    issues = [
        hybrid_issue(a, corroboration.groups[a.finding_id])
        if a.finding_id in corroboration.groups
        else ai_issue(a)
        for a in ai
    ]
    issues += [static_issue(s) for s in static if s.finding_id not in merged]
    issues = group_static(issues, {s.finding_id: s for s in static})
    ordered = sorted(issues, key=_sort_key)
    return tuple(
        Issue.model_validate({**dict(issue), "issue_id": f"ISS-{n:03d}"})
        for n, issue in enumerate(ordered, start=1)
    )
