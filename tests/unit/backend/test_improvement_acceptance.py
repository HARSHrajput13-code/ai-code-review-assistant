"""Improved-code acceptance steps 1-4 (CIS §14.3, D-76, §20.3): pure, in order, first failure ends.

The candidate is untrusted model output; it is only compared, never executed.
"""

import pytest

from backend.review.improvement import (
    EMPTY_OR_INVALID_TEXT,
    TOO_LARGE,
    UNCHANGED,
    accept_candidate,
    strip_fence,
)
from shared.domain.errors import ImprovedCodeInvalid
from shared.domain.models import SourceText

ORIGINAL = SourceText.of("def f(items=[]):\n    return items\n")
IMPROVED = "def f(items=None):\n    return items or []\n"


def rejected(candidate: str, max_source_bytes: int = 12_000) -> str:
    with pytest.raises(ImprovedCodeInvalid) as error:
        accept_candidate(ORIGINAL, candidate, max_source_bytes)
    assert error.value.code == "IMPROVED_CODE_INVALID"
    assert error.value.safe_message == "Improved code could not be validated."
    return error.value.reason


# --- step 1, fence stripping ---------------------------------------------------------------


@pytest.mark.parametrize(
    "fenced",
    [
        f"```python\n{IMPROVED}```",
        f"```\n{IMPROVED}```",
        f"```py3\n{IMPROVED}```",
        f"  \n```python\n{IMPROVED}```\n\n",  # the fence lines are found in the trimmed text
        f"```python\r\n{IMPROVED.replace(chr(10), chr(13) + chr(10))}```",
    ],
    ids=["language", "bare", "alphanumeric", "surrounding-whitespace", "crlf"],
)
def test_a_fenced_candidate_loses_only_its_two_fence_lines(fenced: str) -> None:
    assert accept_candidate(ORIGINAL, fenced, 12_000) == IMPROVED


@pytest.mark.parametrize(
    "text",
    [
        f"```py thon\n{IMPROVED}```",  # not [A-Za-z0-9]*
        f"```python\n{IMPROVED}",  # no closing fence
        f"{IMPROVED}```",  # no opening fence
        f"```python\n{IMPROVED}````",  # the closing line is not exactly ```
        f"```python\n{IMPROVED}``` # end",
        "```python",  # one line
        f"x = 1\n```python\n{IMPROVED}```",  # the fence must open the text
    ],
)
def test_anything_else_is_left_exactly_as_it_is(text: str) -> None:
    assert strip_fence(text) == text


def test_without_a_fence_nothing_is_trimmed_or_rewritten() -> None:
    code = "\n\t\n  def f(items=None):\n\treturn items\n\n\n"
    assert strip_fence(code) == code
    assert accept_candidate(ORIGINAL, code, 12_000) == code  # tabs, indentation, blank lines kept


def test_the_fenced_content_is_kept_byte_for_byte() -> None:
    body = "\tdef f(items=None):\n  \treturn items  \n\n\n"
    assert strip_fence(f"```python\n{body}```") == body


# --- step 2, line endings ------------------------------------------------------------------


def test_line_endings_are_normalized_to_lf() -> None:
    assert accept_candidate(ORIGINAL, "a = 1\r\nb = 2\rc = 3\n", 12_000) == "a = 1\nb = 2\nc = 3\n"


# --- step 3, text and size -----------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [" ", "\n\t\n", "```python\n```", "```\n   \n```", "x = 1\x00\n", "x = '\ud800'\n"],
    ids=["space", "whitespace", "empty-fence", "blank-fence", "nul", "lone-surrogate"],
)
def test_blank_nul_or_unencodable_text_is_rejected(candidate: str) -> None:
    assert rejected(candidate) == EMPTY_OR_INVALID_TEXT


def test_the_acceptance_bound_is_twice_max_source_bytes_in_utf8_bytes() -> None:
    limit = 2 * 1_000
    exact = "#" + "x" * (limit - 2) + "\n"
    assert len(exact.encode()) == limit
    assert accept_candidate(ORIGINAL, exact, 1_000) == exact
    assert rejected(exact + "y", 1_000) == TOO_LARGE
    multibyte = "#" + "é" * 1_000 + "\n"  # 1,000 characters but 2,002 bytes
    assert rejected(multibyte, 1_000) == TOO_LARGE


def test_text_checks_come_before_the_size_check() -> None:
    assert rejected("\x00" * 5_000, 1_000) == EMPTY_OR_INVALID_TEXT


# --- step 4, unchanged ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        ORIGINAL.text,
        ORIGINAL.text.rstrip("\n"),
        ORIGINAL.text + "\n\n  \n",
        "def f(items=[]):   \n    return items\t\n",
        ORIGINAL.text.replace("\n", "\r\n"),
        f"```python\n{ORIGINAL.text}```",
    ],
    ids=[
        "identical",
        "no-final-newline",
        "trailing-blank-lines",
        "trailing-spaces",
        "crlf",
        "fenced",
    ],
)
def test_a_candidate_equal_to_the_original_is_unchanged(candidate: str) -> None:
    assert rejected(candidate) == UNCHANGED


def test_a_leading_whitespace_change_is_a_change() -> None:
    changed = "def f(items=[]):\n        return items\n"
    assert accept_candidate(ORIGINAL, changed, 12_000) == changed
