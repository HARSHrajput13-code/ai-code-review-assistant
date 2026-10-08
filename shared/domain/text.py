"""Text helpers shared by static normalization (§12.2) and AI sanitization (§11.3)."""

import unicodedata


def strip_control(text: str) -> str:
    """Remove control characters other than newline and tab."""
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
