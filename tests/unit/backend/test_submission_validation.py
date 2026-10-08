"""Submission validation and normalization (CIS §6.3 steps 8-11, §7.2) and the Deadline."""

from datetime import timedelta

import pytest

from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from analysis.registry import LanguageRegistry
from backend.application.deadline import MonotonicDeadline, SystemClock
from backend.application.validation import SubmissionValidator, normalize_source
from shared.domain.enums import ErrorCode, Language
from shared.domain.errors import RequestRejected
from tests.unit.analysis.fakes import FakeProcessRunner
from tests.unit.builders import FakeClock

ADAPTER = PythonLanguageAdapter(FakeProcessRunner({}), StaticAnalysisOptions(), {})
VALIDATOR = SubmissionValidator(LanguageRegistry([ADAPTER]), max_bytes=20, max_lines=3)


def rejected(language: str, source: str) -> ErrorCode:
    with pytest.raises(RequestRejected) as raised:
        VALIDATOR.validate(language, source)
    return raised.value.code


@pytest.mark.parametrize(
    ("language", "source", "code"),
    [
        ("cobol", "x = 1", ErrorCode.UNSUPPORTED_LANGUAGE),
        ("Python", "x = 1", ErrorCode.UNSUPPORTED_LANGUAGE),
        ("python", "", ErrorCode.EMPTY_CODE),
        ("python", " \n\t ", ErrorCode.EMPTY_CODE),
        ("python", "﻿", ErrorCode.EMPTY_CODE),
        ("python", "x" * 21, ErrorCode.INPUT_TOO_LARGE),
        ("python", "é" * 11, ErrorCode.INPUT_TOO_LARGE),  # 22 bytes: bytes, not characters
        ("python", "a\r\nb\rc\nd", ErrorCode.INPUT_TOO_LARGE),  # 4 lines after normalization
        ("python", "x = 1\x00", ErrorCode.INVALID_REQUEST),
        ("python", "x = '\ud800'", ErrorCode.INVALID_REQUEST),  # lone surrogate
    ],
)
def test_rejections_in_processing_order(language: str, source: str, code: ErrorCode) -> None:
    assert rejected(language, source) is code


def test_exactly_the_limits_are_accepted_and_normalized() -> None:
    submission, adapter = VALIDATOR.validate("python", "﻿a\r\nb\rc")  # 3 lines
    assert adapter is ADAPTER
    assert submission.language is Language.PYTHON
    assert submission.source.text == "a\nb\nc"
    assert submission.source.byte_size == len("﻿a\r\nb\rc".encode())  # the submitted size
    assert VALIDATOR.validate("python", "x" * 20)[0].source.byte_size == 20


def test_normalization_changes_nothing_else() -> None:
    assert normalize_source("﻿﻿x\t \r\n") == "﻿x\t \n"


def test_deadline_counts_down_with_the_clock() -> None:
    clock = FakeClock()
    deadline = MonotonicDeadline(clock, 300)
    assert deadline.remaining() == 300
    clock.advance(301)
    assert deadline.remaining() == -1


def test_system_clock_is_monotonic_and_utc() -> None:
    clock = SystemClock()
    first = clock.monotonic()
    assert clock.monotonic() >= first
    assert clock.now().utcoffset() == timedelta(0)
