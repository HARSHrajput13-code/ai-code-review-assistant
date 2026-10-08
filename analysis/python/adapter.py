"""PythonLanguageAdapter (CIS §5.8, §7.2 stages 3-4, §8).

Pylint and Bandit run concurrently; each one's failure is isolated and contributes no candidates.
Candidates are assembled in a fixed order (parser, Pylint, Bandit), never in completion order.
"""

import asyncio
import platform
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib import metadata

from analysis.process import ProcessRunner, ToolCommand
from analysis.python import bandit_tool, pylint_tool, rules
from analysis.python.syntax import check_syntax, validate_generated_code
from shared.domain.enums import Category, ErrorCode, Language, OutcomeStatus, SkipReason
from shared.domain.errors import StaticAnalysisFailed
from shared.domain.interfaces import Deadline, ProviderHealth
from shared.domain.models import (
    CodeValidation,
    FindingCandidate,
    SourceText,
    StageOutcome,
    StaticAnalysisResult,
    SyntaxCheck,
    ToolOutcome,
    ToolVersion,
)


@dataclass(frozen=True)
class StaticAnalysisOptions:
    """Typed configuration (CIS §16.1): STATIC_*_ENABLED and STATIC_TOOL_TIMEOUT_SECONDS."""

    pylint_enabled: bool = True
    bandit_enabled: bool = True
    tool_timeout_s: float = 30.0


@dataclass(frozen=True)
class _Tool:
    key: str  # "pylint" | "bandit"
    name: str  # for safe messages
    command: ToolCommand
    parse: Callable[..., tuple[tuple[FindingCandidate, ...], tuple[str, ...]]]


_PYLINT = _Tool(rules.PYLINT, pylint_tool.NAME, pylint_tool.COMMAND, pylint_tool.parse)
_BANDIT = _Tool(rules.BANDIT, bandit_tool.NAME, bandit_tool.COMMAND, bandit_tool.parse)

type _ToolRun = tuple[ToolOutcome, tuple[FindingCandidate, ...], tuple[str, ...]]


def installed_version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


class PythonLanguageAdapter:
    language = Language.PYTHON
    display_name = "Python"

    def __init__(
        self,
        runner: ProcessRunner,
        options: StaticAnalysisOptions,
        versions: Mapping[str, str | None] | None = None,
    ) -> None:
        self._runner = runner
        self._options = options
        # Read once at startup (§8.8); a missing tool fails on every review.
        self._versions = (
            dict(versions)
            if versions is not None
            else {
                rules.PYLINT: installed_version("pylint"),
                rules.BANDIT: installed_version("bandit"),
            }
        )
        self._parser_version = platform.python_version()

    def covered_categories(self, tool: str) -> tuple[Category, ...]:
        return rules.covered_categories(tool)

    def check_syntax(self, source: SourceText) -> SyntaxCheck:
        return check_syntax(source)

    def validate_generated_code(self, original: SourceText, generated: str) -> CodeValidation:
        return validate_generated_code(original, generated)

    def health(self) -> ProviderHealth:
        """Readiness of the static tools from the versions resolved at startup (§6.5)."""
        enabled = {
            rules.PYLINT: self._options.pylint_enabled,
            rules.BANDIT: self._options.bandit_enabled,
        }
        missing = [t for t, on in enabled.items() if on and not self._versions.get(t)]
        if missing:
            return ProviderHealth(available=False, detail=f"{', '.join(missing)} not installed")
        parts = [f"{rules.PARSER} {self._parser_version}"]
        parts += [
            f"{tool} {self._versions[tool]}" if on else f"{tool} disabled"
            for tool, on in enabled.items()
        ]
        return ProviderHealth(available=True, detail=", ".join(parts))

    def tool_versions(self) -> tuple[ToolVersion, ...]:
        versions = [ToolVersion(tool=rules.PARSER, version=self._parser_version)]
        versions += [ToolVersion(tool=t, version=v) for t, v in self._versions.items() if v]
        return tuple(versions)

    async def analyze(
        self, source: SourceText, syntax: SyntaxCheck, deadline: Deadline
    ) -> StaticAnalysisResult:
        parser = ToolOutcome(
            tool=rules.PARSER,
            tool_version=self._parser_version,
            outcome=StageOutcome(status=OutcomeStatus.SUCCEEDED),
        )
        pylint, bandit = await asyncio.gather(
            self._run(_PYLINT, self._options.pylint_enabled, source, syntax, deadline),
            self._run(_BANDIT, self._options.bandit_enabled, source, syntax, deadline),
        )
        syntax_candidates = () if syntax.candidate is None else (syntax.candidate,)
        return StaticAnalysisResult(
            syntax_valid=syntax.valid,
            tools=(parser, pylint[0], bandit[0]),
            candidates=syntax_candidates + pylint[1] + bandit[1],
            diagnostics=pylint[2] + bandit[2],
        )

    async def _run(
        self,
        tool: _Tool,
        enabled: bool,
        source: SourceText,
        syntax: SyntaxCheck,
        deadline: Deadline,
    ) -> _ToolRun:
        version = self._versions.get(tool.key)

        def outcome(status: OutcomeStatus, **fields: object) -> ToolOutcome:
            return ToolOutcome(
                tool=tool.key,
                tool_version=version,
                outcome=StageOutcome.model_validate({"status": status, **fields}),
            )

        def failed(code: ErrorCode, message: str, started: float) -> _ToolRun:
            elapsed = int((time.monotonic() - started) * 1000)
            return (
                outcome(
                    OutcomeStatus.FAILED, error_code=code, message=message, duration_ms=elapsed
                ),
                (),
                (),
            )

        if not enabled:
            return outcome(OutcomeStatus.SKIPPED, skip_reason=SkipReason.DISABLED), (), ()
        if not syntax.valid:
            return outcome(OutcomeStatus.SKIPPED, skip_reason=SkipReason.SYNTAX_ERROR), (), ()
        started = time.monotonic()
        if version is None:
            return failed(
                ErrorCode.STATIC_ANALYSIS_FAILURE, f"{tool.name} is not installed", started
            )
        remaining = deadline.remaining()
        if remaining <= 0:
            return (
                outcome(
                    OutcomeStatus.SKIPPED,
                    skip_reason=SkipReason.DEADLINE_EXCEEDED,
                    error_code=ErrorCode.REVIEW_TIMEOUT,
                ),
                (),
                (),
            )

        limit = self._options.tool_timeout_s
        timeout = min(limit, remaining)  # the overall deadline always wins (D-48)
        try:
            result = await self._runner.run(tool.command, source.text.encode("utf-8"), timeout)
            if result.timed_out and remaining < limit:
                return failed(ErrorCode.REVIEW_TIMEOUT, f"{tool.name} timed out", started)
            candidates, diagnostics = tool.parse(result, source.line_count)
        except StaticAnalysisFailed as error:
            return failed(error.code, error.safe_message, started)
        except Exception:  # isolated at the stage boundary (§7.2)
            return failed(
                ErrorCode.STATIC_ANALYSIS_FAILURE, f"{tool.name} could not be run", started
            )
        succeeded = outcome(OutcomeStatus.SUCCEEDED, duration_ms=result.duration_ms)
        return succeeded, candidates, diagnostics
