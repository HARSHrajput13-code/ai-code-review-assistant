"""Defensive paths of the API layer that ordinary requests do not reach."""

import asyncio
from contextlib import nullcontext
from typing import Any

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from backend.api import errors
from backend.api.middleware import RequestGuard, is_json
from shared.domain.errors import ReviewNotFound

REQUEST = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {}})


@pytest.mark.parametrize("handler", [errors.rejected, errors.invalid, errors.http_error])
def test_handlers_reraise_what_they_were_not_registered_for(handler: Any) -> None:
    with pytest.raises(KeyError):
        asyncio.run(handler(REQUEST, KeyError("other")))


def test_a_server_side_http_error_becomes_internal_error() -> None:
    response = asyncio.run(errors.http_error(REQUEST, HTTPException(503)))
    assert response.status_code == 500 and b"INTERNAL_ERROR" in response.body
    assert asyncio.run(errors.rejected(REQUEST, ReviewNotFound())).status_code == 404
    invalid = RequestValidationError([{"type": "missing", "loc": ("body", "language")}])
    assert asyncio.run(errors.invalid(REQUEST, invalid)).status_code == 422


def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    guard = RequestGuard(
        app, max_request_bytes=10, max_source_bytes=1, max_source_lines=1,
        request_context=lambda rid: nullcontext(),
    )  # fmt: skip
    asyncio.run(guard({"type": "lifespan"}, None, None))  # type: ignore[arg-type]
    assert seen == ["lifespan"]


def test_a_client_disconnect_ends_the_body_read() -> None:
    received: list[Any] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        received.append(await receive())
        received.append(await receive())

    messages = iter([{"type": "http.disconnect"}, {"type": "http.disconnect"}])

    async def receive() -> Any:
        return next(messages)

    guard = RequestGuard(
        app, max_request_bytes=10, max_source_bytes=1, max_source_lines=1,
        request_context=lambda rid: nullcontext(),
    )  # fmt: skip
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    asyncio.run(guard(scope, receive, None))  # type: ignore[arg-type]
    assert received == [
        {"type": "http.request", "body": b"", "more_body": False},
        {"type": "http.disconnect"},
    ]


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("application/json", True),
        ('application/json; charset="utf-8"', True),
        ("application/json; boundary=x", False),
        ("application/jsonx", False),
        ("", False),
    ],
)
def test_media_type_parsing(value: str, accepted: bool) -> None:
    assert is_json(value) is accepted
