"""Coverage: which categories are assessed (CIS §13.2, D-45). Disabled is not failed (D-57)."""

from collections.abc import Callable

from shared.domain.enums import Category, OutcomeStatus, SkipReason
from shared.domain.models import Coverage, StageOutcome, StaticAnalysisResult

STATIC_TOOLS = ("pylint", "bandit")


def _ok(outcome: StageOutcome | None) -> bool:
    return outcome is not None and outcome.status is OutcomeStatus.SUCCEEDED


def coverage(
    ai: StageOutcome,
    static: StaticAnalysisResult,
    covered_categories: Callable[[str], tuple[Category, ...]],
) -> tuple[Coverage, frozenset[Category]]:
    """The Coverage and the set of assessed categories."""
    tools = {t.tool: t.outcome for t in static.tools}
    assessed: set[Category] = set()
    if _ok(ai):
        assessed.update(Category)
    for tool in STATIC_TOOLS:
        if _ok(tools.get(tool)):
            assessed.update(covered_categories(tool))
    if not static.syntax_valid:
        assessed.add(Category.CORRECTNESS)  # a definitive parser result

    def complete(tool: str) -> bool:
        outcome = tools.get(tool)
        return _ok(outcome) or (
            outcome is not None
            and outcome.status is OutcomeStatus.SKIPPED
            and outcome.skip_reason is SkipReason.SYNTAX_ERROR
        )

    missing = (["ai"] if not _ok(ai) else []) + [t for t in STATIC_TOOLS if not complete(t)]
    return (
        Coverage(
            complete=not missing,
            unassessed_categories=tuple(c for c in Category if c not in assessed),
            missing_components=tuple(missing),
        ),
        frozenset(assessed),
    )
