"""Deterministic numbering of static and AI findings (CIS §12.1, D-40).

Identical inputs give identical findings, order and IDs, whatever order the tools finished in.
"""

from collections.abc import Iterable

from backend.review.normalization import normalize
from shared.domain.enums import Category
from shared.domain.models import Finding, FindingCandidate

_MISSING = float("inf")


def _sort_key(candidate: FindingCandidate) -> tuple[float, float, str, str, str]:
    where = candidate.location
    return (
        where.start_line if where else _MISSING,
        where.end_line if where else _MISSING,
        candidate.rule_key or "",
        candidate.summary,
        candidate.title,
    )


def number_static(candidates: Iterable[FindingCandidate], line_count: int) -> tuple[Finding, ...]:
    """Normalize → sort → deduplicate (same rule_key and start_line) → S1..Sn."""
    ordered = sorted((normalize(c, line_count) for c in candidates), key=_sort_key)
    kept: list[FindingCandidate] = []
    seen: set[tuple[str | None, int | None]] = set()
    for candidate in ordered:
        identity = (
            candidate.rule_key,
            candidate.location.start_line if candidate.location else None,
        )
        if identity not in seen:
            seen.add(identity)
            kept.append(candidate)
    return tuple(Finding(**c.model_dump(), finding_id=f"S{n}") for n, c in enumerate(kept, start=1))


_CATEGORY_ORDER = {category: index for index, category in enumerate(Category)}


def _ai_sort_key(candidate: FindingCandidate) -> tuple[int, float, int, str, int]:
    where = candidate.location
    return (
        -candidate.severity.rank,
        where.start_line if where else _MISSING,
        _CATEGORY_ORDER[candidate.category],
        candidate.title.casefold(),
        candidate.ai_output_index if candidate.ai_output_index is not None else 0,
    )


def number_ai(candidates: Iterable[FindingCandidate], line_count: int) -> tuple[Finding, ...]:
    """Normalize → sort (severity desc, line, category, title, output index) → A1..Am."""
    ordered = sorted((normalize(c, line_count) for c in candidates), key=_ai_sort_key)
    return tuple(
        Finding(**c.model_dump(), finding_id=f"A{n}") for n, c in enumerate(ordered, start=1)
    )
