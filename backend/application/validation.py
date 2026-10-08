"""SubmissionValidator: §6.3 steps 8-11 and source normalization (CIS §7.1, §7.2)."""

from typing import Protocol

from shared.domain.enums import Language
from shared.domain.errors import EmptyCode, InputTooLarge, InvalidRequest, UnsupportedLanguage
from shared.domain.interfaces import LanguageAdapter
from shared.domain.models import ReviewSubmission, SourceText


class AdapterLookup(Protocol):
    """Satisfied by analysis.registry.LanguageRegistry; application code depends only on this."""

    def get(self, language: Language) -> LanguageAdapter | None: ...


def normalize_source(text: str) -> str:
    """Remove one leading BOM, then CRLF and CR to LF. Nothing else changes (§7.2)."""
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


class SubmissionValidator:
    def __init__(self, registry: AdapterLookup, max_bytes: int, max_lines: int) -> None:
        self._registry = registry
        self._max_bytes = max_bytes
        self._max_lines = max_lines

    def validate(self, language: str, source_code: str) -> tuple[ReviewSubmission, LanguageAdapter]:
        adapter = self._adapter(language)  # step 8
        if not source_code.strip():  # step 9
            raise EmptyCode()
        size = len(source_code.encode("utf-8", errors="surrogatepass"))
        normalized = normalize_source(source_code)
        if size > self._max_bytes or len(normalized.split("\n")) > self._max_lines:  # step 10
            raise InputTooLarge(self._max_bytes, self._max_lines)
        if "\x00" in source_code or not _encodable(source_code):  # step 11
            raise InvalidRequest()
        if not normalized.strip():  # only a BOM was submitted
            raise EmptyCode()
        source = SourceText(text=normalized, byte_size=size, line_count=len(normalized.split("\n")))
        return ReviewSubmission(language=adapter.language, source=source), adapter

    def _adapter(self, language: str) -> LanguageAdapter:
        try:
            adapter = self._registry.get(Language(language))
        except ValueError:
            adapter = None
        if adapter is None:
            raise UnsupportedLanguage()
        return adapter


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate
        return False
    return True
