"""Generated contracts (CIS §6.9, §11.4, D-38, D-42): the committed snapshots are the contract."""

import json
from typing import Any

import pytest

from scripts import export_contracts

LIVE = export_contracts.contracts()
ENDPOINTS = {
    ("post", "/api/v1/reviews"),
    ("get", "/api/v1/reviews/{review_id}"),
    ("get", "/api/v1/capabilities"),
    ("get", "/api/v1/health"),
    ("get", "/api/v1/health/ready"),
}


@pytest.mark.parametrize("path", sorted(LIVE), ids=lambda p: p.name)
def test_each_snapshot_matches_the_live_schema(path: Any) -> None:
    assert path.is_file(), f"run: uv run python -m scripts.export_contracts ({path.name})"
    assert path.read_text(encoding="utf-8") == LIVE[path]


def test_the_check_mode_agrees() -> None:
    assert export_contracts.main(["--check"]) == 0


def openapi() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        LIVE[export_contracts.ROOT / "shared/openapi/openapi.json"]
    )
    return document


def test_exactly_the_section_6_2_endpoints() -> None:
    operations = {(method, path) for path, item in openapi()["paths"].items() for method in item}
    assert operations == ENDPOINTS


def test_every_enum_is_an_explicit_string_union() -> None:
    schemas = openapi()["components"]["schemas"]
    enums = {name: s for name, s in schemas.items() if "enum" in s}
    assert {"ReviewStatus", "ReviewStage", "ErrorCode", "Severity", "Category"} <= set(enums)
    for schema in enums.values():
        assert schema["type"] == "string" and all(isinstance(v, str) for v in schema["enum"])


def test_errors_are_documented_as_the_error_body_only() -> None:
    document = openapi()
    assert "HTTPValidationError" not in document["components"]["schemas"]
    for item in document["paths"].values():
        for operation in item.values():
            for status, response in operation["responses"].items():
                if status.startswith(("4", "5")) or status == "default":
                    if status == "503":
                        continue  # readiness reports a degraded Readiness body
                    ref = response["content"]["application/json"]["schema"]["$ref"]
                    assert ref.endswith("/ErrorBody"), (status, ref)


def test_the_request_contract() -> None:
    document = openapi()
    request = document["components"]["schemas"]["ReviewCreateRequest"]
    assert request["additionalProperties"] is False
    assert request["required"] == ["language", "source_code"]
    assert request["properties"]["language"]["pattern"] == "^[a-z0-9_+-]+$"
    assert "enum" not in request["properties"]["language"]  # a string, not an enum (D-24)
    post = document["paths"]["/api/v1/reviews"]["post"]
    assert set(post["responses"]) >= {"200", "202", "400", "413", "415", "422", "429"}
    (header,) = [p for p in post["parameters"] if p["in"] == "header"]
    assert header["name"] == "Idempotency-Key" and not header.get("required", False)


def test_the_ai_schema_snapshots_forbid_extra_keys() -> None:
    root = export_contracts.ROOT / "shared/schemas"
    review = json.loads(LIVE[root / "ai_review_output.schema.json"])
    improvement = json.loads(LIVE[root / "ai_improvement_output.schema.json"])
    assert review["additionalProperties"] is False and improvement["additionalProperties"] is False
    assert review["$defs"]["AIIssue"]["additionalProperties"] is False
    assert improvement["properties"]["improved_code"]["maxLength"] == 200_000
