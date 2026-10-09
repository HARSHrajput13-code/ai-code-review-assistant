"""Context budget (CIS §9.7, D-75, D-92). Integer arithmetic only.

PR-03 provides the computation and the startup check, tested against fixture system-prompt sizes;
PR-06 measures the real rendered prompts and applies the request-time check before every call.
"""

from shared.domain.errors import AIContextExceeded

BYTES_PER_TOKEN_ESTIMATE = 3
CONTEXT_SAFETY_MARGIN_TOKENS = 512
REVIEW_MIN_OUTPUT_TOKENS = 4_096
IMPROVE_OUTPUT_FACTOR_PERCENT = 115  # IMPROVE_OUTPUT_FACTOR 1.15, in integer form
IMPROVE_NOTES_RESERVE_TOKENS = 512
IMPROVE_MIN_OUTPUT_FLOOR_TOKENS = 1_024
PROMPT_MAX_STATIC_FINDINGS = 25
STATIC_FINDING_LINE_BYTES = 400
STATIC_FINDING_TITLE_CHARS = 100  # §10.2 cuts; with the JSON keys a line can exceed 400 bytes
STATIC_FINDING_MESSAGE_CHARS = 200  # slightly, which the request-time check (actual bytes) covers
PROMPT_MAX_IMPROVEMENT_ISSUES = 20
IMPROVEMENT_ISSUE_LINE_BYTES = 500
IMPROVEMENT_ISSUE_TITLE_CHARS = 100  # §10.2 cuts; likewise a line can slightly exceed 500 bytes
IMPROVEMENT_RECOMMENDATION_CHARS = 300
SYSTEM_PROMPT_MAX_BYTES = 9_000
LINE_PREFIX_BYTES = 7
TASK_LINE_AND_DELIMITER_BYTES = 300


def est(utf8_bytes: int) -> int:
    """Estimated tokens: the integer ceiling of bytes / 3."""
    return (utf8_bytes + BYTES_PER_TOKEN_ESTIMATE - 1) // BYTES_PER_TOKEN_ESTIMATE


def improve_min_output_tokens(source_bytes: int) -> int:
    scaled = (IMPROVE_OUTPUT_FACTOR_PERCENT * est(source_bytes) + 99) // 100
    return max(IMPROVE_MIN_OUTPUT_FLOOR_TOKENS, scaled + IMPROVE_NOTES_RESERVE_TOKENS)


def review_worst_case_total(system_bytes: int, max_source_bytes: int, max_source_lines: int) -> int:
    findings = PROMPT_MAX_STATIC_FINDINGS * STATIC_FINDING_LINE_BYTES
    prefixes = max_source_lines * LINE_PREFIX_BYTES
    tokens = est(
        system_bytes + TASK_LINE_AND_DELIMITER_BYTES + findings + max_source_bytes + prefixes
    )
    return tokens + REVIEW_MIN_OUTPUT_TOKENS + CONTEXT_SAFETY_MARGIN_TOKENS


def improve_worst_case_total(system_bytes: int, max_source_bytes: int) -> int:
    issues = PROMPT_MAX_IMPROVEMENT_ISSUES * IMPROVEMENT_ISSUE_LINE_BYTES
    tokens = est(system_bytes + TASK_LINE_AND_DELIMITER_BYTES + issues + max_source_bytes)
    return tokens + improve_min_output_tokens(max_source_bytes) + CONTEXT_SAFETY_MARGIN_TOKENS


def startup_budget_problems(
    *,
    num_ctx: int,
    num_predict: int,
    max_source_bytes: int,
    max_source_lines: int,
    review_system_bytes: int,
    improve_system_bytes: int,
) -> list[str]:
    """Configuration-time validation (§9.7 layer 1). An empty list means the budget fits."""
    review = review_worst_case_total(review_system_bytes, max_source_bytes, max_source_lines)
    improve = improve_worst_case_total(improve_system_bytes, max_source_bytes)
    largest_minimum = max(REVIEW_MIN_OUTPUT_TOKENS, improve_min_output_tokens(max_source_bytes))
    problems = []
    if review > num_ctx:
        problems.append(f"worst-case review needs {review} tokens; OLLAMA_NUM_CTX is {num_ctx}")
    if improve > num_ctx:
        problems.append(
            f"worst-case improvement needs {improve} tokens; OLLAMA_NUM_CTX is {num_ctx}"
        )
    if num_predict < largest_minimum:
        problems.append(
            f"OLLAMA_NUM_PREDICT {num_predict} is below the minimum output {largest_minimum}"
        )
    return problems


def estimated_input_tokens(system_message: str, user_message: str) -> int:
    return est(len(system_message.encode("utf-8")) + len(user_message.encode("utf-8")))


def actual_output_budget(
    system_message: str, user_message: str, *, num_ctx: int, num_predict: int
) -> int:
    """`min(OLLAMA_NUM_PREDICT, available)`; the available budget may be zero or negative."""
    available = (
        num_ctx
        - estimated_input_tokens(system_message, user_message)
        - CONTEXT_SAFETY_MARGIN_TOKENS
    )
    return min(num_predict, available)


def require_output_budget(budget: int, required_min_output: int) -> int:
    """§9.7 decision rule: the num_predict to send, or AIContextExceeded with no call made."""
    if budget < required_min_output:
        raise AIContextExceeded()
    return budget
