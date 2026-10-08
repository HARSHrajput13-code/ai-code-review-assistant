"""Static normalization and deterministic numbering (CIS §12.1, §12.2, D-40)."""

import itertools
from typing import Any

import pytest

from backend.review.normalization import ELLIPSIS, normalize, truncate
from backend.review.numbering import number_static
from shared.domain.enums import Category, Confidence, LocationStatus, Provenance, Severity
from shared.domain.models import TEXT_LIMITS, FindingCandidate, Location
from shared.domain.text import strip_control


def static(
    rule: str = "pylint:W0611", line: int | None = 1, end: int | None = None, **fields: Any
) -> FindingCandidate:
    where = Location(start_line=line, end_line=end or line) if line else None
    values: dict[str, Any] = {
        "provenance": Provenance.STATIC,
        "origin": rule.split(":")[0],
        "rule_key": rule,
        "severity": Severity.LOW,
        "category": Category.MAINTAINABILITY,
        "title": "Unused import",
        "summary": "Unused import os",
        "impact": "i",
        "recommendation": "r",
        "location": where,
        "location_status": LocationStatus.SOURCE_MATCHED if where else LocationStatus.NOT_PROVIDED,
        "tool_confidence": Confidence.HIGH,
    }
    return FindingCandidate(**{**values, **fields})


CANDIDATES = (
    static("pylint:W0611", 3),
    static("bandit:B602", 2),
    static("pylint:W0102", 2),
    static("python-parser:syntax-error", None),
    static("pylint:C0301", 2, 4),
    static("pylint:W0612", 2, summary="Unused variable 'b'"),
    static("pylint:W0612", 2, summary="Unused variable 'a'"),
)


def keys(findings: tuple[Any, ...]) -> list[tuple[str, str | None]]:
    return [(f.finding_id, f.rule_key) for f in findings]


def test_sort_key_order_and_contiguous_ids() -> None:
    assert keys(number_static(CANDIDATES, line_count=10)) == [
        ("S1", "bandit:B602"),  # line 2..2; rule keys compared by code point
        ("S2", "pylint:W0102"),
        ("S3", "pylint:W0612"),  # same rule and line: the first in sort order is kept
        ("S4", "pylint:C0301"),  # line 2..4 sorts after 2..2
        ("S5", "pylint:W0611"),
        ("S6", "python-parser:syntax-error"),  # no location: last
    ]


def test_deduplication_keeps_the_first_in_sort_order() -> None:
    findings = number_static(CANDIDATES, line_count=10)
    assert [f.summary for f in findings if f.rule_key == "pylint:W0612"] == ["Unused variable 'a'"]


def test_unlocated_duplicates_are_deduplicated_too() -> None:
    twice = (static("bandit:B105", None), static("bandit:B105", None))
    assert len(number_static(twice, line_count=10)) == 1


def test_numbering_is_independent_of_input_order() -> None:
    expected = number_static(CANDIDATES, line_count=10)
    for order in itertools.permutations(CANDIDATES):
        assert number_static(order, line_count=10) == expected


def test_empty_input() -> None:
    assert number_static((), line_count=1) == ()


def test_control_characters_are_stripped_except_newline_and_tab() -> None:
    assert strip_control("a\x00b\x1b[31mc\td\ne\x7f") == "ab[31mc\td\ne"
    (finding,) = number_static((static(summary="bad\x07 text\x00"),), line_count=5)
    assert finding.summary == "bad text"


def test_summary_left_empty_by_stripping_falls_back_to_the_title() -> None:
    (finding,) = number_static((static(summary="\x01\x02"),), line_count=5)
    assert finding.summary == finding.title


@pytest.mark.parametrize("limit", [10, 150, 600])
def test_truncation_fits_the_limit_at_a_word_boundary(limit: int) -> None:
    text = " ".join(["word"] * 400)
    cut = truncate(text, limit)
    assert len(cut) <= limit
    assert cut.endswith(ELLIPSIS)
    assert cut[:-1].split(" ")[-1] == "word"


def test_truncation_of_a_single_long_word_and_short_text() -> None:
    assert truncate("x" * 20, 10) == "x" * 9 + ELLIPSIS
    assert truncate("short", 10) == "short"


def test_static_texts_are_truncated_to_the_domain_limits() -> None:
    long: dict[str, Any] = {field: "long " * 400 for field in TEXT_LIMITS}
    (finding,) = number_static((static(**long),), line_count=5)
    for field, limit in TEXT_LIMITS.items():
        assert len(getattr(finding, field)) <= limit


def test_location_beyond_the_source_is_removed() -> None:
    found = normalize(static(line=4, end=9), line_count=5)
    assert (found.location, found.location_status) == (None, LocationStatus.NOT_PROVIDED)
    assert normalize(static(line=4, end=5), line_count=5).location == Location(
        start_line=4, end_line=5
    )
