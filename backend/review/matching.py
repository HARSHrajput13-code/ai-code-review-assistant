"""Similarity helpers, AI–AI deduplication and corroboration (CIS §12.2, §12.3, §12.5, D-43).

`related_static_ids` is an advisory hint: it never establishes a merge on its own.
"""

import re
from dataclasses import dataclass

from shared.domain.enums import Category, LocationStatus
from shared.domain.models import Finding, Location

STOP_WORDS = frozenset(
    "a an the of in on to for with is are be by and or not this that it its may can could "
    "possible potential should using use used".split()
)
FAMILY = {
    Category.CORRECTNESS: "F1",
    Category.SECURITY: "F2",
    Category.PERFORMANCE: "F3",
    Category.READABILITY: "F4",
    Category.MAINTAINABILITY: "F4",
    Category.BEST_PRACTICE: "F4",
}


def tokens(text: str) -> frozenset[str]:
    found = set()
    for token in re.split(r"[^a-z0-9]+", text.lower()):
        if len(token) < 2 or token in STOP_WORDS:
            continue
        found.add(token[:-1] if len(token) > 3 and token.endswith("s") else token)
    return frozenset(found)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    shared = len(a & b)
    return shared >= 2 and shared * 2 >= min(len(a), len(b))


def textual(ai: Finding, static: Finding) -> bool:
    return similar(tokens(ai.title), tokens(static.title) | tokens(static.summary))


def close(l1: Location | None, l2: Location | None, tolerance: int) -> bool:
    if l1 is None or l2 is None:
        return False
    return l1.start_line <= l2.end_line + tolerance and l2.start_line <= l1.end_line + tolerance


def family(category: Category) -> str:
    return FAMILY[category]


def number(finding: Finding) -> int:
    return int(finding.finding_id[1:])


def _with_related(survivor: Finding, other: Finding) -> Finding:
    merged = tuple(dict.fromkeys(survivor.related_static_ids + other.related_static_ids))
    return survivor.model_copy(update={"related_static_ids": merged})


def _duplicates(a: Finding, b: Finding) -> bool:
    return (
        a.location_status is LocationStatus.SOURCE_MATCHED
        and b.location_status is LocationStatus.SOURCE_MATCHED
        and close(a.location, b.location, 0)
        and family(a.category) == family(b.category)
        and similar(tokens(a.title), tokens(b.title))
    )


def dedupe_ai(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """§12.3: keep the higher severity (on a tie, the earlier); the survivor unions the hints."""
    survivors: list[Finding] = []
    for candidate in sorted(findings, key=number):
        for earlier in list(survivors):
            if not _duplicates(earlier, candidate):
                continue
            if earlier.severity.rank >= candidate.severity.rank:  # a tie removes the later one
                survivors[survivors.index(earlier)] = _with_related(earlier, candidate)
                break
            survivors.remove(earlier)
            candidate = _with_related(candidate, earlier)
        else:
            survivors.append(candidate)
    return tuple(sorted(survivors, key=number))


@dataclass(frozen=True)
class Corroboration:
    groups: dict[str, tuple[Finding, ...]]  # AI finding ID → its corroborated static group G
    refs_rejected: int  # references that failed the rules (a log metric)


def _strength(ai: Finding, static: Finding) -> int:
    if family(ai.category) != family(static.category):
        return 0
    ref = static.finding_id in ai.related_static_ids
    located = ai.location is not None and static.location is not None
    if ref and located and close(ai.location, static.location, 2):
        return 3  # R1
    if ref and not located and textual(ai, static):
        return 2  # R2
    if not ref and located and close(ai.location, static.location, 1) and textual(ai, static):
        return 1  # U1
    return 0


def corroborate(ai: tuple[Finding, ...], static: tuple[Finding, ...]) -> Corroboration:
    """§12.5: deterministic greedy assignment; each static finding merges at most once."""
    pairs, rejected = [], 0
    for a in ai:
        for s in static:
            strength = _strength(a, s)
            if strength:
                distance = (
                    abs(a.location.start_line - s.location.start_line)
                    if (a.location and s.location)
                    else 0
                )
                pairs.append((-strength, distance, number(a), number(s), a, s))
            elif s.finding_id in a.related_static_ids:
                rejected += 1
    assigned: set[str] = set()
    groups: dict[str, list[Finding]] = {}
    for *_, a, s in sorted(pairs, key=lambda p: p[:4]):
        if s.finding_id not in assigned:
            assigned.add(s.finding_id)
            groups.setdefault(a.finding_id, []).append(s)
    return Corroboration(
        groups={a_id: tuple(sorted(g, key=number)) for a_id, g in groups.items()},
        refs_rejected=rejected,
    )
