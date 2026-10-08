"""ScoringPolicyV1, policy version 1.0 (CIS §13). All arithmetic is Decimal; rounding happens once.

The LLM never supplies a number; it influences the score only through validated issues and claims.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType

from shared.domain.enums import Category, Confidence, ScoreCap, Severity
from shared.domain.models import CategoryScore, Issue, Score, band_for

D = Decimal
CATEGORY_WEIGHTS = MappingProxyType({
    Category.CORRECTNESS: D("0.25"),
    Category.SECURITY: D("0.25"),
    Category.MAINTAINABILITY: D("0.15"),
    Category.READABILITY: D("0.15"),
    Category.PERFORMANCE: D("0.10"),
    Category.BEST_PRACTICE: D("0.10"),
})  # fmt: skip
SEVERITY_DEDUCTION = MappingProxyType({
    Severity.CRITICAL: D(40), Severity.HIGH: D(20), Severity.MEDIUM: D(8), Severity.LOW: D(3),
})  # fmt: skip
CONFIDENCE_MULTIPLIER = MappingProxyType({
    Confidence.HIGH: D("1.00"), Confidence.MEDIUM: D("0.75"), Confidence.LOW: D("0.50"),
})  # fmt: skip
SEVERITY_SUBTOTAL_CAP = MappingProxyType({
    Severity.LOW: D(15), Severity.MEDIUM: D(40), Severity.HIGH: D(70), Severity.CRITICAL: D(100),
})  # fmt: skip
OVERALL_CAPS = MappingProxyType({
    ScoreCap.UNPARSEABLE_SOURCE: D(20),
    ScoreCap.CRITICAL_ISSUE: D(40),
    ScoreCap.HIGH_CORRECTNESS_OR_SECURITY: D(70),
})  # fmt: skip


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(D(1), rounding=ROUND_HALF_UP))


def _at_least_medium(confidence: Confidence) -> bool:
    return confidence.rank >= Confidence.MEDIUM.rank


@dataclass(frozen=True)
class ScoringPolicyV1:
    version: str = "1.0"
    weights: MappingProxyType[Category, Decimal] = field(default=CATEGORY_WEIGHTS)

    def category_score(self, issues: Iterable[Issue]) -> Decimal:
        """§13.3: deductions add up per severity, bounded by the subtotal caps."""
        subtotal = dict.fromkeys(Severity, D(0))
        for issue in issues:
            subtotal[issue.severity] += (
                SEVERITY_DEDUCTION[issue.severity] * CONFIDENCE_MULTIPLIER[issue.confidence]
            )
        deduction = sum((min(SEVERITY_SUBTOTAL_CAP[s], subtotal[s]) for s in Severity), start=D(0))
        return D(100) - min(D(100), deduction)

    def applicable_caps(self, issues: Iterable[Issue], syntax_valid: bool) -> list[ScoreCap]:
        """§13.5, in canonical order. LOW-confidence issues never trigger a cap."""
        issues = list(issues)
        conditions = {
            ScoreCap.UNPARSEABLE_SOURCE: not syntax_valid,
            ScoreCap.CRITICAL_ISSUE: any(
                i.severity is Severity.CRITICAL and _at_least_medium(i.confidence) for i in issues
            ),
            ScoreCap.HIGH_CORRECTNESS_OR_SECURITY: any(
                i.severity is Severity.HIGH
                and i.category in (Category.CORRECTNESS, Category.SECURITY)
                and _at_least_medium(i.confidence)
                for i in issues
            ),
        }
        return [cap for cap in ScoreCap if conditions[cap]]

    def score(
        self,
        issues: Iterable[Issue],
        assessed: frozenset[Category],
        *,
        syntax_valid: bool,
        coverage_complete: bool,
    ) -> Score:
        if not assessed:
            raise ValueError("no assessed category: the review is FAILED and has no score")
        issues = list(issues)
        by_category = {c: [i for i in issues if i.category is c] for c in Category}
        exact = {c: self.category_score(by_category[c]) for c in assessed}
        total_weight = sum((self.weights[c] for c in assessed), start=D(0))
        raw = sum((self.weights[c] * exact[c] for c in assessed), start=D(0)) / total_weight
        caps = self.applicable_caps(issues, syntax_valid)
        limit = min([D(100)] + [OVERALL_CAPS[cap] for cap in caps])
        overall = max(0, min(100, round_half_up(min(raw, limit))))
        return Score(
            overall=overall,
            band=band_for(overall),
            provisional=not coverage_complete,
            assessed_weight=int(total_weight * 100),
            categories=tuple(
                CategoryScore(
                    category=c,
                    assessed=c in assessed,
                    score=round_half_up(exact[c]) if c in assessed else None,
                    issue_count=len(by_category[c]) if c in assessed else 0,
                )
                for c in Category
            ),
            caps_applied=tuple(cap for cap in caps if OVERALL_CAPS[cap] < raw),
            policy_version=self.version,
        )
