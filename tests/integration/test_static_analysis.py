"""Static analysis end to end with the real runner and the pinned tools (CIS §8, §20.3)."""

import asyncio
import os
from pathlib import Path

import pytest

from analysis.process import SafeProcessRunner
from analysis.python.adapter import PythonLanguageAdapter, StaticAnalysisOptions
from backend.review.numbering import number_static
from shared.domain.enums import OutcomeStatus, Severity, SkipReason
from shared.domain.models import Finding, Location, SourceText, StaticAnalysisResult
from tests.unit.analysis.fakes import FixedDeadline

ADAPTER = PythonLanguageAdapter(SafeProcessRunner(dict(os.environ)), StaticAnalysisOptions())


def analyze(text: str) -> StaticAnalysisResult:
    source = SourceText.of(text)
    return asyncio.run(ADAPTER.analyze(source, ADAPTER.check_syntax(source), FixedDeadline(300)))


def findings(text: str) -> tuple[Finding, ...]:
    analysis = analyze(text)
    assert {t.outcome.status for t in analysis.tools} == {OutcomeStatus.SUCCEEDED}
    return number_static(analysis.candidates, SourceText.of(text).line_count)


def by_rule(found: tuple[Finding, ...]) -> dict[str | None, Finding]:
    return {f.rule_key: f for f in found}


def test_the_pinned_tools_are_used() -> None:
    versions = {v.tool: v.version for v in ADAPTER.tool_versions()}
    assert (versions["pylint"], versions["bandit"]) == ("4.1.2", "1.9.4")


def test_clean_code_succeeds_with_no_findings() -> None:
    assert findings("def add(a, b):\n    return a + b\n") == ()


def test_pylint_findings_at_the_right_lines() -> None:
    found = by_rule(
        findings("import os\n\n\ndef collect(item, items=[]):\n    return items + [missing]\n")
    )
    assert found["pylint:W0102"].location == Location(start_line=4, end_line=4)
    assert found["pylint:E0602"].location == Location(start_line=5, end_line=5)
    assert found["pylint:W0611"].location == Location(start_line=1, end_line=1)


def test_bandit_findings_and_mapping() -> None:
    source = (
        "import subprocess\n\n\n"
        "def run(command):\n    return subprocess.call(command, shell=True)  # nosec\n\n\n"
        "def query(cursor, name):\n"
        "    assert name\n"
        "    return cursor.execute(\"SELECT * FROM users WHERE name = '%s'\" % name)\n"
    )
    found = by_rule(findings(source))
    assert found["bandit:B602"].severity is Severity.HIGH  # `# nosec` is ignored
    assert "bandit:B608" in found
    assert "bandit:B101" not in found and "bandit:B404" not in found  # skipped tests


def test_syntax_error_skips_the_tools() -> None:
    analysis = analyze("def broken(:\n    pass\n")
    assert {t.tool: t.outcome.skip_reason for t in analysis.tools}[
        "pylint"
    ] is SkipReason.SYNTAX_ERROR
    assert [c.rule_key for c in analysis.candidates] == ["python-parser:syntax-error"]


def test_submitted_code_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    source = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('x')\n"
        "import os\nos.system('echo executed > executed.txt')\n"
    )
    analyze(source)
    assert not marker.exists()


@pytest.mark.parametrize(
    "payload", ["; rm -rf /", "$(id)", "`whoami`", "| more", "& echo x", "%PATH%"]
)
def test_shell_metacharacters_in_source_are_inert(payload: str) -> None:
    analysis = analyze(f"x = {payload!r}\n")
    assert {t.outcome.status for t in analysis.tools} == {OutcomeStatus.SUCCEEDED}


def test_repeated_analysis_is_deterministic() -> None:
    source = "import os\nimport sys\n\n\ndef f(a=[]):\n    eval(a)\n    return a\n"
    assert findings(source) == findings(source)
