"""The live runner's pure parts (CIS §20.7 step 4, §20.10): no Ollama, no network."""

from typing import Any

from scripts import run_eval
from scripts.evaluation import CONTROLLED_CONFIG, Case, manifest_identifier

CASE = Case("security_a", "security", "x = 1\n", {"kind": "security", "planted_lines": [5]}, True)
OUTCOME_OK = {"status": "SUCCEEDED", "error_code": None, "skip_reason": None}


def resource(**result: Any) -> dict[str, Any]:
    analysis = {
        "static_analysis": OUTCOME_OK,
        "static_tools": [{"tool": "bandit", "outcome": OUTCOME_OK}],
        "ai_analysis": OUTCOME_OK,
        "improvement": {"status": "FAILED", "error_code": "IMPROVED_CODE_INVALID"},
    }
    body = {
        "analysis": analysis,
        "improved_code": {"status": "UNAVAILABLE"},
        "score": {"overall": 64},
        "issues": [
            {
                "category": "SECURITY", "severity": "HIGH", "location": {"start_line": 5,
                "end_line": 6}, "summary": "s", "impact": "i", "recommendation": "r",
            },
            {
                "category": "READABILITY", "severity": "LOW", "location": None,
                "summary": "s", "impact": " ", "recommendation": "r",
            },
        ],
    } | result  # fmt: skip
    return {"review_id": "rid", "status": "PARTIAL", "failure": None, "result": body}


RECORDS: list[dict[str, Any]] = [
    {"event": "review.stage.finished", "review_id": "rid", "stage": "AI_ANALYSIS",
     "status": "SUCCEEDED", "attempt": 2, "prompt_eval_count": 1200, "eval_count": 300,
     "total_duration": 9, "input_token_estimate": 1500, "token_estimate_exceeded": False,
     "thinking_emitted": 0},
    {"event": "review.stage.finished", "review_id": "rid", "stage": "IMPROVEMENT",
     "status": "FAILED", "attempt": 1, "prompt_eval_count": 800, "thinking_emitted": 0},
    {"event": "review.stage.finished", "review_id": "other", "stage": "AI_ANALYSIS",
     "status": "SUCCEEDED", "attempt": 1},
    {"event": "review.stage.finished", "review_id": "rid", "stage": "SCORING",
     "status": "SUCCEEDED"},
]  # fmt: skip


def test_a_terminal_review_becomes_a_case_result() -> None:
    found = run_eval.observe(CASE, resource(), RECORDS, 4321, (5_000, 3_000))
    assert (found.status, found.duration_ms, found.score) == ("PARTIAL", 4321, 64)
    assert found.review == ("SUCCEEDED", None)
    assert found.improvement == ("FAILED", "IMPROVED_CODE_INVALID")
    assert found.improved_code_status == "UNAVAILABLE"
    assert found.error_codes == ("IMPROVED_CODE_INVALID",)
    first, second = found.issues
    assert (first.category, first.start_line, first.end_line, first.complete) == (
        "SECURITY", 5, 6, True,
    )  # fmt: skip
    assert (second.start_line, second.complete) == (None, False)  # a blank impact
    assert [(c.operation, c.attempt) for c in found.calls] == [("review", 2), ("improve", 1)]
    assert found.calls[0].input_token_estimate == 1500  # only this review's stage records
    assert (found.model_size_bytes, found.model_vram_bytes) == (5_000, 3_000)


def test_a_failed_review_has_no_result() -> None:
    failed = {"review_id": "rid", "status": "FAILED", "failure": {"code": "REVIEW_TIMEOUT"},
              "result": None}  # fmt: skip
    found = run_eval.observe(CASE, failed, [], 300_000, (None, None))
    assert (found.status, found.review, found.improvement) == (
        "FAILED", ("FAILED", "REVIEW_TIMEOUT"), ("SKIPPED", None),
    )  # fmt: skip
    assert found.error_codes == ("REVIEW_TIMEOUT",) and found.issues == ()


def test_a_review_that_never_finished_is_a_hang() -> None:
    running = {"review_id": "rid", "status": "RUNNING"}
    assert run_eval.observe(CASE, running, [], 630_000, (None, None)).status == "HUNG"
    assert run_eval.observe(CASE, None, [], 630_000, (None, None)).status == "HUNG"


def test_the_runner_uses_exactly_the_controlled_configuration() -> None:
    settings = run_eval.settings_for("model:tag", 43)
    assert (settings.ollama_seed, settings.ollama_model, settings.review_max_concurrent) == (
        43, "model:tag", 1,
    )  # fmt: skip
    assert settings.ollama_temperature == CONTROLLED_CONFIG["temperature"]


def test_the_prompt_identity_is_checked_against_the_manifest() -> None:
    identifier, status = run_eval.prompt_identity()
    manifest = (run_eval.PROMPTS / "MANIFEST").read_text(encoding="utf-8")
    assert (identifier, status) == (manifest_identifier(manifest), "draft")
