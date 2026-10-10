"""GET /api/v1/reviews/{review_id} (CIS §6.4, §6.5, §6.8): every status, and not found."""

from typing import Any
from uuid import uuid4

import pytest

from ai.fake import TIMEOUT, UNAVAILABLE, FakeAIReviewProvider
from tests.unit.backend.api_client import CODE, REVIEWS, Api, error_of, scenario
from tests.unit.backend.pipeline_doubles import FAILED, GatedRunner, StubAdapter

RESULT_KEYS = {
    "summary", "score", "coverage", "issues", "total_issue_count", "severity_counts",
    "issues_truncated", "improved_code", "analysis", "capabilities", "warnings", "metadata",
}  # fmt: skip


async def finished(api: Api) -> dict[str, Any]:
    created = await api.submit()
    await api.context.jobs.wait_idle()
    response = await api.client.get(created.headers["Location"])
    assert response.status_code == 200
    resource: dict[str, Any] = response.json()
    return resource


def test_completed_review_maps_the_whole_result() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        assert (resource["status"], resource["stage"], resource["failure"]) == (
            "COMPLETED", "FINISHED", None,
        )  # fmt: skip
        assert resource["finished_at"].endswith("Z")
        result = resource["result"]
        assert set(result) == RESULT_KEYS
        assert result["capabilities"] == {
            "static_analysis": True, "ai_analysis": True, "improved_code": True,
        }  # fmt: skip
        assert set(result["severity_counts"]) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
        assert sum(result["severity_counts"].values()) == result["total_issue_count"]
        assert [i["issue_id"] for i in result["issues"]] == [
            f"ISS-{n:03d}" for n in range(1, len(result["issues"]) + 1)
        ]
        issue = result["issues"][0]
        assert set(issue["sources"][0]) == {"origin", "rule_key"}  # no internal finding IDs
        assert len(result["score"]["categories"]) == 6
        assert result["improved_code"] == {
            "status": "AVAILABLE", "code": "# Reviewed\n" + CODE, "notes": [],
            "failure_code": None, "message": None,
        }  # fmt: skip
        assert result["analysis"]["improvement"]["status"] == "SUCCEEDED"
        assert result["metadata"]["ai_provider"] == "fake"
        assert "started_at" not in result["metadata"]

    scenario(body)


def test_disabled_improvement_is_a_non_failure_skip() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        assert resource["status"] == "COMPLETED"
        result = resource["result"]
        assert result["capabilities"]["improved_code"] is False
        assert result["analysis"]["improvement"]["skip_reason"] == "DISABLED"
        assert result["improved_code"] == {
            "status": "UNAVAILABLE", "code": None, "notes": [], "failure_code": None,
            "message": "Improved-code generation is disabled.",
        }  # fmt: skip

    scenario(body, improvement_enabled=False)


def test_pending_and_running() -> None:
    async def body(api: Api) -> None:
        running = await api.submit()
        queued = await api.submit("x = 2\n")
        await api.client.get("/api/v1/health")  # lets the first job take the only slot
        first = (await api.client.get(running.headers["Location"])).json()
        second = (await api.client.get(queued.headers["Location"])).json()
        assert (first["status"], first["stage"]) == ("RUNNING", "GENERATING_IMPROVEMENT")
        assert (second["status"], second["stage"]) == ("PENDING", "QUEUED")
        assert first["result"] is None and second["result"] is None
        runner.gate.set()

    runner = GatedRunner()
    scenario(body, runner=runner, review_max_concurrent=1)


def test_partial_when_ai_is_unavailable() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        assert resource["status"] == "PARTIAL" and resource["failure"] is None
        result = resource["result"]
        assert result["analysis"]["ai_analysis"]["error_code"] == "AI_MODEL_UNAVAILABLE"
        assert result["summary"]["source"] == "GENERATED"
        assert result["capabilities"]["ai_analysis"] is False

    scenario(body, provider=FakeAIReviewProvider(review_script=[UNAVAILABLE]))


def test_failed_when_nothing_can_be_assessed() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        assert (resource["status"], resource["result"]) == ("FAILED", None)
        assert resource["failure"] == {
            "code": "REVIEW_TIMEOUT", "message": "The review took too long and was stopped.",
        }  # fmt: skip

    scenario(
        body,
        adapter=StubAdapter(pylint=FAILED, bandit=FAILED),
        provider=FakeAIReviewProvider(review_script=[TIMEOUT]),
    )


def test_polling_changes_nothing() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        again = (await api.client.get(f"{REVIEWS}/{resource['review_id']}")).json()
        assert again == resource

    scenario(body)


@pytest.mark.parametrize("path", ["not-a-uuid", "12345", str(uuid4())])
def test_malformed_and_unknown_ids_are_404(path: str) -> None:
    async def body(api: Api) -> None:
        response = await api.client.get(f"{REVIEWS}/{path}")
        assert response.status_code == 404
        assert error_of(response)["code"] == "REVIEW_NOT_FOUND"

    scenario(body)


def test_an_expired_review_is_404() -> None:
    async def body(api: Api) -> None:
        resource = await finished(api)
        api.clock.advance(901)
        response = await api.client.get(f"{REVIEWS}/{resource['review_id']}")
        assert error_of(response)["code"] == "REVIEW_NOT_FOUND"

    scenario(body)
