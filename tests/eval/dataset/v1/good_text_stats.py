"""Word statistics for short texts."""

import re
from collections import Counter

WORD = re.compile(r"[A-Za-z']+")


def word_counts(text: str) -> Counter[str]:
    """Count words case-insensitively."""
    return Counter(word.lower() for word in WORD.findall(text))


def most_common(text: str, limit: int = 3) -> list[tuple[str, int]]:
    """The most frequent words, most frequent first."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    return word_counts(text).most_common(limit)
