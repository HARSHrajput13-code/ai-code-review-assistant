"""Domain exceptions (CIS §17.2). Safe messages never contain source, raw tool or model output."""

from shared.domain.enums import ErrorCode


class ReviewError(Exception):
    def __init__(self, code: ErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


# Rejected synchronously, before a review is created (§6.3). The user messages are the §17.1 map.
class RequestRejected(ReviewError):
    pass


class InvalidRequest(RequestRejected):
    def __init__(self, safe_message: str = "The request was not valid.") -> None:
        super().__init__(ErrorCode.INVALID_REQUEST, safe_message)


class UnsupportedMediaType(RequestRejected):
    def __init__(self) -> None:
        super().__init__(ErrorCode.UNSUPPORTED_MEDIA_TYPE, "The request format is not supported.")


class EmptyCode(RequestRejected):
    def __init__(self) -> None:
        super().__init__(ErrorCode.EMPTY_CODE, "Please enter some code to review.")


class InputTooLarge(RequestRejected):
    def __init__(self, max_bytes: int, max_lines: int) -> None:
        super().__init__(
            ErrorCode.INPUT_TOO_LARGE,
            f"The code exceeds the maximum size of {max_bytes} bytes or {max_lines} lines.",
        )


class UnsupportedLanguage(RequestRejected):
    def __init__(self) -> None:
        super().__init__(ErrorCode.UNSUPPORTED_LANGUAGE, "This language is not supported yet.")


class IdempotencyConflict(RequestRejected):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "This submission conflicts with an earlier one. Please submit again.",
        )


class ServiceBusy(RequestRejected):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.SERVICE_BUSY, "Another review is still running. Please wait and try again."
        )


class ReviewNotFound(RequestRejected):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.REVIEW_NOT_FOUND,
            "This review is no longer available. Please submit the code again.",
        )


class StaticAnalysisFailed(ReviewError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(ErrorCode.STATIC_ANALYSIS_FAILURE, safe_message)


class AIProviderUnavailable(ReviewError):
    def __init__(self, safe_message: str = "AI analysis is unavailable.") -> None:
        super().__init__(ErrorCode.AI_MODEL_UNAVAILABLE, safe_message)


class AIProviderTimeout(ReviewError):
    def __init__(self, safe_message: str = "The AI request timed out.") -> None:
        super().__init__(ErrorCode.REVIEW_TIMEOUT, safe_message)


class AIContextExceeded(ReviewError):
    def __init__(
        self, safe_message: str = "The code is too large for the AI context window."
    ) -> None:
        super().__init__(ErrorCode.AI_CONTEXT_EXCEEDED, safe_message)


class AIResponseInvalid(ReviewError):
    def __init__(self, retryable: bool, safe_message: str = "The AI response was invalid.") -> None:
        super().__init__(ErrorCode.AI_OUTPUT_INVALID, safe_message)
        self.retryable = retryable


class ImprovedCodeInvalid(ReviewError):
    def __init__(self, reason: str) -> None:
        super().__init__(ErrorCode.IMPROVED_CODE_INVALID, "Improved code could not be validated.")
        self.reason = reason  # internal only


class InvalidStateTransition(ReviewError):
    """A programming error (§5.7)."""

    def __init__(self, safe_message: str = "Invalid review state transition.") -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, safe_message)
