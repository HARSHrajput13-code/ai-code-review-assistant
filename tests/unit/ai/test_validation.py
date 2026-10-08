"""AI output schemas and post-sanitization re-validation (CIS §11, D-42, D-76, D-85)."""

import json
from pathlib import Path
from typing import Any

import pytest

from ai.schemas import AIImprovementOutput, AIReviewOutput
from ai.validation import process_improvement, process_review
from shared.domain.enums import Language, LocationStatus, Provenance
from shared.domain.errors import AIResponseInvalid
from shared.domain.interfaces import AIReviewRequest
from shared.domain.models import SourceText
from tests.unit.builders import static_finding

SOURCE = SourceText.of("import os\nx = eval(input())\n")
REQUEST = AIReviewRequest(
    language=Language.PYTHON, source=SOURCE, static_findings=(static_finding(1, line=1),)
)


def ai_issue(**changes: Any) -> dict[str, Any]:
    return {
        "severity": "HIGH",
        "category": "SECURITY",
        "title": "Use of eval on input",
        "line": 2,
        "end_line": 2,
        "evidence": "x = eval(input())",
        "summary": "eval runs arbitrary input.",
        "impact": "Arbitrary code execution.",
        "recommendation": "Parse the input explicitly.",
        "related_static_ids": [],
        **changes,
    }


def raw(*issues: dict[str, Any], summary: str = "Overall fine.", **extra: Any) -> str:
    return json.dumps({"summary": summary, "issues": list(issues), **extra})


def test_valid_issue_becomes_an_ai_candidate() -> None:
    processed = process_review(raw(ai_issue()), REQUEST)
    (found,) = processed.candidates
    assert (found.provenance, found.origin, found.rule_key, found.tool_confidence) == (
        Provenance.AI, "ai", None, None,
    )  # fmt: skip
    assert found.location_status is LocationStatus.SOURCE_MATCHED
    assert (found.ai_output_index, processed.dropped_issue_count) == (0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        raw(ai_issue(), unexpected=1),
        json.dumps({"summary": "s"}),
        raw(ai_issue(severity="URGENT")),
        raw(ai_issue(line="2")),
        raw(ai_issue(line=0)),
        raw(ai_issue(title="   ")),
        raw(ai_issue(title="x" * 151)),  # schema safety bound; AI text is never truncated
        raw(ai_issue(related_static_ids=["X1"])),
        raw(*[ai_issue()] * 21),
        raw(summary=""),
    ],
)
def test_schema_violations_are_retryable_invalid_output(text: str) -> None:
    with pytest.raises(AIResponseInvalid) as raised:
        process_review(text, REQUEST)
    assert raised.value.retryable


def test_blank_after_sanitization_issue_is_dropped_and_the_rest_kept() -> None:
    processed = process_review(raw(ai_issue(summary="\x01\x02"), ai_issue(title="Second")), REQUEST)
    assert processed.dropped_issue_count == 1
    assert [(c.title, c.ai_output_index) for c in processed.candidates] == [("Second", 1)]


def test_blank_after_sanitization_summary_invalidates_the_response() -> None:
    with pytest.raises(AIResponseInvalid) as raised:
        process_review(raw(ai_issue(), summary="\x07\x08"), REQUEST)
    assert raised.value.retryable


def test_control_characters_are_removed_and_blank_evidence_is_null() -> None:
    processed = process_review(raw(ai_issue(title="Use\x1b of eval", evidence="\x00")), REQUEST)
    (found,) = processed.candidates
    assert found.title == "Use of eval"
    assert found.location_status is LocationStatus.UNMATCHED  # line given, evidence missing


def test_unknown_related_static_ids_are_removed() -> None:
    issue = ai_issue(related_static_ids=["S1", "S9", "S1"])
    (found,) = process_review(raw(issue), REQUEST).candidates
    assert found.related_static_ids == ("S1",)


def test_improvement_output_is_kept_verbatim_and_blank_notes_dropped() -> None:
    code = "\tdef f():\n  pass\n\n\n"
    text = json.dumps({"improved_code": code, "notes": ["Fixed eval.", "\x01 "]})
    assert process_improvement(text) == (code, ("Fixed eval.",))  # blank after sanitization
    for invalid in ({"improved_code": "", "notes": []}, {"improved_code": "x", "notes": ["  "]}):
        with pytest.raises(AIResponseInvalid):  # blank after stripping: a schema violation
            process_improvement(json.dumps(invalid))


def test_generated_schema_requires_every_key_and_forbids_extras() -> None:
    schema = AIReviewOutput.model_json_schema()
    issue = schema["$defs"]["AIIssue"]
    assert schema["additionalProperties"] is False and issue["additionalProperties"] is False
    assert set(issue["required"]) == set(issue["properties"])
    assert set(AIImprovementOutput.model_json_schema()["required"]) == {"improved_code", "notes"}


def test_no_hand_authored_schema_files_exist_under_ai() -> None:
    assert list((Path(__file__).resolve().parents[3] / "ai").rglob("*.schema.json")) == []
