"""AI numbering, deduplication, corroboration, claims and issues (CIS §12.1-§12.7, D-43, D-44)."""

import itertools

from backend.review.issues import ai_claim, build_issues, static_claim
from backend.review.matching import close, corroborate, dedupe_ai, family, similar, tokens
from backend.review.numbering import number_ai
from shared.domain.enums import Category, LocationStatus, Provenance, Severity, SeveritySource
from shared.domain.models import Finding, FindingCandidate, Issue
from tests.unit.builders import (
    CONF_LOW,
    CONF_MEDIUM,
    CRITICAL,
    HIGH,
    LOW,
    MEDIUM,
    B,
    C,
    M,
    R,
    S,
    ai_finding,
    loc,
    static_finding,
)

# Helpers (§12.2) ---------------------------------------------------------------------------


def test_tokens_drop_stop_words_short_tokens_and_one_plural_s() -> None:
    assert tokens("The unused imports of os module may be removed") == {
        "unused", "import", "os", "module", "removed",
    }  # fmt: skip


def test_similarity_needs_two_shared_tokens_and_half_overlap() -> None:
    assert similar(frozenset({"sql", "injection"}), frozenset({"sql", "injection", "query"}))
    assert not similar(frozenset({"sql"}), frozenset({"sql"}))
    assert not similar(frozenset("abcdef"), frozenset("abxyzw"))


def test_close_with_tolerance_and_families() -> None:
    assert close(loc(3), loc(5), 2) and not close(loc(3), loc(6), 2) and not close(None, loc(1), 9)
    assert family(Category.READABILITY) == family(Category.BEST_PRACTICE) != family(C)


# AI numbering (§12.1) -----------------------------------------------------------------------


def candidate(
    index: int, severity: Severity, line: int | None, category: Category, title: str
) -> FindingCandidate:
    finding = ai_finding(index + 1, category, severity, line=line, title=title)
    return FindingCandidate(**finding.model_dump(exclude={"finding_id"}))


def test_number_ai_orders_by_severity_line_category_title_and_output_index() -> None:
    candidates = [
        candidate(0, LOW, 1, R, "b"),
        candidate(1, HIGH, 9, S, "x"),
        candidate(2, HIGH, 2, M, "y"),
        candidate(3, HIGH, 2, C, "z"),
        candidate(4, HIGH, None, C, "a"),
        candidate(5, LOW, 1, R, "B"),
    ]
    numbered = number_ai(candidates, line_count=10)
    assert [(f.finding_id, f.ai_output_index) for f in numbered] == [
        ("A1", 3), ("A2", 2), ("A3", 1), ("A4", 4), ("A5", 0), ("A6", 5),
    ]  # fmt: skip
    for order in itertools.permutations(candidates):
        assert number_ai(order, line_count=10) == numbered


# AI–AI deduplication (§12.3) ------------------------------------------------------------------


def test_duplicate_ai_findings_keep_the_higher_severity_and_union_hints() -> None:
    a1 = ai_finding(1, C, MEDIUM, line=4, related=("S1",), title="Mutable default argument list")
    a2 = ai_finding(2, C, HIGH, line=4, related=("S2",), title="Default argument list is mutable")
    (survivor,) = dedupe_ai((a1, a2))
    assert survivor.finding_id == "A2" and set(survivor.related_static_ids) == {"S1", "S2"}


def test_tie_removes_the_later_finding() -> None:
    a1 = ai_finding(1, C, MEDIUM, line=4, title="Mutable default argument list")
    a2 = ai_finding(2, C, MEDIUM, line=4, title="Default argument list is mutable")
    assert [f.finding_id for f in dedupe_ai((a1, a2))] == ["A1"]


def test_not_duplicates_when_unmatched_apart_other_family_or_dissimilar() -> None:
    base = ai_finding(1, C, MEDIUM, line=4, title="Mutable default argument list")
    cases = [
        ai_finding(2, C, MEDIUM, line=4, status=LocationStatus.UNMATCHED, title=base.title),
        ai_finding(2, C, MEDIUM, line=6, title=base.title),
        ai_finding(2, S, MEDIUM, line=4, title=base.title),
        ai_finding(2, C, MEDIUM, line=4, title="Completely different problem here"),
    ]
    for other in cases:
        assert len(dedupe_ai((base, other))) == 2


# Corroboration (§12.5) ---------------------------------------------------------------------


def groups(ai: tuple[Finding, ...], static: tuple[Finding, ...]) -> dict[str, list[str]]:
    result = corroborate(ai, static)
    return {a: [s.finding_id for s in g] for a, g in result.groups.items()}


def test_r1_reference_and_close_locations_merge() -> None:
    assert groups((ai_finding(1, M, line=3, related=("S1",)),), (static_finding(1, line=1),)) == {
        "A1": ["S1"]
    }


def test_r2_reference_without_locations_and_textual_similarity_merge() -> None:
    ai = (ai_finding(1, M, line=None, related=("S1",), title="Unused import of os"),)
    assert groups(ai, (static_finding(1, line=None),)) == {"A1": ["S1"]}


def test_u1_unreferenced_close_and_textual_merge_with_tolerance_one() -> None:
    assert groups((ai_finding(1, M, line=2),), (static_finding(1, line=1),)) == {"A1": ["S1"]}
    assert groups((ai_finding(1, M, line=3),), (static_finding(1, line=1),)) == {}


def test_reference_alone_never_merges() -> None:
    other_family = (ai_finding(1, S, line=1, related=("S1",)),)
    far = (ai_finding(1, M, line=5, related=("S1",)),)
    no_text = (ai_finding(1, M, line=None, related=("S1",), title="Something unrelated entirely"),)
    for ai in (other_family, far, no_text):
        result = corroborate(ai, (static_finding(1, line=1 if ai is not no_text else None),))
        assert result.groups == {} and result.refs_rejected == 1


def test_greedy_assignment_is_deterministic_and_one_ai_absorbs_several() -> None:
    static = (static_finding(1, line=1), static_finding(2, "pylint:W0612", line=2))
    ai = (ai_finding(1, M, line=1, related=("S1", "S2")), ai_finding(2, M, line=2, related=("S2",)))
    assert groups(ai, static) == {"A1": ["S1"], "A2": ["S2"]}
    single = (ai_finding(1, M, line=1, related=("S1", "S2")),)
    assert groups(single, static) == {"A1": ["S1", "S2"]}


# Claims and issues (§12.4, §12.6, §12.7) --------------------------------------------------------


def test_claims() -> None:
    assert static_claim(static_finding(1, confidence=CONF_MEDIUM)).confidence is CONF_MEDIUM
    assert ai_claim(ai_finding(1, line=1)).confidence is CONF_MEDIUM  # never above MEDIUM
    assert ai_claim(ai_finding(1, line=None)).confidence is CONF_MEDIUM
    assert ai_claim(ai_finding(1, line=1, status=LocationStatus.UNMATCHED)).confidence is CONF_LOW


def build(static: tuple[Finding, ...], ai: tuple[Finding, ...]) -> list[Issue]:
    return list(build_issues(static, ai, corroborate(ai, static)))


def test_ai_cannot_lower_a_static_severity_and_ties_go_to_static() -> None:
    static = (static_finding(1, "pylint:W0102", C, MEDIUM, line=4),)
    for severity in (LOW, MEDIUM):
        (made,) = build(static, (ai_finding(1, C, severity, line=4, related=("S1",)),))
        assert (made.severity, made.severity_source) == (MEDIUM, SeveritySource.STATIC)


def test_hybrid_takes_ai_texts_primary_location_and_lists_sources() -> None:
    static = (
        static_finding(1, "pylint:W0102", C, MEDIUM, line=4),
        static_finding(2, "pylint:E0602", C, HIGH, line=5),
    )
    ai = (
        ai_finding(
            1, C, LOW, line=4, related=("S1", "S2"), title="Mutable default shared across calls"
        ),
    )
    (made,) = build(static, ai)
    assert made.provenance is Provenance.HYBRID
    assert made.title == "Mutable default shared across calls"
    assert made.location == loc(5)  # primary = highest-weight static claim
    assert made.additional_locations == (loc(4),)
    assert [s.finding_id for s in made.sources] == ["S1", "S2", "A1"]


def test_ai_only_critical_low_is_adjusted_to_high() -> None:
    (made,) = build((), (ai_finding(1, S, CRITICAL, line=3, status=LocationStatus.UNMATCHED),))
    assert (made.severity, made.confidence) == (HIGH, CONF_LOW)
    (kept,) = build((), (ai_finding(1, S, CRITICAL, line=None),))
    assert (kept.severity, kept.confidence) == (CRITICAL, CONF_MEDIUM)


def test_repeated_low_static_rule_is_grouped() -> None:
    static = tuple(
        static_finding(n, "pylint:W0611", M, LOW, line=n, summary=f"Unused import m{n}")
        for n in (1, 2, 3)
    )
    (made,) = build(static, ())
    assert made.location == loc(1)
    assert made.additional_locations == (loc(2), loc(3))
    assert made.occurrence_count == 3
    assert made.summary == "Unused import m1 (and 2 more occurrences)"
    assert [s.finding_id for s in made.sources] == ["S1", "S2", "S3"]


def test_issues_are_sorted_and_numbered() -> None:
    static = (
        static_finding(1, "pylint:W0611", M, LOW, line=9),
        static_finding(2, "bandit:B602", S, HIGH, line=5),
        static_finding(3, "pylint:W0102", C, MEDIUM, line=2),
        static_finding(4, "bandit:B105", S, HIGH, line=1),
    )
    ai = (ai_finding(1, B, MEDIUM, line=7, title="Prefer context manager"),)
    made = build(static, ai)
    assert [(i.issue_id, i.sources[0].finding_id) for i in made] == [
        ("ISS-001", "S4"),
        ("ISS-002", "S2"),
        ("ISS-003", "S3"),
        ("ISS-004", "A1"),
        ("ISS-005", "S1"),
    ]


def test_location_is_none_when_neither_side_has_one() -> None:
    (made,) = build(
        (static_finding(1, line=None),),
        (ai_finding(1, M, line=None, related=("S1",), title="Unused import of os"),),
    )
    assert made.location is None and made.occurrence_count == 1
