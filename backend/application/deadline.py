"""Clock and the monotonic review Deadline (CIS §7.1, D-48). Tests inject a fake clock."""

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)


class MonotonicDeadline:
    """The governing overall deadline, started when the job enters RUNNING."""

    def __init__(self, clock: Clock, seconds: float) -> None:
        self._clock = clock
        self._expires = clock.monotonic() + seconds

    def remaining(self) -> float:
        return self._expires - self._clock.monotonic()
