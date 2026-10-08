"""ScoringPolicyV1: the §13.7 reference vectors and the §13.8 properties."""

import itertools
from decimal import Decimal
from types import MappingProxyType

import pytest

from backend.review.issues import build_issues
from backend.review.matching import corroborate
from backend.review.scoring.policy import CATEGORY_WEIGHTS, ScoringPolicyV1, round_half_up
from shared.domain.enums import Category, Provenance, ScoreBand, ScoreCap, SeveritySource
from shared.domain.models import Issue, Score
from tests.unit.builders import (
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    CRITICAL,
    HIGH,
    LOW,
    MEDIUM,
    B,
    C,
    M,
    P,
    R,
    S,
    ai_finding,
    issue,
    static_finding,
)

POLICY = ScoringPolicyV1()
FULL = frozenset(Category)
PYLINT = frozenset({C, P, R, M, B})
ORDER = (C, S, M, R, P, B)  # the column order of the §13.7 table


def score(
    issues: list[Issue], assessed: frozenset[Category] = FULL, syntax_valid: bool = True
) -> Score:
    return POLICY.score(
        issues, assessed, syntax_valid=syntax_valid, coverage_complete=assessed == FULL
    )


def columns(result: Score) -> list[int | None]:
    by_category = {c.category: c.score for c in result.categories}
    return [by_category[c] for c in ORDER]


def test_weights_sum_to_exactly_one() -> None:
    assert sum(CATEGORY_WEIGHTS.values()) == Decimal("1.00")


@pytest.mark.parametrize(
    ("name", "issues", "assessed", "syntax_valid", "categories", "caps", "overall", "band"),
    [
        ("V1", [], FULL, True, [100] * 6, (), 100, ScoreBand.EXCELLENT),
        (
            "V2",
            [
                issue(S, HIGH, CONF_HIGH),
                issue(C, MEDIUM, CONF_MEDIUM, provenance=Provenance.AI),
                issue(R, LOW, CONF_HIGH),
                issue(R, LOW, CONF_HIGH),
            ],
            FULL,
            True,
            [94, 80, 100, 94, 100, 100],
            (ScoreCap.HIGH_CORRECTNESS_OR_SECURITY,),
            70,
            ScoreBand.FAIR,
        ),
        ("V3", [issue(M, LOW, CONF_HIGH) for _ in range(8)], FULL, True,
         [100, 100, 85, 100, 100, 100], (), 98, ScoreBand.EXCELLENT),
        ("V4", [issue(C, CRITICAL, CONF_HIGH)], FULL, False, [60, 100, 100, 100, 100, 100],
         (ScoreCap.UNPARSEABLE_SOURCE, ScoreCap.CRITICAL_ISSUE), 20, ScoreBand.VERY_POOR),
        # AI-only S/CRITICAL, UNMATCHED: confidence LOW, shown and scored as HIGH (§12.4).
        ("V5", [issue(S, HIGH, CONF_LOW, provenance=Provenance.AI)], FULL, True,
         [100, 90, 100, 100, 100, 100], (), 98, ScoreBand.EXCELLENT),
        ("V6", [issue(S, CRITICAL, CONF_MEDIUM, provenance=Provenance.AI)], FULL, True,
         [100, 70, 100, 100, 100, 100], (ScoreCap.CRITICAL_ISSUE,), 40, ScoreBand.POOR),
        ("V7", [issue(C, CRITICAL, CONF_HIGH) for _ in range(3)], FULL, True,
         [0, 100, 100, 100, 100, 100], (ScoreCap.CRITICAL_ISSUE,), 40, ScoreBand.POOR),
        ("V8", [issue(C, MEDIUM, CONF_HIGH)], PYLINT, True, [92, None, 100, 100, 100, 100],
         (), 97, ScoreBand.EXCELLENT),
        ("V9", [issue(C, CRITICAL, CONF_HIGH)], frozenset({C}), False,
         [60, None, None, None, None, None],
         (ScoreCap.UNPARSEABLE_SOURCE, ScoreCap.CRITICAL_ISSUE), 20, ScoreBand.VERY_POOR),
        ("V10", [], frozenset({S, B}), True, [None, 100, None, None, None, 100], (), 100,
         ScoreBand.EXCELLENT),
    ],
)  # fmt: skip
def test_reference_vectors(
    name: str,
    issues: list[Issue],
    assessed: frozenset[Category],
    syntax_valid: bool,
    categories: list[int | None],
    caps: tuple[ScoreCap, ...],
    overall: int,
    band: ScoreBand,
) -> None:
    result = score(issues, assessed, syntax_valid)
    assert columns(result) == categories, name
    assert (result.caps_applied, result.overall, result.band) == (caps, overall, band), name
    assert result.provisional == (assessed != FULL)
    assert result.policy_version == "1.0"


@pytest.mark.parametrize(
    ("assessed", "weight"),
    [(PYLINT, 75), (frozenset({C}), 25), (frozenset({S, B}), 35), (FULL, 100)],
)
def test_assessed_weight(assessed: frozenset[Category], weight: int) -> None:
    assert score([], assessed).assessed_weight == weight


def test_v8_raw_is_renormalized_exactly() -> None:
    # (0.25×92 + 0.15×100 + 0.15×100 + 0.10×100 + 0.10×100) / 0.75 = 97.333…
    assert score([issue(C, MEDIUM, CONF_HIGH)], PYLINT).overall == 97


def test_v11_hybrid_escalable_rule_takes_the_ai_claim() -> None:
    static = (static_finding(1, "bandit:B608", S, LOW, line=3, confidence=CONF_MEDIUM),)
    ai = (ai_finding(1, S, CRITICAL, line=3, related=("S1",), title="SQL injection in query"),)
    (built,) = build_issues(static, ai, corroborate(ai, static))
    assert (built.provenance, built.severity, built.confidence, built.severity_source) == (
        Provenance.HYBRID, CRITICAL, CONF_MEDIUM, SeveritySource.AI,
    )  # fmt: skip
    result = score([built])
    assert (columns(result), result.overall, result.band) == (
        [100, 70, 100, 100, 100, 100], 40, ScoreBand.POOR,
    )  # fmt: skip


def test_v12_hybrid_non_escalable_rule_keeps_the_static_claim() -> None:
    static = (static_finding(1, "pylint:W0611", M, LOW, line=1),)
    ai = (ai_finding(1, M, HIGH, line=1, related=("S1",)),)
    (built,) = build_issues(static, ai, corroborate(ai, static))
    assert (built.severity, built.confidence, built.severity_source) == (
        LOW, CONF_HIGH, SeveritySource.STATIC,
    )  # fmt: skip
    result = score([built])
    assert (columns(result), result.overall) == ([100, 100, 97, 100, 100, 100], 100)


@pytest.mark.parametrize(
    ("value", "expected"), [("24.5", 25), ("49.49", 49), ("74.5", 75), ("89.5", 90), ("0.5", 1)]
)
def test_half_up_rounding(value: str, expected: int) -> None:
    assert round_half_up(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("overall", "band"),
    [(24, ScoreBand.VERY_POOR), (25, ScoreBand.POOR), (49, ScoreBand.POOR), (50, ScoreBand.FAIR),
     (74, ScoreBand.FAIR), (75, ScoreBand.GOOD), (89, ScoreBand.GOOD), (90, ScoreBand.EXCELLENT)],
)  # fmt: skip
def test_band_boundaries(overall: int, band: ScoreBand) -> None:
    from shared.domain.models import band_for

    assert band_for(overall) is band


def test_no_assessed_category_has_no_score() -> None:
    with pytest.raises(ValueError):
        POLICY.score([], frozenset(), syntax_valid=True, coverage_complete=False)


# §13.8 properties ------------------------------------------------------------------------------

BASE = [issue(S, MEDIUM, CONF_HIGH), issue(R, LOW, CONF_MEDIUM)]
EVERY_ISSUE = [
    (category, severity, confidence)
    for category in Category
    for severity in (CRITICAL, HIGH, MEDIUM, LOW)
    for confidence in (CONF_HIGH, CONF_MEDIUM, CONF_LOW)
]


@pytest.mark.parametrize(("category", "severity", "confidence"), EVERY_ISSUE)
def test_monotonicity(category: Category, severity: object, confidence: object) -> None:
    before = score(BASE)
    after = score([*BASE, issue(category, severity, confidence)])  # type: ignore[arg-type]
    assert after.overall <= before.overall
    for old, new in zip(before.categories, after.categories, strict=True):
        assert (new.score or 0) <= (old.score or 0)


def test_order_invariance() -> None:
    issues = [
        issue(C, HIGH, CONF_HIGH),
        issue(S, CRITICAL, CONF_LOW),
        issue(M, LOW, CONF_HIGH),
        issue(R, MEDIUM, CONF_MEDIUM),
        issue(P, LOW, CONF_LOW),
    ]
    expected = score(issues)
    for order in itertools.permutations(issues):
        assert score(list(order)) == expected


def test_renormalization_is_exact_for_every_subset() -> None:
    for size in range(1, 7):
        for subset in itertools.combinations(Category, size):
            same = [issue(c, LOW, CONF_HIGH) for c in subset]  # every category scores 97
            assert score(same, frozenset(subset)).overall == 97


def test_unassessed_categories_have_no_score_and_their_weight_is_irrelevant() -> None:
    result = score([issue(C, MEDIUM, CONF_HIGH)], PYLINT)
    security = next(c for c in result.categories if c.category is S)
    assert (security.assessed, security.score, security.issue_count) == (False, None, 0)
    heavier = MappingProxyType({**CATEGORY_WEIGHTS, S: Decimal("0.90")})
    reweighted = ScoringPolicyV1(weights=heavier).score(
        [issue(C, MEDIUM, CONF_HIGH)], PYLINT, syntax_valid=True, coverage_complete=False
    )
    assert reweighted.overall == result.overall


@pytest.mark.parametrize(
    ("issues", "syntax_valid", "applicable"),
    [
        ([], False, [ScoreCap.UNPARSEABLE_SOURCE]),
        ([issue(R, CRITICAL, CONF_MEDIUM)], True, [ScoreCap.CRITICAL_ISSUE]),
        ([issue(R, CRITICAL, CONF_LOW)], True, []),
        ([issue(C, HIGH, CONF_MEDIUM)], True, [ScoreCap.HIGH_CORRECTNESS_OR_SECURITY]),
        ([issue(S, HIGH, CONF_HIGH)], True, [ScoreCap.HIGH_CORRECTNESS_OR_SECURITY]),
        ([issue(S, HIGH, CONF_LOW)], True, []),
        ([issue(M, HIGH, CONF_HIGH)], True, []),
    ],
)
def test_caps_trigger_exactly_at_their_condition(
    issues: list[Issue], syntax_valid: bool, applicable: list[ScoreCap]
) -> None:
    assert POLICY.applicable_caps(issues, syntax_valid) == applicable


def test_caps_applied_lists_only_caps_below_raw() -> None:
    # raw is 100 - (one category at 0) ... a cap at 70 only appears when raw exceeds 70.
    low_raw = [issue(c, CRITICAL, CONF_HIGH) for c in Category for _ in range(3)]
    result = score(low_raw)
    assert result.overall == 0
    assert result.caps_applied == ()


def test_confidence_multipliers_are_exact() -> None:
    for confidence, expected in ((CONF_HIGH, 80), (CONF_MEDIUM, 85), (CONF_LOW, 90)):
        result = score([issue(M, HIGH, confidence)])
        assert columns(result)[2] == expected


def test_grouped_and_hybrid_issues_count_once() -> None:
    grouped = issue(M, LOW, CONF_HIGH, line=1, occurrence_count=7)
    assert columns(score([grouped]))[2] == 97
