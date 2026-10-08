"""The §5.8 interface contracts and the `ai.provider` public API (CIS §3.1, §4, §5.8, §19.2)."""

import ast
import asyncio
import inspect
import typing
from datetime import UTC, datetime
from uuid import uuid4

import pytest

import ai.provider
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from shared.domain import interfaces, models
from shared.domain.enums import Language, OutcomeStatus, ReviewStatus
from shared.domain.interfaces import LanguageAdapter, ReviewRecordRepository
from shared.domain.models import CodeValidation, ReviewRecord, SourceText
from tests.architecture.modules import production_modules
from tests.architecture.test_import_layering import imports, under
from tests.unit.analysis.fakes import FakeProcessRunner

LANGUAGE_ADAPTER = {
    "language",
    "display_name",
    "covered_categories",
    "check_syntax",
    "analyze",
    "validate_generated_code",
    "tool_versions",
}


def test_language_adapter_exposes_every_section_5_8_member() -> None:
    assert typing.get_protocol_members(LanguageAdapter) == LANGUAGE_ADAPTER


def test_validate_generated_code_keeps_its_contract_signature() -> None:
    method = LanguageAdapter.validate_generated_code
    assert list(inspect.signature(method).parameters) == ["self", "original", "generated"]
    hints = typing.get_type_hints(method)
    assert hints == {"original": SourceText, "generated": str, "return": CodeValidation}


def test_the_python_adapter_implements_the_full_protocol() -> None:
    adapter: LanguageAdapter = PythonLanguageAdapter(  # checked by mypy as well
        FakeProcessRunner({}), StaticAnalysisOptions(), {"pylint": "4.1.2", "bandit": "1.9.4"}
    )
    assert all(hasattr(adapter, member) for member in LANGUAGE_ADAPTER)
    result = adapter.validate_generated_code(SourceText.of("x = 1\n"), "x = 2\n")
    assert result == CodeValidation(valid=True, reason=None)


# --- ReviewRecordRepository (§5.8, §19.2): the contract only; implementations live in
# backend/persistence (PR-10).

RECORD_COLUMNS = {
    "review_id", "created_at", "finished_at", "language", "status", "error_code",
    "overall_score", "score_provisional", "assessed_weight", "coverage_complete",
    "issue_count", "critical_count", "high_count", "medium_count", "low_count",
    "static_status", "ai_status", "improvement_status",
    "ai_provider", "ai_model", "prompt_version", "scoring_policy_version",
    "source_bytes", "source_lines", "duration_ms",
}  # fmt: skip


class MemoryRepository:
    """A test-only implementation, proving the Protocol can be satisfied from outside shared."""

    def __init__(self) -> None:
        self.saved: list[ReviewRecord] = []

    async def save(self, record: ReviewRecord) -> None:
        self.saved.append(record)

    def check_health(self) -> bool:
        return True


def record() -> ReviewRecord:
    now = datetime(2026, 10, 9, tzinfo=UTC)
    return ReviewRecord(
        review_id=uuid4(), created_at=now, finished_at=now, language=Language.PYTHON,
        status=ReviewStatus.COMPLETED, error_code=None, overall_score=97,
        score_provisional=False, assessed_weight=100, coverage_complete=True, issue_count=1,
        critical_count=0, high_count=0, medium_count=0, low_count=1,
        static_status=OutcomeStatus.SUCCEEDED, ai_status=OutcomeStatus.SUCCEEDED,
        improvement_status=OutcomeStatus.SKIPPED, ai_provider="fake", ai_model="fake",
        prompt_version="v1", scoring_policy_version="1.0", source_bytes=10, source_lines=1,
        duration_ms=5,
    )  # fmt: skip


def test_review_record_repository_matches_section_5_8() -> None:
    assert typing.get_protocol_members(ReviewRecordRepository) == {"save", "check_health"}
    assert inspect.iscoroutinefunction(ReviewRecordRepository.save)
    assert not inspect.iscoroutinefunction(ReviewRecordRepository.check_health)
    assert typing.get_type_hints(ReviewRecordRepository.save) == {
        "record": ReviewRecord,
        "return": type(None),
    }
    assert typing.get_type_hints(ReviewRecordRepository.check_health) == {"return": bool}


def test_a_repository_outside_shared_satisfies_the_protocol() -> None:
    repository: ReviewRecordRepository = MemoryRepository()  # checked by mypy as well
    asyncio.run(repository.save(record()))
    assert repository.check_health()


def test_the_review_record_holds_metadata_only() -> None:
    assert set(ReviewRecord.model_fields) == RECORD_COLUMNS
    with pytest.raises(ValueError):
        ReviewRecord.model_validate({**record().model_dump(), "source_code": "x = 1"})


def test_sqlite_is_used_only_by_backend_persistence() -> None:
    users = [
        module.name
        for module in production_modules()
        if any(under(name, "sqlite3") for name in imports(module))
    ]
    assert all(under(name, "backend.persistence") for name in users), users


# --- The ai.provider public API (§4): the shared definitions, re-exported, never copied.

PROVIDER_TYPES = {
    "AIReviewRequest": interfaces,
    "AIImprovementRequest": interfaces,
    "AIImprovementResult": interfaces,
    "AIReviewProvider": interfaces,
    "ProviderDescriptor": interfaces,
    "ProviderHealth": interfaces,
    "AIReviewResult": models,
}


@pytest.mark.parametrize("name", sorted(PROVIDER_TYPES))
def test_ai_provider_re_exports_the_shared_type(name: str) -> None:
    assert name in ai.provider.__all__
    assert getattr(ai.provider, name) is getattr(PROVIDER_TYPES[name], name)


def test_each_provider_type_is_defined_exactly_once() -> None:
    definitions: dict[str, list[str]] = {name: [] for name in PROVIDER_TYPES}
    for module in production_modules():
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ClassDef) and node.name in definitions:
                definitions[node.name].append(module.name)
    assert definitions == {name: [owner.__name__] for name, owner in PROVIDER_TYPES.items()}


def test_shared_never_imports_ai_so_there_is_no_cycle() -> None:
    for module in production_modules():
        if under(module.name, "shared"):
            assert not any(under(name, "ai") for name in imports(module)), module.name
