"""Context budget (CIS §9.7, D-75, D-92): fixture system prompts, integer arithmetic."""

import pytest

from ai import budget
from shared.domain.errors import AIContextExceeded

SYS = 9_000  # the largest allowed rendered system prompt (fixture size)


def test_estimate_is_an_integer_ceiling() -> None:
    assert [budget.est(n) for n in (0, 1, 2, 3, 4, 34_800)] == [0, 1, 1, 1, 2, 11_600]


def test_worst_case_figures_reproduce_the_cis_exactly() -> None:
    assert budget.review_worst_case_total(SYS, 12_000, 500) == 16_208
    assert budget.improve_worst_case_total(SYS, 12_000) == 16_058
    assert budget.improve_min_output_tokens(12_000) == 5_112


def test_improvement_minimum_has_a_floor() -> None:
    assert budget.improve_min_output_tokens(10) == 1_024


def problems(**overrides: int) -> list[str]:
    values = {
        "num_ctx": 16_384,
        "num_predict": 8_192,
        "max_source_bytes": 12_000,
        "max_source_lines": 500,
        "review_system_bytes": SYS,
        "improve_system_bytes": SYS,
        **overrides,
    }
    return budget.startup_budget_problems(**values)


def test_defaults_fit() -> None:
    assert problems() == []


def test_v02_defaults_do_not_fit() -> None:
    found = problems(max_source_bytes=20_000, max_source_lines=800)
    assert len(found) == 2 and "19575" in found[0]


def test_num_predict_below_the_largest_minimum_output_fails() -> None:
    assert problems(num_predict=5_111) and not problems(num_predict=5_112)


@pytest.mark.parametrize("source_bytes", [100, 3_000, 12_000])
def test_request_budget_keeps_input_plus_output_within_the_context(source_bytes: int) -> None:
    system, user = "s" * SYS, "u" * (source_bytes + 10_000)
    sent = budget.actual_output_budget(system, user, num_ctx=16_384, num_predict=8_192)
    estimated = budget.est(len(system) + len(user))
    assert sent == min(8_192, 16_384 - estimated - 512)
    assert estimated + sent + 512 <= 16_384


@pytest.mark.parametrize(("available", "required"), [(-5, 4_096), (0, 1_024), (4_095, 4_096)])
def test_too_small_a_budget_raises_before_any_call(available: int, required: int) -> None:
    with pytest.raises(AIContextExceeded):
        budget.require_output_budget(available, required)


def test_the_smallest_budget_ever_sent_is_the_required_minimum() -> None:
    assert budget.require_output_budget(1_024, 1_024) == 1_024
