"""Per-operation AI call metrics for the AI stage log record (CIS §19.1, §9.3 step 6, D-77).

The orchestrator binds a collector around an AI operation; the provider fills in what it actually
observed. A value the provider never observed stays None and is left out of the record, so no
metric is ever invented.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass


@dataclass
class AICallMetrics:
    attempt: int | None = None  # the last call made: 1, or 2 after a retry
    seed: int | None = None
    num_predict: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration: int | None = None  # nanoseconds, as Ollama reports it
    token_estimate_exceeded: bool | None = None
    thinking_emitted: int | None = None  # calls whose response carried message.thinking

    def fields(self) -> dict[str, object]:
        return {k: v for k, v in asdict(self).items() if v is not None}


_current: ContextVar[AICallMetrics | None] = ContextVar("ai_call_metrics", default=None)


@contextmanager
def collect_ai_metrics() -> Iterator[AICallMetrics]:
    metrics = AICallMetrics()
    token = _current.set(metrics)
    try:
        yield metrics
    finally:
        _current.reset(token)


def current_ai_metrics() -> AICallMetrics:
    """The bound collector, or a throwaway one when no operation is being logged."""
    return _current.get() or AICallMetrics()
