"""PR-04 acceptance (CIS §22.5): HTTP → application → real static tools → fake AI → HTTP GET.

The default composition is used unchanged (real Pylint and Bandit through SafeProcessRunner, the
fake provider), served in process through httpx.ASGITransport. No server, no Ollama.
"""

import asyncio
from typing import Any

import httpx

from backend.composition import build_context
from backend.main import create_app
from tests.unit.backend.api_client import settings

SOURCE = (
    "import os\n"
    "import subprocess\n\n\n"
    "def run(command, items=[]):\n"
    "    return subprocess.call(command, shell=True)\n"
)


def review(source: str) -> tuple[int, dict[str, Any], dict[str, Any]]:
    async def body() -> tuple[int, dict[str, Any], dict[str, Any]]:
        context = build_context(settings())
        app = create_app(settings(), context)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            created = await client.post(
                "/api/v1/reviews",
                json={"language": "python", "source_code": source},
                headers={"Idempotency-Key": "acceptance-key-0001"},
            )
            await context.jobs.wait_idle()
            done = await client.get(created.headers["Location"])
            return created.status_code, created.json(), done.json()

    return asyncio.run(body())


def test_a_review_runs_through_the_api_with_the_real_tools_and_the_fake_ai() -> None:
    status, created, done = review(SOURCE)
    assert (status, created["status"]) == (202, "PENDING")
    assert done["review_id"] == created["review_id"]
    assert (done["status"], done["stage"]) == ("COMPLETED", "FINISHED")
    result = done["result"]
    rules = {s["rule_key"] for issue in result["issues"] for s in issue["sources"]}
    assert {"bandit:B602", "pylint:W0102", "pylint:W0611"} <= rules  # the real tools ran
    assert "AI" in {issue["provenance"] for issue in result["issues"]}  # the fake AI ran
    tools = {t["tool"]: t for t in result["analysis"]["static_tools"]}
    assert (tools["pylint"]["tool_version"], tools["bandit"]["tool_version"]) == (
        "4.1.2", "1.9.4",
    )  # fmt: skip
    assert result["score"]["overall"] == 70 and result["coverage"]["complete"]
    assert result["capabilities"] == {
        "static_analysis": True, "ai_analysis": True, "improved_code": False,
    }  # fmt: skip
    assert result["metadata"]["ai_provider"] == "fake"


def test_a_syntax_error_review_reports_static_analysis_as_usable() -> None:
    _, _, done = review("def broken(:\n    pass\n")
    result = done["result"]
    assert done["status"] == "COMPLETED" and result["score"]["overall"] <= 20
    assert result["capabilities"]["static_analysis"] is True  # §5.6: an invalid syntax is usable
    assert result["issues"][0]["sources"][0]["rule_key"] == "python-parser:syntax-error"
