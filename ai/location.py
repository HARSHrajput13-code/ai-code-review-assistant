"""Evidence-based location matching (CIS §11.3 steps 2-4, D-49, D-56).

Full-line equality after whitespace normalization; never substring matching. SOURCE_MATCHED means
only that the quoted line exists at or near the claimed place, not that the issue is real.
"""

import re

from shared.domain.enums import LocationStatus
from shared.domain.models import Location, SourceText

_COPIED_PREFIX = re.compile(r"^\s*\d+\s*\|\s?")
MIN_EVIDENCE_LENGTH = 3

type Located = tuple[Location | None, LocationStatus]


def norm(text: str) -> str:
    return " ".join(text.split())


def _evidence_line(evidence: str | None) -> str | None:
    if evidence is None:
        return None
    first = next((line for line in evidence.split("\n") if line.strip()), "")
    return norm(_COPIED_PREFIX.sub("", first, count=1))


def match_location(
    line: int | None, end_line: int | None, evidence: str | None, source: SourceText
) -> Located:
    if line is None:
        return None, LocationStatus.NOT_PROVIDED  # including line null with end_line set
    end = line if end_line is None else end_line
    count = source.line_count
    if end < line or line > count:
        return None, LocationStatus.UNMATCHED  # a location claim that cannot hold
    end = min(end, count)

    expected = _evidence_line(evidence)
    if expected is None or len(expected) < MIN_EVIDENCE_LENGTH:
        return None, LocationStatus.UNMATCHED
    lines = [norm(text) for text in source.lines()]

    window = [
        k for k in range(max(1, line - 2), min(count, end + 2) + 1) if lines[k - 1] == expected
    ]
    if len(window) > 1:
        return None, LocationStatus.UNMATCHED  # ambiguous
    if len(window) == 1:
        k = window[0]
        if k < line:
            line, end = k, end - (line - k)
        elif k > end:
            line, end = line + (k - end), k
        return Location(
            start_line=max(1, line), end_line=min(count, end)
        ), LocationStatus.SOURCE_MATCHED

    anywhere = [k for k, text in enumerate(lines, start=1) if text == expected]
    if len(anywhere) == 1:
        return Location(start_line=anywhere[0], end_line=anywhere[0]), LocationStatus.SOURCE_MATCHED
    return None, LocationStatus.UNMATCHED
