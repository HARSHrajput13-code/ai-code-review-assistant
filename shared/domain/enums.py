"""Domain enums (CIS §5.1). The value of each member is its name, except `Language`."""

from enum import StrEnum


class Language(StrEnum):
    PYTHON = "python"


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}[self.value]


class Category(StrEnum):
    """Members are declared in the canonical order."""

    CORRECTNESS = "CORRECTNESS"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    READABILITY = "READABILITY"
    MAINTAINABILITY = "MAINTAINABILITY"
    BEST_PRACTICE = "BEST_PRACTICE"


class Provenance(StrEnum):
    STATIC = "STATIC"
    AI = "AI"
    HYBRID = "HYBRID"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class LocationStatus(StrEnum):
    SOURCE_MATCHED = "SOURCE_MATCHED"
    NOT_PROVIDED = "NOT_PROVIDED"
    UNMATCHED = "UNMATCHED"


class OutcomeStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SkipReason(StrEnum):
    SYNTAX_ERROR = "SYNTAX_ERROR"
    NOT_NEEDED = "NOT_NEEDED"
    DISABLED = "DISABLED"
    DEPENDENCY_FAILED = "DEPENDENCY_FAILED"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"

    @property
    def failure_related(self) -> bool:
        return self in (SkipReason.DEPENDENCY_FAILED, SkipReason.DEADLINE_EXCEEDED)


class ErrorCode(StrEnum):
    """CIS §17.1."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    EMPTY_CODE = "EMPTY_CODE"
    INPUT_TOO_LARGE = "INPUT_TOO_LARGE"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SERVICE_BUSY = "SERVICE_BUSY"
    REVIEW_NOT_FOUND = "REVIEW_NOT_FOUND"
    STATIC_ANALYSIS_FAILURE = "STATIC_ANALYSIS_FAILURE"
    AI_MODEL_UNAVAILABLE = "AI_MODEL_UNAVAILABLE"
    AI_OUTPUT_INVALID = "AI_OUTPUT_INVALID"
    AI_CONTEXT_EXCEEDED = "AI_CONTEXT_EXCEEDED"
    IMPROVED_CODE_INVALID = "IMPROVED_CODE_INVALID"
    REVIEW_TIMEOUT = "REVIEW_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
