"""Evidence-based location matching (CIS §11.3 steps 2-4, D-49, D-56)."""

import pytest

from ai.location import match_location
from shared.domain.enums import LocationStatus
from shared.domain.models import Location, SourceText

SOURCE = SourceText.of(
    "def f(items=[]):\n"  # 1
    "    items.append(1)\n"  # 2
    "    return items\n"  # 3
    "\n"  # 4
    "def g():\n"  # 5
    "    return  items\n"  # 6
    "x = 1\n"  # 7
)
MATCHED, UNMATCHED, NOT_PROVIDED = (
    LocationStatus.SOURCE_MATCHED,
    LocationStatus.UNMATCHED,
    LocationStatus.NOT_PROVIDED,
)


def at(start: int, end: int | None = None) -> Location:
    return Location(start_line=start, end_line=end or start)


@pytest.mark.parametrize(
    ("line", "end", "evidence", "expected"),
    [
        (1, 1, "def   f(items=[]):", (at(1), MATCHED)),  # whitespace differences match
        (2, None, "    items.append(1)", (at(2), MATCHED)),  # end_line defaults to line
        (2, 2, "items.append", (None, UNMATCHED)),  # a substring never matches
        (1, 1, "items.append(1)", (at(2), MATCHED)),  # in the window, outside the range: shifted
        (4, 5, "    items.append(1)", (at(2, 3), MATCHED)),  # shifted by the minimal offset
        (1, 1, "x = 1", (at(7), MATCHED)),  # no window match, unique elsewhere: relocated
        (4, 4, "return items", (None, UNMATCHED)),  # lines 3 and 6 both in the window: ambiguous
        (3, 3, "return items", (at(3), MATCHED)),  # line 6 is outside the window [1, 5]
        (1, 1, "y = 2", (None, UNMATCHED)),  # nowhere
        (1, 1, "ab", (None, UNMATCHED)),  # evidence shorter than 3 characters
        (1, 1, None, (None, UNMATCHED)),
        (2, 2, "  12 | items.append(1)", (at(2), MATCHED)),  # a copied line-number prefix
        (2, 2, "\n   \n    items.append(1)\nmore", (at(2), MATCHED)),  # first non-blank line
        (None, 3, "x = 1", (None, NOT_PROVIDED)),
        (None, None, None, (None, NOT_PROVIDED)),
        (3, 2, "return items", (None, UNMATCHED)),  # end before start
        (99, 99, "x = 1", (None, UNMATCHED)),  # beyond the source
        (7, 99, "x = 1", (at(7, 8), MATCHED)),  # end clamped to line_count
    ],
)
def test_matching_rules(
    line: int | None,
    end: int | None,
    evidence: str | None,
    expected: tuple[Location | None, LocationStatus],
) -> None:
    assert match_location(line, end, evidence, SOURCE) == expected
