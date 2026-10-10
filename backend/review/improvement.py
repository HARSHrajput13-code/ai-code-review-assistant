"""Improved-code acceptance, the language-independent steps 1-4 (CIS §14.3, D-76).

Steps 5-6 (the bounded parse and public-interface preservation) are the language adapter's
`validate_generated_code`. The candidate is untrusted model output: it is only compared here,
never executed, and the rejection reason is internal (it is logged, never shown).
"""

import re

from shared.domain.errors import ImprovedCodeInvalid
from shared.domain.models import SourceText

EMPTY_OR_INVALID_TEXT = "EMPTY_OR_INVALID_TEXT"
TOO_LARGE = "TOO_LARGE"
UNCHANGED = "UNCHANGED"
OPENING_FENCE = re.compile(r"```[A-Za-z0-9]*")
LINE_BREAK = re.compile(r"\r\n|\r|\n")


def strip_fence(text: str) -> str:
    """Step 1: remove an opening and a closing fence line around the trimmed text, nothing else."""
    trimmed = text.strip()
    first = LINE_BREAK.search(trimmed)
    if first is None:
        return text
    last_start = max(trimmed.rfind("\n"), trimmed.rfind("\r")) + 1
    if OPENING_FENCE.fullmatch(trimmed[: first.start()]) and trimmed[last_start:] == "```":
        return trimmed[first.end() : last_start]
    return text


def _comparable(text: str) -> str:
    """Trailing whitespace on each line and trailing blank lines are ignored by step 4."""
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")


def accept_candidate(original: SourceText, candidate: str, max_source_bytes: int) -> str:
    """Steps 1-4, in order: the candidate to validate further, or ImprovedCodeInvalid."""
    code = LINE_BREAK.sub("\n", strip_fence(candidate))  # step 2
    try:
        size = len(code.encode("utf-8"))
    except UnicodeEncodeError:  # e.g. a lone surrogate
        raise ImprovedCodeInvalid(EMPTY_OR_INVALID_TEXT) from None
    if not code.strip() or "\x00" in code:
        raise ImprovedCodeInvalid(EMPTY_OR_INVALID_TEXT)
    if size > 2 * max_source_bytes:  # the application acceptance bound (D-76)
        raise ImprovedCodeInvalid(TOO_LARGE)
    if _comparable(code) == _comparable(original.text):
        raise ImprovedCodeInvalid(UNCHANGED)
    return code
