"""AIResponseProcessor: model_validate_json → sanitize → re-validate → candidates (CIS §11.3, D-85).

Both providers return raw model text, which always goes through these functions.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from ai.location import match_location
from ai.schemas import AIImprovementOutput, AIReviewOutput
from shared.domain.enums import Provenance
from shared.domain.errors import AIResponseInvalid
from shared.domain.interfaces import AIReviewRequest
from shared.domain.models import FindingCandidate
from shared.domain.text import strip_control

REQUIRED_TEXTS = ("title", "summary", "impact", "recommendation")


@dataclass(frozen=True)
class ProcessedReview:
    summary: str
    candidates: tuple[FindingCandidate, ...]
    dropped_issue_count: int


def _clean(text: str) -> str:
    return strip_control(text).strip()


def process_review(raw: str, request: AIReviewRequest) -> ProcessedReview:
    try:
        output = AIReviewOutput.model_validate_json(raw)
    except ValidationError:
        raise AIResponseInvalid(retryable=True) from None
    summary = _clean(output.summary)
    if not summary:
        raise AIResponseInvalid(retryable=True)

    known_ids = {f.finding_id for f in request.static_findings}
    candidates, dropped = [], 0
    for index, issue in enumerate(output.issues):
        texts = {field: _clean(getattr(issue, field)) for field in REQUIRED_TEXTS}
        if not all(texts.values()):
            dropped += 1  # rejected after sanitization; never enters the domain
            continue
        evidence = _clean(issue.evidence) if issue.evidence is not None else None
        location, status = match_location(
            issue.line, issue.end_line, evidence or None, request.source
        )
        related = tuple(dict.fromkeys(i for i in issue.related_static_ids if i in known_ids))
        candidates.append(
            FindingCandidate(
                provenance=Provenance.AI,
                origin="ai",
                rule_key=None,
                severity=issue.severity,
                category=issue.category,
                location=location,
                location_status=status,
                tool_confidence=None,
                related_static_ids=related,
                ai_output_index=index,
                **texts,
            )
        )
    return ProcessedReview(summary, tuple(candidates), dropped)


def process_improvement(raw: str) -> tuple[str, tuple[str, ...]]:
    """The raw improved code (validated later by §14.3) and the non-blank notes."""
    try:
        output = AIImprovementOutput.model_validate_json(raw)
    except ValidationError:
        raise AIResponseInvalid(retryable=True) from None
    notes = tuple(note for note in (_clean(n) for n in output.notes) if note)
    return output.improved_code, notes
