"""Request guard: request IDs, the HTTP-level checks of §6.3 steps 1-3, and the 500 catch-all.

A pure ASGI middleware, so it runs before FastAPI parses anything:
1. the body is counted while it is read (Content-Length first) and rejected with 413 once it
   exceeds MAX_REQUEST_BYTES, before any JSON parsing;
2. POST /api/v1/reviews requires `application/json` (optionally `charset=utf-8`), else 415;
3. a malformed `Idempotency-Key` is 400 INVALID_REQUEST.
Steps 4 onwards are FastAPI's parsing (errors.invalid) and the application (§6.3 steps 6-13).

X-Request-ID is correlation only: a valid client value is reused, otherwise a UUID4 is generated,
and it is echoed on every response. It never affects processing and is never conflated with the
Idempotency-Key, which is neither echoed nor logged.
"""

import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.dtos import ErrorDetail
from backend.api.errors import error_response, internal_error
from backend.application.events import diagnostics
from shared.domain.enums import ErrorCode
from shared.domain.errors import InputTooLarge, InvalidRequest, UnsupportedMediaType

logger = logging.getLogger(__name__)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:-]{16,128}$"
REVIEWS_PATH = "/api/v1/reviews"

type RequestContext = Callable[[str], AbstractContextManager[None]]


def is_json(content_type: str | None) -> bool:
    """`application/json`, optionally with `charset=utf-8` and nothing else (§6.1, D-58)."""
    if content_type is None:
        return False
    media_type, *parameters = (part.strip() for part in content_type.split(";"))
    if media_type.lower() != "application/json":
        return False
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name.strip().lower() != "charset" or value.strip().strip('"').lower() != "utf-8":
            return False
    return True


class _BodyTooLarge(Exception):
    pass


async def _read_body(receive: Receive, limit: int) -> bytes:
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise _BodyTooLarge
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


class RequestGuard:
    def __init__(
        self, app: ASGIApp, *, max_request_bytes: int, max_source_bytes: int,
        max_source_lines: int, request_context: RequestContext,
    ) -> None:  # fmt: skip
        self.app = app
        self.limit = max_request_bytes
        self.too_large = InputTooLarge(max_source_bytes, max_source_lines).safe_message
        self.request_context = request_context

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        supplied = headers.get("x-request-id")
        rid = supplied if supplied and REQUEST_ID_PATTERN.match(supplied) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = rid
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)["X-Request-ID"] = rid
            await send(message)

        with self.request_context(rid):
            try:
                body, rejection = b"", None
                length = headers.get("content-length")
                if length is not None and length.isdigit() and int(length) > self.limit:
                    rejection = self._too_large(rid)  # step 1, from the declared length
                else:
                    try:
                        body = await _read_body(receive, self.limit)  # step 1, counted
                    except _BodyTooLarge:
                        rejection = self._too_large(rid)
                rejection = rejection or self._contract_rejection(scope, headers, rid)
                if rejection is not None:
                    await rejection(scope, receive, send_with_id)
                    return
                await self.app(scope, _replay(body, receive), send_with_id)
            except Exception as error:  # §6.6: 500 INTERNAL_ERROR, no traceback in the body
                logger.error(
                    "request.failed",
                    extra={"method": scope["method"], "path": scope["path"], **diagnostics(error)},
                )
                if not started:
                    await internal_error(rid)(scope, receive, send_with_id)

    def _too_large(self, rid: str) -> Any:
        return error_response(413, ErrorCode.INPUT_TOO_LARGE, self.too_large, rid)

    def _contract_rejection(self, scope: Scope, headers: Headers, rid: str) -> Any:
        """§6.3 steps 2-3 for POST /api/v1/reviews, in order."""
        if scope["method"] != "POST" or scope["path"] != REVIEWS_PATH:
            return None
        if not is_json(headers.get("content-type")):  # step 2
            return error_response(
                415, ErrorCode.UNSUPPORTED_MEDIA_TYPE, UnsupportedMediaType().safe_message, rid
            )
        key = headers.get("idempotency-key")
        if key is not None and not re.match(IDEMPOTENCY_KEY_PATTERN, key):  # step 3
            detail = ErrorDetail(field="Idempotency-Key", problem="has an invalid format")
            return error_response(
                400, ErrorCode.INVALID_REQUEST, InvalidRequest().safe_message, rid, [detail]
            )
        return None


def _replay(body: bytes, receive: Receive) -> Receive:
    """Hand the already-read body to the application once, then defer to the server."""
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if sent:
            return await receive()
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay
