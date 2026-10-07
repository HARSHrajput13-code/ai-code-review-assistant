"""LanguageRegistry: the supported languages and their adapters (CIS §3, §4)."""

from collections.abc import Iterable

from shared.domain.enums import Language
from shared.domain.interfaces import LanguageAdapter


class LanguageRegistry:
    def __init__(self, adapters: Iterable[LanguageAdapter]) -> None:
        self._adapters: dict[Language, LanguageAdapter] = {}
        for adapter in adapters:
            if adapter.language in self._adapters:
                raise ValueError(f"duplicate adapter for {adapter.language}")
            self._adapters[adapter.language] = adapter

    def get(self, language: Language) -> LanguageAdapter | None:
        return self._adapters.get(language)

    def languages(self) -> tuple[Language, ...]:
        return tuple(self._adapters)
