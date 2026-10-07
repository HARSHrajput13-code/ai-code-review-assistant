"""Python 3.14 compatibility of the locked backend dependency set (CIS §21.2)."""

import asyncio
import importlib
import platform
import sys

import httpx
import pydantic_core
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings

RUNTIME_MODULES = [
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic_settings",
    "httpx",
    "pylint",
    "bandit",
]


def test_interpreter_is_the_baseline() -> None:
    assert sys.version_info[:3] == (3, 14, 8)
    assert platform.python_implementation() == "CPython"
    assert sys.maxsize > 2**32


@pytest.mark.parametrize("module", RUNTIME_MODULES)
def test_runtime_dependency_imports(module: str) -> None:
    importlib.import_module(module)


class Item(BaseModel):
    name: str = Field(min_length=1)
    count: int


def test_pydantic_compiled_core_validates_rejects_and_emits_schema() -> None:
    assert pydantic_core._pydantic_core.__file__ is not None
    assert not pydantic_core._pydantic_core.__file__.endswith(".py")
    assert Item.model_validate_json('{"name": "a", "count": 1}') == Item(name="a", count=1)
    with pytest.raises(ValidationError):
        Item.model_validate_json('{"name": "", "count": "x"}')
    assert set(Item.model_json_schema()["required"]) == {"name", "count"}


def test_fastapi_request_round_trip() -> None:
    app = FastAPI()

    @app.post("/items")
    def create(item: Item) -> Item:
        return item

    async def round_trip() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            valid = await client.post("/items", json={"name": "a", "count": 2})
            invalid = await client.post("/items", json={"name": "a"})
        return valid, invalid

    valid, invalid = asyncio.run(round_trip())
    assert valid.json() == {"name": "a", "count": 2}
    assert invalid.status_code == 422


def test_pydantic_settings_validates_typed_values() -> None:
    class Limits(BaseSettings):
        max_items: int = 5

    assert Limits(max_items=7).max_items == 7
    with pytest.raises(ValidationError):
        Limits(max_items="many")  # type: ignore[arg-type]  # deliberately invalid


def test_httpx_mock_transport_round_trip() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        assert client.get("http://127.0.0.1:11434/api/version").json() == {"ok": True}
