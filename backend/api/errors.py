"""Error responses and exception handlers (CIS §6.6, §6.7, §17).

Every non-2xx response is an ErrorBody with a fixed, safe message and the request ID. Submitted
values are never echoed: validation details carry a field path and a fixed phrase only.
"""

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.dtos import ErrorBody, ErrorDetail, ErrorInfo
from shared.domain.enums import ErrorCode
from shared.domain.errors import InvalidRequest, RequestRejected, ReviewNotFound, ServiceBusy

STATUS = {
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: 415,
    ErrorCode.EMPTY_CODE: 422,
    ErrorCode.INPUT_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_LANGUAGE: 422,
    ErrorCode.IDEMPOTENCY_CONFLICT: 422,
    ErrorCode.SERVICE_BUSY: 429,
    ErrorCode.REVIEW_NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}
RETRY_AFTER_SECONDS = "10"
INTERNAL_MESSAGE = "An unexpected error occurred. Please try again."

# Fixed problem phrases for request-validation errors (§6.6); never the rejected value.
PROBLEMS = {
    "missing": "is required",
    "extra_forbidden": "is not allowed",
    "string_type": "must be a string",
    "string_pattern_mismatch": "has an invalid format",
    "string_too_short": "is too short",
    "string_too_long": "is too long",
    "model_attributes_type": "must be a JSON object",
    "json_invalid": "is not valid JSON",
}
# Details for application rejections that concern one field (§6.3 steps 7, 9, 11).
REJECTION_DETAILS = {
    ErrorCode.EMPTY_CODE: ErrorDetail(field="source_code", problem="must not be blank"),
    ErrorCode.IDEMPOTENCY_CONFLICT: ErrorDetail(
        field="Idempotency-Key", problem="was used with a different payload"
    ),
    ErrorCode.INVALID_REQUEST: ErrorDetail(
        field="source_code", problem="contains characters that are not allowed"
    ),
}


def request_id(scope: Any) -> str:
    return str(scope.get("state", {}).get("request_id", ""))


def error_response(
    status: int,
    code: ErrorCode,
    message: str,
    request_id: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        error=ErrorInfo(code=code, message=message, details=details, request_id=request_id)
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=headers)


def internal_error(request_id: str) -> JSONResponse:
    return error_response(500, ErrorCode.INTERNAL_ERROR, INTERNAL_MESSAGE, request_id)


async def rejected(request: Request, error: Exception) -> JSONResponse:
    """RequestRejected raised by the application (§6.3 steps 6-12; GET not found)."""
    if not isinstance(error, RequestRejected):  # registered for this type only
        raise error
    detail = REJECTION_DETAILS.get(error.code)
    return error_response(
        STATUS[error.code],
        error.code,
        error.safe_message,
        request_id(request.scope),
        [detail] if detail else None,
        {"Retry-After": RETRY_AFTER_SECONDS} if isinstance(error, ServiceBusy) else None,
    )


def _field(problem: Any) -> str:
    """The dotted field path under the body or header; the whole body for malformed JSON."""
    if problem["type"] == "json_invalid":
        return "body"
    return ".".join(str(part) for part in problem["loc"][1:]) or "body"


async def invalid(request: Request, error: Exception) -> JSONResponse:
    """FastAPI's RequestValidationError: §6.3 steps 4-5, and a malformed review ID (§6.4)."""
    if not isinstance(error, RequestValidationError):  # registered for this type only
        raise error
    rid = request_id(request.scope)
    problems = error.errors()
    if any(p["loc"][0] == "path" for p in problems):
        return error_response(404, ErrorCode.REVIEW_NOT_FOUND, ReviewNotFound().safe_message, rid)
    details = [
        ErrorDetail(field=_field(p), problem=PROBLEMS.get(p["type"], "is invalid"))
        for p in problems  # the Pydantic `input` and `ctx` are deliberately dropped
    ]
    malformed = any(p["type"] == "json_invalid" for p in problems)
    return error_response(
        400 if malformed else 422,
        ErrorCode.INVALID_REQUEST,
        InvalidRequest().safe_message,
        rid,
        details,
    )


async def http_error(request: Request, error: Exception) -> JSONResponse:
    """Routing errors (unknown path, wrong method) in the ErrorBody shape."""
    if not isinstance(error, StarletteHTTPException):  # registered for this type only
        raise error
    if error.status_code >= 500:
        return internal_error(request_id(request.scope))
    return error_response(
        error.status_code,
        ErrorCode.INVALID_REQUEST,
        InvalidRequest().safe_message,
        request_id(request.scope),
        headers=dict(error.headers or {}),
    )
