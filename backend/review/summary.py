"""The generated (fallback) summary, used when the AI stage did not succeed (CIS §12.8)."""

from shared.domain.enums import Category, Severity
from shared.domain.models import Issue


def generated_summary(
    *, syntax_valid: bool, issues: tuple[Issue, ...], unassessed: tuple[Category, ...]
) -> str:
    sentences = []
    if not syntax_valid:
        sentences.append("The code contains a syntax error, so it cannot run as written.")
    sentences.append(
        "AI analysis was unavailable, so this review is based on static analysis only."
    )
    if issues:
        counts = [
            f"{n} {severity.value.lower()}"
            for severity in Severity
            if (n := sum(1 for i in issues if i.severity is severity))
        ]
        sentences.append(f"{len(issues)} issue(s) were detected: {', '.join(counts)}.")
    else:
        sentences.append("No issues were detected by the checks that ran.")
    if unassessed:
        names = ", ".join(c.value.replace("_", " ").lower() for c in unassessed)
        sentences.append(f"Not assessed: {names}.")
    return " ".join(sentences)
