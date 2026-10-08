"""Candidate normalization (CIS §12.2). Pure and deterministic."""

from shared.domain.enums import LocationStatus, Provenance
from shared.domain.models import TEXT_LIMITS, FindingCandidate
from shared.domain.text import strip_control

ELLIPSIS = "…"


def truncate(text: str, limit: int) -> str:
    """Truncate at a word boundary, appending an ellipsis, so the result fits `limit`."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + ELLIPSIS


def normalize(candidate: FindingCandidate, line_count: int) -> FindingCandidate:
    static = candidate.provenance is Provenance.STATIC
    texts = {field: strip_control(getattr(candidate, field)) for field in TEXT_LIMITS}
    if static:  # AI text already satisfies the schema limits (§11)
        texts = {field: truncate(text, TEXT_LIMITS[field]) for field, text in texts.items()}
    if not texts["summary"].strip():
        texts["summary"] = texts["title"]

    where = candidate.location
    status = candidate.location_status
    if where is not None and where.end_line > line_count:
        where = None
        status = LocationStatus.NOT_PROVIDED if static else LocationStatus.UNMATCHED
    return candidate.model_copy(update={**texts, "location": where, "location_status": status})
