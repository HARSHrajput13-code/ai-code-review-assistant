"""POST /api/v1/reviews (CIS §6.1, §6.3, §6.6, §20.3): every step, and first-match order."""

import json
import re
from collections.abc import AsyncIterator

import pytest

from tests.unit.backend.api_client import CODE, KEY, REVIEWS, Api, error_of, scenario
from tests.unit.backend.pipeline_doubles import GatedRunner

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
LIMIT = 2 * 1000 + 16_384  # MAX_REQUEST_BYTES with MAX_SOURCE_BYTES=1000
JSON = {"content-type": "application/json"}


def test_creation_is_202_pending_with_location_and_request_id() -> None:
    async def body(api: Api) -> None:
        response = await api.submit(key=KEY)
        assert response.status_code == 202
        resource = response.json()
        assert response.headers["Location"] == f"{REVIEWS}/{resource['review_id']}"
        assert (resource["status"], resource["stage"], resource["language"]) == (
            "PENDING", "QUEUED", "python",
        )  # fmt: skip
        assert (resource["result"], resource["failure"], resource["finished_at"]) == (
            None, None, None,
        )  # fmt: skip
        assert set(resource) == {
            "review_id", "status", "stage", "language", "created_at", "finished_at",
            "result", "failure",
        }  # fmt: skip
        assert resource["created_at"].endswith("Z")
        assert UUID4.match(response.headers["X-Request-ID"])

    scenario(body)


def test_same_key_and_payload_replays_with_200_and_the_same_location() -> None:
    async def body(api: Api) -> None:
        first = await api.submit(key=KEY)
        await api.context.jobs.wait_idle()
        again = await api.submit(key=KEY)
        assert (first.status_code, again.status_code) == (202, 200)
        assert again.json()["review_id"] == first.json()["review_id"]
        assert again.headers["Location"] == first.headers["Location"]
        assert again.json()["status"] == "COMPLETED"  # the current state
        assert len(api.context.jobs._store._entries) == 1  # type: ignore[attr-defined]

    scenario(body)


def test_same_key_with_a_different_payload_conflicts() -> None:
    async def body(api: Api) -> None:
        await api.submit(key=KEY)
        response = await api.submit(CODE + "\n", key=KEY)
        assert response.status_code == 422
        error = error_of(response)
        assert error["code"] == "IDEMPOTENCY_CONFLICT"
        assert error["details"] == [
            {"field": "Idempotency-Key", "problem": "was used with a different payload"}
        ]
        assert len(api.context.jobs._store._entries) == 1  # type: ignore[attr-defined]

    scenario(body)


def test_without_a_key_every_post_creates_a_review() -> None:
    async def body(api: Api) -> None:
        ids = {(await api.submit()).json()["review_id"] for _ in range(3)}
        assert len(ids) == 3

    scenario(body, review_max_queued=5)


@pytest.mark.parametrize("key", ["short", "x" * 129, "has space in it!", "bad/slash-0123456789"])
def test_a_malformed_idempotency_key_is_400(key: str) -> None:
    async def body(api: Api) -> None:
        response = await api.submit(key=key)
        assert response.status_code == 400
        error = error_of(response)
        assert error["code"] == "INVALID_REQUEST"
        assert error["details"] == [
            {"field": "Idempotency-Key", "problem": "has an invalid format"}
        ]
        assert key not in response.text

    scenario(body)


def test_request_ids_are_echoed_or_generated() -> None:
    async def body(api: Api) -> None:
        mine = await api.client.get("/api/v1/health", headers={"X-Request-ID": "client-id-1234"})
        assert mine.headers["X-Request-ID"] == "client-id-1234"
        generated = await api.client.get("/api/v1/health")
        assert UUID4.match(generated.headers["X-Request-ID"])
        for untrusted in ("short", "has spaces in it", "x" * 65, "semi;colon-1234"):
            replaced = await api.client.get("/api/v1/health", headers={"X-Request-ID": untrusted})
            assert UUID4.match(replaced.headers["X-Request-ID"])
        # Correlation only: the request ID never acts as an idempotency key.
        a = await api.client.post(REVIEWS, json={"language": "python", "source_code": CODE},
                                  headers={"X-Request-ID": "same-request-id"})  # fmt: skip
        b = await api.client.post(REVIEWS, json={"language": "python", "source_code": CODE},
                                  headers={"X-Request-ID": "same-request-id"})  # fmt: skip
        assert (a.status_code, b.status_code) == (202, 202)
        assert a.json()["review_id"] != b.json()["review_id"]

    scenario(body, review_max_queued=5)


@pytest.mark.parametrize(
    ("content_type", "status"),
    [
        ("application/json", 202),
        ("application/json; charset=utf-8", 202),
        ("Application/JSON; charset=UTF-8", 202),
        ("text/plain", 415),
        ("application/json; charset=latin-1", 415),
        ("application/x-www-form-urlencoded", 415),
        ("application/problem+json", 415),
        (None, 415),
    ],
)
def test_only_json_is_accepted(content_type: str | None, status: int) -> None:
    async def body(api: Api) -> None:
        headers = {"content-type": content_type} if content_type else {}
        content = json.dumps({"language": "python", "source_code": CODE}).encode()
        response = await api.client.post(REVIEWS, content=content, headers=headers)
        assert response.status_code == status
        if status == 415:
            assert error_of(response)["code"] == "UNSUPPORTED_MEDIA_TYPE"

    scenario(body)


def padded(size: int) -> bytes:
    """A JSON body of exactly `size` bytes with an extra field, so it fails only the schema."""
    prefix, suffix = b'{"language":"python","source_code":"x","pad":"', b'"}'
    return prefix + b"a" * (size - len(prefix) - len(suffix)) + suffix


def test_the_body_limit_is_exact_and_checked_before_parsing() -> None:
    async def body(api: Api) -> None:
        at_limit = await api.client.post(REVIEWS, content=padded(LIMIT), headers=JSON)
        assert at_limit.status_code == 422  # passed the size check; failed the schema
        over = await api.client.post(REVIEWS, content=padded(LIMIT + 1), headers=JSON)
        assert over.status_code == 413
        assert error_of(over)["code"] == "INPUT_TOO_LARGE"
        garbage = b"{" + b"SENTINEL_BODY_7F91" * 2000  # malformed JSON, but too large first
        rejected = await api.client.post(REVIEWS, content=garbage, headers=JSON)
        assert rejected.status_code == 413 and "SENTINEL" not in rejected.text

    scenario(body, max_source_bytes=1000)


def test_a_streamed_body_is_counted_without_content_length() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(20):
            yield b"a" * 1000

    async def body(api: Api) -> None:
        # Too large AND the wrong media type: the size (step 1) wins over the type (step 2).
        response = await api.client.post(
            REVIEWS, content=chunks(), headers={"content-type": "text/plain"}
        )
        assert "content-length" not in response.request.headers
        assert response.status_code == 413

    scenario(body, max_source_bytes=1000)


@pytest.mark.parametrize(
    ("content", "status", "details"),
    [
        (b'{"language": "python", ', 400, [{"field": "body", "problem": "is not valid JSON"}]),
        (b'{"language": "python"}', 422, [{"field": "source_code", "problem": "is required"}]),
        (
            b'{"language": "python", "source_code": "x", "extra": 1}',
            422,
            [{"field": "extra", "problem": "is not allowed"}],
        ),
        (
            b'{"language": "python", "source_code": 5}',
            422,
            [{"field": "source_code", "problem": "must be a string"}],
        ),
        (
            b'{"language": "Python 3!", "source_code": "x"}',
            422,
            [{"field": "language", "problem": "has an invalid format"}],
        ),
        (
            b'{"language": "", "source_code": "x"}',
            422,
            [{"field": "language", "problem": "is too short"}],
        ),
        (b"[1, 2]", 422, [{"field": "body", "problem": "must be a JSON object"}]),
        (b"", 422, [{"field": "body", "problem": "is required"}]),
    ],
)
def test_json_and_schema_errors(content: bytes, status: int, details: list[object]) -> None:
    async def body(api: Api) -> None:
        response = await api.client.post(REVIEWS, content=content, headers=JSON)
        assert response.status_code == status
        error = error_of(response)
        assert (error["code"], error["details"]) == ("INVALID_REQUEST", details)
        assert "input" not in response.text and "Python 3!" not in response.text

    scenario(body)


@pytest.mark.parametrize(
    ("language", "code", "status", "error_code"),
    [
        ("java", CODE, 422, "UNSUPPORTED_LANGUAGE"),
        ("python", "   \n\t ", 422, "EMPTY_CODE"),
        ("python", "x = 1\n" * 200, 413, "INPUT_TOO_LARGE"),  # 1,200 bytes > 1,000
        ("python", "\r\n" * 600 + "x", 413, "INPUT_TOO_LARGE"),  # 601 lines after CRLF → LF
        ("python", "x = 'SENTINEL_NUL_7F91'\x00", 422, "INVALID_REQUEST"),
        ("python", "x = 'SENTINEL_SURROGATE_7F91' # \ud800", 422, "INVALID_REQUEST"),
    ],
)
def test_application_validation(language: str, code: str, status: int, error_code: str) -> None:
    async def body(api: Api) -> None:
        response = await api.submit(code, language=language)
        assert response.status_code == status
        assert error_of(response)["code"] == error_code
        assert "SENTINEL" not in response.text and "x = 1" not in response.text

    scenario(body, max_source_bytes=1000, max_source_lines=600)


def test_messages_match_the_frontend_map() -> None:
    async def body(api: Api) -> None:
        too_large = error_of(await api.submit("x" * 1200))
        assert too_large["message"] == (
            "The code exceeds the maximum size of 1000 bytes or 500 lines."
        )
        assert error_of(await api.submit("  "))["message"] == "Please enter some code to review."

    scenario(body, max_source_bytes=1000)


def test_capacity_is_429_with_retry_after_but_a_replay_still_succeeds() -> None:
    async def body(api: Api) -> None:
        first = await api.submit(key=KEY)
        busy = await api.submit(CODE + "# other\n")
        assert busy.status_code == 429 and busy.headers["Retry-After"] == "10"
        assert error_of(busy)["code"] == "SERVICE_BUSY"
        replay = await api.submit(key=KEY)
        assert replay.status_code == 200
        assert replay.json()["review_id"] == first.json()["review_id"]
        runner.gate.set()

    runner = GatedRunner()
    scenario(body, runner=runner, review_max_concurrent=1, review_max_queued=0)


def test_a_rejected_request_records_no_key() -> None:
    async def body(api: Api) -> None:
        assert (await api.submit("   ", key=KEY)).status_code == 422
        assert (await api.submit(key=KEY)).status_code == 202

    scenario(body)


def test_first_match_order() -> None:
    async def body(api: Api) -> None:
        async def post(content: bytes, **headers: str) -> int:
            return (await api.client.post(REVIEWS, content=content, headers=headers)).status_code

        valid = json.dumps({"language": "python", "source_code": CODE}).encode()
        # 1 before 2: too large and not JSON.
        assert await post(b"a" * (LIMIT + 1), **{"content-type": "text/plain"}) == 413
        # 2 before 3: not JSON and a malformed key.
        assert await post(valid, **{"content-type": "text/plain", "Idempotency-Key": "bad"}) == 415
        # 3 before 4: a malformed key and malformed JSON.
        bad_key = await api.client.post(
            REVIEWS, content=b"{", headers={**JSON, "Idempotency-Key": "bad"}
        )
        assert error_of(bad_key)["details"][0]["field"] == "Idempotency-Key"
        # 5 before 6: a schema error with a known key.
        await api.submit(key=KEY)
        assert await post(b'{"language": "python"}', **JSON, **{"Idempotency-Key": KEY}) == 422
        # 7 before 8: a different payload with a known key and an unknown language.
        conflict = await api.submit(key=KEY, language="java")
        assert error_of(conflict)["code"] == "IDEMPOTENCY_CONFLICT"
        # 8 before 9: an unknown language and blank code.
        assert error_of(await api.submit("  ", language="java"))["code"] == "UNSUPPORTED_LANGUAGE"
        # 9 before 10: blank code above the byte limit.
        assert error_of(await api.submit(" " * 1500))["code"] == "EMPTY_CODE"
        # 10 before 11: too large and containing NUL.
        assert error_of(await api.submit("x" * 1500 + "\x00"))["code"] == "INPUT_TOO_LARGE"

    scenario(body, max_source_bytes=1000, review_max_queued=5)


def test_11_before_12_a_nul_is_rejected_even_when_the_service_is_full() -> None:
    async def body(api: Api) -> None:
        await api.submit()
        assert error_of(await api.submit("x\x00"))["code"] == "INVALID_REQUEST"
        assert error_of(await api.submit(CODE + "#\n"))["code"] == "SERVICE_BUSY"
        runner.gate.set()

    runner = GatedRunner()
    scenario(body, runner=runner, review_max_concurrent=1, review_max_queued=0)
