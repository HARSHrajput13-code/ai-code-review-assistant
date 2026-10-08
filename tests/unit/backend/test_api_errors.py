"""Error bodies, the 500 catch-all and API log privacy (CIS §6.6, §17.3, §18 D-55, §20.3)."""

import dataclasses
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from ai.fake import FakeAIReviewProvider, FakeResponse
from backend.logging_setup import JsonFormatter
from tests.unit.backend.api_client import KEY, REVIEWS, Api, error_of, scenario

MARK = "LEAK_SENTINEL_7F91"
SOURCE = f"def f():\n    return '{MARK}_SOURCE'\n"
LEAK_KEY = f"key-{MARK}-0001"


class ExplodingJobs:
    """Stands in for ReviewJobService: an unexpected error whose message embeds the input."""

    async def submit(self, language: str, source_code: str, key: str | None) -> Any:
        raise RuntimeError(f"{MARK} {source_code} {key}")

    async def get(self, review_id: Any) -> Any:
        raise ValueError(rf"C:\internal\{MARK}\store.py")

    async def shutdown(self) -> None:
        pass


@pytest.fixture
def json_log() -> Iterator[io.StringIO]:
    """Every record, at DEBUG, through the production JSON formatter."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(previous)


def exploding(test: Any) -> None:
    def swap(api: Api) -> Any:
        jobs: Any = ExplodingJobs()
        api.app.state.context = dataclasses.replace(api.context, jobs=jobs)
        return test(api)

    scenario(swap)


def test_an_unexpected_error_is_a_safe_500(json_log: io.StringIO) -> None:
    async def body(api: Api) -> None:
        response = await api.submit(SOURCE, key=LEAK_KEY)
        assert response.status_code == 500
        error = error_of(response)
        assert error == {
            "code": "INTERNAL_ERROR",
            "message": "An unexpected error occurred. Please try again.",
            "details": None,
            "request_id": response.headers["X-Request-ID"],
        }
        assert MARK not in response.text and "Traceback" not in response.text
        assert "RuntimeError" not in response.text
        got = await api.client.get(f"{REVIEWS}/00000000-0000-4000-8000-000000000000")
        assert got.status_code == 500 and "internal" not in got.text

    exploding(body)
    records = [json.loads(line) for line in json_log.getvalue().splitlines()]
    failed = [r for r in records if r["event"] == "request.failed"]
    assert {r["exception_type"] for r in failed} == {"RuntimeError", "ValueError"}
    assert all(r["traceback"] and r["request_id"] for r in failed)  # frames, no message
    assert MARK not in json_log.getvalue()


def test_no_protected_data_reaches_the_logs(json_log: io.StringIO) -> None:
    review = json.dumps(
        {
            "summary": f"Summary {MARK}.",
            "issues": [
                {
                    "severity": "LOW", "category": "READABILITY", "title": f"Title {MARK}",
                    "line": 2, "end_line": 2, "evidence": f"    return '{MARK}_SOURCE'",
                    "summary": f"S {MARK}", "impact": f"I {MARK}", "recommendation": f"R {MARK}",
                    "related_static_ids": [],
                }
            ],
        }
    )  # fmt: skip

    async def body(api: Api) -> None:
        created = await api.submit(SOURCE, key=LEAK_KEY)
        await api.context.jobs.wait_idle()
        done = (await api.client.get(created.headers["Location"])).json()
        assert MARK in done["result"]["summary"]["text"]  # the sentinel did flow through
        for bad in (SOURCE + "\x00", " " * 5, SOURCE * 1000):
            await api.submit(bad, key=LEAK_KEY.replace("0001", "0002"))
        await api.client.post(REVIEWS, content=f'{{"{MARK}": 1'.encode(),
                              headers={"content-type": "application/json"})  # fmt: skip
        await api.client.post(REVIEWS, content=SOURCE.encode(), headers={"Idempotency-Key": MARK})

    scenario(body, provider=FakeAIReviewProvider(review_script=[FakeResponse(review)]))
    text = json_log.getvalue()
    assert "review.accepted" in text  # the log was captured, and it carries no payload
    assert MARK not in text and "return '" not in text


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("GET", "/api/v1/nothing", 404), ("DELETE", REVIEWS, 405), ("GET", REVIEWS, 405)],
)
def test_routing_errors_use_the_error_body(method: str, path: str, status: int) -> None:
    async def body(api: Api) -> None:
        response = await api.client.request(method, path)
        assert response.status_code == status
        assert error_of(response)["code"] == "INVALID_REQUEST"

    scenario(body)


def test_validation_errors_never_echo_input() -> None:
    async def body(api: Api) -> None:
        payload = {"language": f"bad {MARK}", "source_code": 7, "extra": MARK}
        response = await api.client.post(REVIEWS, json=payload)
        assert response.status_code == 422
        assert MARK not in response.text and '"input"' not in response.text
        assert '"ctx"' not in response.text and "http" not in response.text.lower()

    scenario(body)


def test_a_review_failure_is_not_an_http_error() -> None:
    async def body(api: Api) -> None:
        created = await api.submit(key=KEY)
        await api.context.jobs.wait_idle()
        response = await api.client.get(created.headers["Location"])
        assert response.status_code == 200
        assert response.json()["result"]["analysis"]["ai_analysis"]["error_code"] == (
            "AI_OUTPUT_INVALID"
        )

    invalid = FakeResponse(content="{not json")
    scenario(body, provider=FakeAIReviewProvider(review_script=[invalid, invalid]))
