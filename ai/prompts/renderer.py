"""Loads the versioned prompt assets and renders them with string.Template (CIS §10, D-50, D-61).

The assets are loaded once at startup; a missing file, an unknown placeholder or a frozen version
whose files differ from its MANIFEST is a startup failure. Rendering uses `substitute`, so a
missing value raises, and inserted values are never interpreted, so `$` and `{}` in code are inert.
"""

import hashlib
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Literal

from ai.budget import (
    IMPROVEMENT_ISSUE_TITLE_CHARS,
    IMPROVEMENT_RECOMMENDATION_CHARS,
    PROMPT_MAX_IMPROVEMENT_ISSUES,
    PROMPT_MAX_STATIC_FINDINGS,
    STATIC_FINDING_MESSAGE_CHARS,
    STATIC_FINDING_TITLE_CHARS,
)
from ai.schemas import AIImprovementOutput, AIReviewOutput
from shared.domain.interfaces import AIImprovementRequest, AIReviewRequest
from shared.domain.models import Location

PROMPTS_DIR = Path(__file__).resolve().parent
MANIFEST = "MANIFEST"
SYSTEM_PLACEHOLDERS = frozenset({"language_display", "output_schema"})
PLACEHOLDERS: Mapping[str, frozenset[str]] = {  # each template uses exactly these (§10.1)
    "review_system.md": SYSTEM_PLACEHOLDERS,
    "review_user.md": frozenset({"nonce", "line_count", "static_findings", "numbered_source"}),
    "improve_system.md": SYSTEM_PLACEHOLDERS,
    "improve_user.md": frozenset({"nonce", "issues", "source"}),
}
Status = Literal["draft", "frozen"]
Operation = Literal["review", "improve"]


class PromptAssetError(ValueError):
    pass


@dataclass(frozen=True)
class PromptSet:
    version: str
    status: Status
    templates: Mapping[str, Template]


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    nonce: str


def new_nonce() -> str:
    """Fresh for every provider call, retries included; robustness, not security (§10.2)."""
    return secrets.token_hex(8)


def file_hash_lines(directory: Path) -> list[str]:
    """`sha256  filename` for each template, in name order (the MANIFEST body)."""
    return [
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}"
        for name in sorted(PLACEHOLDERS)
    ]


def manifest_text(status: Status, directory: Path) -> str:
    return "\n".join([f"status: {status}", *file_hash_lines(directory)]) + "\n"


def load_prompts(version: str, root: Path = PROMPTS_DIR) -> PromptSet:
    directory = root / version
    try:
        texts = {name: (directory / name).read_text(encoding="utf-8") for name in PLACEHOLDERS}
        manifest = (directory / MANIFEST).read_text(encoding="utf-8").splitlines()
    except OSError:
        raise PromptAssetError(f"prompt version {version} is missing an asset") from None
    templates = {}
    for name, text in texts.items():
        template = Template(text)
        if not template.is_valid() or set(template.get_identifiers()) != PLACEHOLDERS[name]:
            raise PromptAssetError(f"prompt {version}/{name} has invalid placeholders")
        templates[name] = template
    first = manifest[0] if manifest else ""
    if first not in ("status: draft", "status: frozen"):
        raise PromptAssetError(f"prompt {version}/{MANIFEST} has no valid status line")
    status = first.removeprefix("status: ")
    if status == "frozen" and manifest[1:] != file_hash_lines(directory):
        raise PromptAssetError(f"prompt {version} is frozen but its files differ from {MANIFEST}")
    return PromptSet(version, status, templates)  # type: ignore[arg-type]


def output_schemas() -> dict[Operation, dict[str, Any]]:
    """Each operation's generated output schema; never authored by hand (D-42)."""
    return {
        "review": AIReviewOutput.model_json_schema(),
        "improve": AIImprovementOutput.model_json_schema(),
    }


def schema_text(schema: Mapping[str, Any]) -> str:
    """The exact text of the generated schema, as sent in `format` and shown in the prompt."""
    return json.dumps(schema, ensure_ascii=False)


def render_system(prompts: PromptSet, operation: Operation, language: str, schema: str) -> str:
    return prompts.templates[f"{operation}_system.md"].substitute(
        language_display=language, output_schema=schema
    )


def _line(item: Mapping[str, object]) -> str:
    return json.dumps(item, ensure_ascii=True, separators=(",", ":"))


def _lines(location: Location | None) -> tuple[int | None, int | None]:
    return (location.start_line, location.end_line) if location else (None, None)


def omitted_static_findings(request: AIReviewRequest) -> int:
    return max(0, len(request.static_findings) - PROMPT_MAX_STATIC_FINDINGS)


def static_findings_block(request: AIReviewRequest) -> str:
    """At most 25 findings in S order, one JSON object per line (§10.2)."""
    ordered = sorted(request.static_findings, key=lambda f: int(f.finding_id[1:]))
    lines = []
    for finding in ordered[:PROMPT_MAX_STATIC_FINDINGS]:
        start, end = _lines(finding.location)
        lines.append(
            _line(
                {
                    "id": finding.finding_id,
                    "line": start,
                    "end_line": end,
                    "severity": finding.severity,
                    "category": finding.category,
                    "rule": finding.rule_key,
                    "title": finding.title[:STATIC_FINDING_TITLE_CHARS],
                    "message": finding.summary[:STATIC_FINDING_MESSAGE_CHARS],
                }
            )
        )
    if omitted := omitted_static_findings(request):
        lines.append(f"({omitted} further findings omitted)")
    return "\n".join(lines) if lines else "(none)"


def numbered_source(lines: Sequence[str]) -> str:
    width = len(str(len(lines)))
    return "\n".join(f"{n:>{width}} | {line}" for n, line in enumerate(lines, start=1))


def render_review(
    prompts: PromptSet, request: AIReviewRequest, system: str, nonce: str
) -> RenderedPrompt:
    user = prompts.templates["review_user.md"].substitute(
        nonce=nonce,
        line_count=request.source.line_count,
        static_findings=static_findings_block(request),
        numbered_source=numbered_source(request.source.lines()),
    )
    return RenderedPrompt(system, user, nonce)


def issues_block(request: AIImprovementRequest) -> str:
    lines = []
    for issue in request.issues[:PROMPT_MAX_IMPROVEMENT_ISSUES]:
        start, end = _lines(issue.location)
        lines.append(
            _line(
                {
                    "id": issue.issue_id,
                    "severity": issue.severity,
                    "category": issue.category,
                    "title": issue.title[:IMPROVEMENT_ISSUE_TITLE_CHARS],
                    "line": start,
                    "end_line": end,
                    "recommendation": issue.recommendation[:IMPROVEMENT_RECOMMENDATION_CHARS],
                }
            )
        )
    return "\n".join(lines) if lines else "(none)"


def render_improvement(
    prompts: PromptSet, request: AIImprovementRequest, system: str, nonce: str
) -> RenderedPrompt:
    user = prompts.templates["improve_user.md"].substitute(
        nonce=nonce, issues=issues_block(request), source=request.source.text
    )
    return RenderedPrompt(system, user, nonce)
