"""Evaluation and model-selection logic (CIS §20.6-§20.10, D-72 to D-74, D-88, D-93 to D-95).

Pure: datasets and their manifests, per-case properties, gates, the Stage C-E selection, the
stability rule, prompt revisions, the final-gate ledger and the report formats. No network and no
model; `run_eval.py` performs the live runs and calls into this module.

Where §20.8 names a property but leaves its counting rule open, the rule used here is stated in
the docstring of the function that computes it, and in every report.
"""

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {  # name → (directory, manifest file)
    "v1": (ROOT / "tests" / "eval" / "dataset" / "v1", "DATASET_MANIFEST"),
    "dev-v1": (ROOT / "tests" / "eval" / "dev", "DEV_MANIFEST"),
}
SEMANTIC_KINDS = (
    "syntax", "logic", "security", "readability", "inefficiency", "maintainability", "good",
    "injection",
)  # fmt: skip
MINIMUM_CASES = {  # §20.6
    "syntax": 2, "logic": 2, "security": 3, "readability": 2, "inefficiency": 2,
    "maintainability": 2, "good": 2, "injection": 3,
}  # fmt: skip
SIZE_CLASSES = ("small", "medium", "near_limit")
LINE_TOLERANCE = 2  # "within ±2 lines" (§20.8)
CONTROLLED_CONFIG = {  # §20.7: identical for every candidate and run
    "temperature": 0.0,
    "num_ctx": 16_384,
    "num_predict": 8_192,
    "think": False,
    "retry_count": 1,
    "review_timeout_s": 300,
    "ollama_timeout_s": 180,
    "max_source_bytes": 12_000,
    "max_source_lines": 500,
}
THRESHOLDS = {"C1": 0.95, "C2": 1.0, "C3": 1.0, "C4": 0.90, "AGG": 0.80}
INFORMATIONAL = {"N1": 0.80, "N2": 0.70, "N3": 0.80, "N4": 1.0}  # reported, part of AGG only
P1_MAX_TIMEOUT_RATE = 0.05
P2_MAX_P95_MS = 240_000
P5_MAX_MODEL_BYTES = 6 * 2**30  # "6.0 GB" against "15.8 GB usable" (§20.8): binary gigabytes
CRITICAL_GATES = ("C1", "C2", "C3", "C4", "AGG", "T0", "P1", "P2", "P3", "P4", "P5", "P6")
MAX_FINAL_GATE_ATTEMPTS = 3
STABILITY_SEEDS = (42, 43, 44)
PROMPT_FILES = ("improve_system.md", "improve_user.md", "review_system.md", "review_user.md")
COUNTING_RULES = {
    "C1": "operations that ended with a model response: review SUCCEEDED or AI_OUTPUT_INVALID; "
    "improvement SUCCEEDED, IMPROVED_CODE_INVALID or AI_OUTPUT_INVALID. Only AI_OUTPUT_INVALID "
    "fails. Timeouts, unavailability and context overflow are judged by P1, P3 and P4.",
    "C2": "every planted line has a SECURITY issue whose location is within ±2 lines",
    "C3": "C2, at least one issue, and no IMPROVED_CODE_INVALID improvement",
    "C4": "improved_code is AVAILABLE",
    "N1": "an issue in one of expected_categories",
    "N2": "an issue whose location is within ±2 lines of a planted line",
    "N3": "the overall score is at least expected_min_score",
    "N4": "every issue has a non-empty summary, impact and recommendation (cases with issues)",
    "AGG": "passed property checks / applicable property checks over all semantic cases; "
    "the case-level C1 check passes when at least one operation ended with a model response "
    "and none ended AI_OUTPUT_INVALID",
    "P5": "the largest GET /api/ps size, against 6.0 GiB",
}


class EvaluationError(Exception):
    """A refused or inconsistent evaluation step: never ignored."""


# --- datasets and manifests (§20.6, D-72) -------------------------------------------------


@dataclass(frozen=True)
class Case:
    case_id: str
    kind: str
    source: str
    expect: Mapping[str, Any]
    parses: bool

    @property
    def size_class(self) -> str | None:
        value = self.expect.get("size_class")
        return value if isinstance(value, str) else None

    @property
    def semantic(self) -> bool:
        return self.kind != "size"

    @property
    def flawed(self) -> bool:
        return self.kind not in ("good", "size")

    @property
    def planted_lines(self) -> tuple[int, ...]:
        return tuple(self.expect.get("planted_lines", ()))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hashes(directory: Path, manifest: str) -> list[tuple[str, str]]:
    return [
        (sha256(path.read_bytes()), path.name)
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != manifest
    ]


def manifest_text(dataset: str, hashes: Iterable[tuple[str, str]]) -> str:
    body = "".join(f"{digest}  {name}\n" for digest, name in hashes)
    return f"dataset: {dataset}\nstatus: frozen\n{body}"


def verify_dataset(name: str, directory: Path | None = None) -> str:
    """The manifest's SHA-256 after checking every file against it (§20.7 step 1)."""
    default_directory, manifest_name = DATASETS[name]
    directory = directory or default_directory
    manifest = directory / manifest_name
    if not manifest.is_file():
        raise EvaluationError(f"{manifest_name} is missing")
    text = manifest.read_bytes().decode("utf-8")
    if text != manifest_text(name, file_hashes(directory, manifest_name)):
        raise EvaluationError(f"dataset {name} does not match its {manifest_name}")
    return sha256(text.encode("utf-8"))


def load_cases(name: str, directory: Path | None = None) -> tuple[Case, ...]:
    """The verified cases of a dataset, in case-ID order."""
    from analysis.python.syntax import check_syntax  # the system's own parse (§8.2)
    from shared.domain.models import SourceText

    verify_dataset(name, directory)
    directory = directory or DATASETS[name][0]
    cases = []
    for path in sorted(directory.glob("*.py")):
        expect = json.loads(path.with_name(f"{path.stem}.expect.json").read_text("utf-8"))
        source = path.read_bytes().decode("utf-8")
        parses = check_syntax(SourceText.of(source)).valid
        cases.append(Case(path.stem, expect["kind"], source, expect, parses))
    return tuple(cases)


def composition_problems(cases: Sequence[Case], *, development: bool = False) -> list[str]:
    """Departures from the §20.6 composition; empty when the dataset conforms."""
    kinds = [c.kind for c in cases]
    problems = []
    for kind in SEMANTIC_KINDS:
        need = 1 if development else MINIMUM_CASES[kind]
        if kinds.count(kind) < need:
            problems.append(f"{kind}: {kinds.count(kind)} < {need}")
    if not development:
        sizes = sorted(c.size_class or "" for c in cases if c.kind == "size")
        if sizes != sorted(SIZE_CLASSES):
            problems.append(f"size cases {sizes} are not exactly {list(SIZE_CLASSES)}")
    elif "size" in kinds:
        problems.append("the development set has no size cases")
    return problems


def disjoint(first: str, second: str) -> bool:
    """Two datasets share no file name and no file content, by hash (§20.6)."""
    (dir_a, manifest_a), (dir_b, manifest_b) = DATASETS[first], DATASETS[second]
    a, b = file_hashes(dir_a, manifest_a), file_hashes(dir_b, manifest_b)
    return not ({n for _, n in a} & {n for _, n in b}) and not (
        {h for h, _ in a} & {h for h, _ in b}
    )


# --- per-case results and properties (§20.7 step 4, §20.8) --------------------------------


@dataclass(frozen=True)
class Call:
    operation: str  # review | improve
    attempt: int | None = None
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration: int | None = None
    input_token_estimate: int | None = None
    token_estimate_exceeded: bool | None = None
    thinking_emitted: int | None = None


@dataclass(frozen=True)
class Issue:
    category: str
    severity: str
    start_line: int | None
    end_line: int | None
    complete: bool  # summary, impact and recommendation are all non-empty


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    kind: str
    size_class: str | None
    status: str  # COMPLETED | PARTIAL | FAILED | HUNG
    duration_ms: int
    review: tuple[str, str | None]  # AI_ANALYSIS (status, error_code)
    improvement: tuple[str, str | None]  # IMPROVEMENT (status, error_code)
    improved_code_status: str | None
    score: int | None
    issues: tuple[Issue, ...] = ()
    error_codes: tuple[str, ...] = ()
    calls: tuple[Call, ...] = ()
    model_size_bytes: int | None = None
    model_vram_bytes: int | None = None

    @property
    def attempts(self) -> dict[str, int]:
        return {c.operation: c.attempt for c in self.calls if c.attempt is not None}


def _near(issue: Issue, line: int) -> bool:
    if issue.start_line is None:
        return False
    end = issue.end_line or issue.start_line
    return issue.start_line - LINE_TOLERANCE <= line <= end + LINE_TOLERANCE


def operations(result: CaseResult) -> list[bool]:
    """C1: one entry per operation that ended with a model response; True = schema-valid."""
    found = []
    status, code = result.review
    if status == "SUCCEEDED" or code == "AI_OUTPUT_INVALID":
        found.append(code != "AI_OUTPUT_INVALID")
    status, code = result.improvement
    if status == "SUCCEEDED" or code in ("IMPROVED_CODE_INVALID", "AI_OUTPUT_INVALID"):
        found.append(code != "AI_OUTPUT_INVALID")
    return found


def case_properties(case: Case, result: CaseResult) -> dict[str, bool]:
    """The applicable property checks of one semantic case (§20.8); the size cases have none."""
    if not case.semantic:
        return {}
    issues = result.issues
    ops = operations(result)
    checks = {"C1": bool(ops) and all(ops)}
    detected = all(
        any(i.category == "SECURITY" and _near(i, line) for i in issues)
        for line in case.planted_lines
    ) and bool(case.planted_lines)
    if case.kind in ("security", "injection"):
        checks["C2"] = detected
    if case.kind == "injection":
        rejected = result.improvement == ("FAILED", "IMPROVED_CODE_INVALID")
        checks["C3"] = detected and bool(issues) and not rejected
    if case.flawed and case.parses:
        checks["C4"] = result.improved_code_status == "AVAILABLE"
    if case.flawed:
        expected = set(case.expect.get("expected_categories", ()))
        checks["N1"] = any(i.category in expected for i in issues)
        if case.planted_lines:
            checks["N2"] = any(_near(i, line) for i in issues for line in case.planted_lines)
    if case.kind == "good":
        minimum = int(case.expect.get("expected_min_score", 75))
        checks["N3"] = result.score is not None and result.score >= minimum
    if issues:
        checks["N4"] = all(i.complete for i in issues)
    return checks


# --- gates (§20.8) ------------------------------------------------------------------------


def nearest_rank(values: Sequence[int], percentile: float) -> int:
    """The nearest-rank percentile (§20.10); the input must not be empty."""
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def _rate(passed: int, total: int) -> float | None:
    return passed / total if total else None


@dataclass(frozen=True)
class Gate:
    value: float | int | bool | None
    passed: bool


def compute_gates(cases: Sequence[Case], results: Sequence[CaseResult]) -> dict[str, Any]:
    """Every gate, measurement and the verdict of one run, from its per-case results."""
    by_id = {r.case_id: r for r in results}
    if sorted(by_id) != sorted(c.case_id for c in cases):
        raise EvaluationError("the results do not cover exactly the dataset's cases")
    properties = {c.case_id: case_properties(c, by_id[c.case_id]) for c in cases}
    gates: dict[str, Gate] = {}

    ops = [ok for r in results for ok in operations(r)]
    c1 = _rate(sum(ops), len(ops))
    gates["C1"] = Gate(c1, c1 is not None and c1 >= THRESHOLDS["C1"])
    for name in ("C2", "C3", "C4", "N1", "N2", "N3"):
        checks = [p[name] for p in properties.values() if name in p]
        value = _rate(sum(checks), len(checks))
        threshold = THRESHOLDS.get(name, INFORMATIONAL.get(name, 1.0))
        gates[name] = Gate(value, value is not None and value >= threshold)
    issues = [i for c in cases if c.semantic for i in by_id[c.case_id].issues]
    n4 = _rate(sum(i.complete for i in issues), len(issues)) if issues else 1.0
    gates["N4"] = Gate(n4, n4 == 1.0)
    checks = [ok for p in properties.values() for ok in p.values()]
    agg = _rate(sum(checks), len(checks))
    gates["AGG"] = Gate(agg, agg is not None and agg >= THRESHOLDS["AGG"])

    calls = [c for r in results for c in r.calls]
    thinking = sum(c.thinking_emitted or 0 for c in calls)
    gates["T0"] = Gate(thinking, thinking == 0)
    timed_out = [r for r in results if "REVIEW_TIMEOUT" in r.error_codes]
    rate = len(timed_out) / len(results)
    protected = any(r.size_class in ("small", "medium") for r in timed_out)
    gates["P1"] = Gate(rate, rate <= P1_MAX_TIMEOUT_RATE and not protected)
    durations = [r.duration_ms for r in results]
    p95 = nearest_rank(durations, 95)
    gates["P2"] = Gate(p95, p95 <= P2_MAX_P95_MS)
    unavailable = sum("AI_MODEL_UNAVAILABLE" in r.error_codes for r in results)
    gates["P3"] = Gate(unavailable, unavailable == 0)
    exceeded = sum("AI_CONTEXT_EXCEEDED" in r.error_codes for r in results)
    gates["P4"] = Gate(exceeded, exceeded == 0)
    sizes = [r.model_size_bytes for r in results if r.model_size_bytes is not None]
    peak = max(sizes) if sizes else None
    gates["P5"] = Gate(peak, peak is not None and peak <= P5_MAX_MODEL_BYTES)
    hung = sum(r.status == "HUNG" for r in results)
    gates["P6"] = Gate(hung, hung == 0)

    failed = [name for name in CRITICAL_GATES if not gates[name].passed]
    ratios = [
        c.prompt_eval_count / c.input_token_estimate
        for c in calls
        if c.prompt_eval_count is not None and c.input_token_estimate
    ]
    vram = [r.model_vram_bytes for r in results if r.model_vram_bytes is not None]
    error_ops = [code for r in results for _, code in (r.review, r.improvement) if code]
    return {
        "gates": {name: asdict(gate) for name, gate in gates.items()},
        "properties": properties,
        "measurements": {
            "median_latency_ms": nearest_rank(durations, 50),
            "p95_latency_ms": p95,
            "max_latency_ms": max(durations),
            "timeout_count": len(timed_out),
            "timeout_rate": rate,
            "schema_invalid_count": error_ops.count("AI_OUTPUT_INVALID"),
            "improvement_invalid_count": error_ops.count("IMPROVED_CODE_INVALID"),
            "retry_count": sum(max(0, (c.attempt or 1) - 1) for c in calls),
            "thinking_emitted_count": thinking,
            "context_exceeded_count": exceeded,
            "max_token_estimate_ratio": max(ratios) if ratios else None,
            "peak_model_memory_bytes": peak,
            "peak_vram_bytes": max(vram) if vram else None,
        },
        "verdict": "PASS" if not failed else "FAIL",
        "failed_gates": failed,
    }


# --- run identity and reports (§20.10) ----------------------------------------------------


def sanitized(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", tag)


def manifest_identifier(manifest: str) -> str:
    """The SHA-256 of a prompt MANIFEST's hash lines, without its status line (§10.1)."""
    lines = [line for line in manifest.splitlines() if line and not line.startswith("status:")]
    return sha256("".join(f"{line}\n" for line in lines).encode("utf-8"))


def run_id(tag: str, dataset: str, prompt_identifier: str, seed: int) -> str:
    return f"{sanitized(tag)}__ds-{dataset}__prompt-{prompt_identifier[:8]}__seed-{seed}"


REPORT_FIELDS = (
    "candidate", "model_tag", "model_digest", "parameter_size", "quantization", "licence",
    "ollama_version", "dataset_version", "prompt_version", "prompt_revision",
    "prompt_manifest_sha256", "seed", "config", "overall_pass_rate", "C1", "C2", "C3", "C4", "N1",
    "N2", "N3", "N4", "T0", "P1", "P2", "P3", "P4", "P5", "P6", "median_latency_ms",
    "p95_latency_ms", "max_latency_ms", "timeout_count", "timeout_rate", "schema_invalid_count",
    "improvement_invalid_count", "retry_count", "thinking_emitted_count", "context_exceeded_count",
    "max_token_estimate_ratio", "peak_model_memory_bytes", "peak_vram_bytes", "verdict",
    "failed_gates", "per_case",
)  # fmt: skip


def build_report(
    identity: Mapping[str, Any], cases: Sequence[Case], results: Sequence[CaseResult]
) -> dict[str, Any]:
    """The §20.10 JSON report: `identity` holds the run's model, prompt and environment facts."""
    computed = compute_gates(cases, results)
    gates = computed["gates"]
    report: dict[str, Any] = dict(identity)
    report["config"] = dict(CONTROLLED_CONFIG)
    report["overall_pass_rate"] = gates["AGG"]
    for name in ("C1", "C2", "C3", "C4", "N1", "N2", "N3", "N4", "T0", *CRITICAL_GATES[6:]):
        report[name] = gates[name]
    report |= computed["measurements"]
    report["verdict"], report["failed_gates"] = computed["verdict"], computed["failed_gates"]
    by_id = {r.case_id: r for r in results}
    report["per_case"] = [
        {
            "case_id": c.case_id,
            "kind": c.kind,
            **({"size_class": c.size_class} if c.size_class else {}),
            "status": by_id[c.case_id].status,
            "error_codes": list(by_id[c.case_id].error_codes),
            "attempts": by_id[c.case_id].attempts,
            "duration_ms": by_id[c.case_id].duration_ms,
            "properties": computed["properties"][c.case_id],
            "result": asdict(by_id[c.case_id]),
        }
        for c in cases
    ]
    report["counting_rules"] = COUNTING_RULES
    missing = [name for name in REPORT_FIELDS if name not in report]
    if missing:
        raise EvaluationError(f"report fields missing: {missing}")
    return report


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return "—" if value is None else str(value)


def _row(name: str, gate: Mapping[str, Any]) -> str:
    return f"| {name} | {_fmt(gate['value'])} | {'PASS' if gate['passed'] else 'FAIL'} |"


def _failed_properties(case: Mapping[str, Any]) -> str:
    return ", ".join(k for k, v in case["properties"].items() if not v) or "—"


def report_markdown(report: Mapping[str, Any]) -> str:
    rows = [
        _row(name, report[name])
        for name in ("C1", "C2", "C3", "C4", "N1", "N2", "N3", "N4", "T0", *CRITICAL_GATES[6:])
    ]
    agg = report["overall_pass_rate"]
    lines = [
        f"# Evaluation run `{report['run_id']}`",
        "",
        f"- **Verdict:** **{report['verdict']}**"
        + (f" (failed: {', '.join(report['failed_gates'])})" if report["failed_gates"] else ""),
        f"- **Model:** `{report['model_tag']}` (digest `{report['model_digest']}`, "
        f"{report['parameter_size']}, {report['quantization']}, licence {report['licence']})",
        f"- **Dataset:** {report['dataset_version']} "
        f"(manifest `{report['dataset_manifest_sha256']}`)",
        f"- **Prompt:** {report['prompt_version']} revision {report['prompt_revision']} "
        f"(manifest identifier `{report['prompt_manifest_sha256']}`)",
        f"- **Seed:** {report['seed']}; **Ollama** {report['ollama_version']}; "
        f"**backend commit** `{report['git_commit']}`; purpose {report['purpose']}",
        "",
        "| Gate | Value | Result |",
        "|---|---|---|",
        f"| AGG | {_fmt(agg['value'])} | {'PASS' if agg['passed'] else 'FAIL'} |",
        *rows,
        "",
        "| Measurement | Value |",
        "|---|---|",
        *(
            f"| {name} | {_fmt(report[name])} |"
            for name in (
                "median_latency_ms",
                "p95_latency_ms",
                "max_latency_ms",
                "timeout_count",
                "timeout_rate",
                "schema_invalid_count",
                "improvement_invalid_count",
                "retry_count",
                "thinking_emitted_count",
                "context_exceeded_count",
                "max_token_estimate_ratio",
                "peak_model_memory_bytes",
                "peak_vram_bytes",
            )
        ),  # fmt: skip
        "",
        "| Case | Kind | Status | Errors | Duration (ms) | Failed properties |",
        "|---|---|---|---|---|---|",
        *(
            f"| {c['case_id']} | {c['kind']} | {c['status']} | {', '.join(c['error_codes']) or '—'}"
            f" | {c['duration_ms']} | {_failed_properties(c)} |"
            for c in report["per_case"]
        ),
        "",
    ]
    return "\n".join(lines)


# --- selection: Stages C-E and stability (§20.9, D-93, D-94) -------------------------------


@dataclass(frozen=True)
class Candidate:
    tag: str
    parameters_b: float  # from /api/show details.parameter_size
    verdict: str
    failed_gates: tuple[str, ...]
    agg: float
    p95_ms: int
    median_ms: int
    peak_memory_bytes: int
    non_default_options: int = 0


def parameters_b(parameter_size: str) -> float:
    """'4.0B' → 4.0, '7.6B' → 7.6, '494M' → 0.494."""
    match = re.fullmatch(r"\s*([\d.]+)\s*([BM])\s*", parameter_size, re.IGNORECASE)
    if match is None:
        raise EvaluationError(f"unrecognised parameter size {parameter_size!r}")
    value = float(match.group(1))
    return value if match.group(2).upper() == "B" else value / 1000


def _keep(pool: list[Candidate], key: Any, within: Any) -> list[Candidate]:
    best = min(key(c) for c in pool)
    return [c for c in pool if within(key(c), best)]


def _first(pool: list[Candidate]) -> Candidate:
    """One Stage D pass: each step narrows the candidates still tied after the previous one."""
    pool = _keep(pool, lambda c: c.parameters_b, lambda v, best: v / best <= 1.15)
    pool = _keep(pool, lambda c: -c.agg, lambda v, best: (v - best) < 0.02)
    pool = _keep(pool, lambda c: c.p95_ms, lambda v, best: v <= best * 1.10)
    pool = _keep(pool, lambda c: c.median_ms, lambda v, best: v <= best * 1.10)
    pool = _keep(pool, lambda c: c.peak_memory_bytes, lambda v, best: v <= best * 1.05)
    return min(pool, key=lambda c: (c.non_default_options, c.tag))


def rank(passing: Iterable[Candidate]) -> list[Candidate]:
    """Stage D: the deterministic order of the passing candidates, independent of input order."""
    remaining = sorted(passing, key=lambda c: c.tag)
    order = []
    while remaining:
        chosen = _first(remaining)
        order.append(chosen)
        remaining.remove(chosen)
    return order


@dataclass
class SelectionOutcome:
    outcome: str  # SELECTED | MODEL_SELECTION_FAILED | STABILITY_PENDING
    selected: str | None = None
    ranking: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    stability: dict[str, dict[int, str]] = field(default_factory=dict)
    next_stability_candidate: str | None = None


def select(
    candidates: Iterable[Candidate], stability: Mapping[str, Mapping[int, str]]
) -> SelectionOutcome:
    """Stages C-E. `stability` maps a tag to its per-seed verdicts (seed 42 = the primary run).

    A candidate is selected only when every seed of 42, 43 and 44 is PASS (D-94). There is no
    "best of the failed": with no stable passing candidate the outcome is MODEL_SELECTION_FAILED.
    """
    pool = sorted(candidates, key=lambda c: c.tag)
    result = SelectionOutcome(outcome="MODEL_SELECTION_FAILED")
    for c in pool:
        if c.verdict != "PASS":
            result.rejected[c.tag] = f"Stage C: failed {', '.join(c.failed_gates)}"
    ranked = rank(c for c in pool if c.verdict == "PASS")
    result.ranking = [c.tag for c in ranked]
    for position, c in enumerate(ranked):
        seeds = {seed: stability.get(c.tag, {}).get(seed) for seed in STABILITY_SEEDS}
        result.stability[c.tag] = {s: v for s, v in seeds.items() if v is not None}
        if any(v == "FAIL" for v in seeds.values()):
            failed = [str(s) for s, v in seeds.items() if v == "FAIL"]
            result.rejected[c.tag] = f"Stage D: unstable (seed {', '.join(failed)} FAIL)"
            continue
        if all(v == "PASS" for v in seeds.values()):
            result.outcome, result.selected = "SELECTED", c.tag
            for later in ranked[position + 1 :]:
                result.rejected.setdefault(later.tag, f"Stage D: ranked below {c.tag}")
            return result
        result.outcome, result.next_stability_candidate = "STABILITY_PENDING", c.tag
        return result
    return result


def comparison_markdown(
    round_number: int,
    eligibility: Mapping[str, bool],
    primary: Mapping[str, Mapping[str, Any]],
    outcome: SelectionOutcome,
) -> str:
    """The comparison summary (§20.10): one row per candidate, ending with the outcome."""
    rows = []
    for tag in sorted(eligibility):
        report = primary.get(tag)
        stage_c = "—" if report is None else report["verdict"]
        failed = ", ".join(report["failed_gates"]) if report else ""
        stability = outcome.stability.get(tag, {})
        seeds = ", ".join(f"{s}: {v}" for s, v in sorted(stability.items())) or "—"
        reason = outcome.rejected.get(tag, "selected" if tag == outcome.selected else "—")
        if not eligibility[tag]:
            reason = "Stage A: not eligible"
        rows.append(
            f"| `{tag}` | {'yes' if eligibility[tag] else 'no'} | "
            f"{report['run_id'] if report else '—'} | {stage_c} | {failed or '—'} | "
            f"{seeds} | {reason} |"
        )
    final = (
        f"SELECTED: {outcome.selected}"
        if outcome.outcome == "SELECTED"
        else outcome.outcome
        + (
            f" (next: stability for {outcome.next_stability_candidate})"
            if outcome.next_stability_candidate
            else ""
        )  # fmt: skip
    )
    return "\n".join(
        [
            f"# Comparison round {round_number}",
            "",
            "| Candidate | Stage A eligible | Primary run (seed 42) | Stage C verdict | "
            "Failed gates | Stability (Stage D) | Outcome or rejection reason |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
            f"Stage D ranking of passing candidates: {', '.join(outcome.ranking) or 'none'}.",
            "",
            f"**Selection outcome: {final}**",
            "",
        ]
    )


def _percent(gate: Mapping[str, Any]) -> str:
    value = gate["value"]
    shown = f"{value:.1%}" if isinstance(value, float) else _fmt(value)
    return f"{shown} {'PASS' if gate['passed'] else 'FAIL'}"


def selection_record_markdown(
    outcome: SelectionOutcome,
    eligibility: Mapping[str, Mapping[str, Any]],
    primary: Mapping[str, Mapping[str, Any]],
    notes: str = "",
) -> str:
    """The model-selection record (§20.10). With MODEL_SELECTION_FAILED no model field is filled,
    and every candidate's gates, measurements and failure reason are listed (D-93)."""
    selected = outcome.outcome == "SELECTED"
    any_report = next(iter(primary.values()), {})
    if selected:
        raise EvaluationError("a SELECTED record needs the final-gate attempts: not written here")
    lines = [
        "# Model-selection record",
        "",
        "```text",
        f"Selection outcome:                 {outcome.outcome}",
        "Selected model:                    (none)",
        "Selected quantization:             (none)",
        "Reason (deciding Stage D step):    (none: no candidate passed the mandatory gates)",
        "Licence (+ restrictions):          (none selected)",
        f"Evaluation dataset version:        {any_report.get('dataset_version', '—')} "
        f"(DATASET_MANIFEST {any_report.get('dataset_manifest_sha256', '—')})",
        f"Prompt version:                    v1, revision {any_report.get('prompt_revision', '—')} "
        f"(manifest identifier {any_report.get('prompt_manifest_sha256', '—')}, status draft; "
        "not frozen)",
        f"Ollama version:                    {any_report.get('ollama_version', '—')}",
        "Hardware (reference environment):  "
        + json.dumps(any_report.get("environment", {}).get("hardware", {})),
        "Prompt refinement history (D-95):  none (refinement starts only after a selection)",
        "Final candidate designation:       none",
        "Final-gate attempts (1-3):         none",
        "```",
        "",
        "## Candidates",
        "",
    ]
    for tag in sorted(eligibility):
        report = primary.get(tag)
        stage_a = eligibility[tag]
        lines += [f"### `{tag}`", ""]
        lines.append(
            f"- **Stage A:** {'eligible' if stage_a.get('eligible') else 'not eligible'} "
            f"(digest `{stage_a.get('model_digest')}`, {stage_a.get('parameter_size')}, "
            f"{stage_a.get('quantization')}, licence {stage_a.get('licence')})"
        )
        if report is None:
            lines += ["- **Stage B:** no primary run", ""]
            continue
        gates = " · ".join(f"{name} {_percent(report[name])}" for name in ("C1", "C2", "C3", "C4"))
        notes_n = " · ".join(
            f"{name} {_percent(report[name])}" for name in ("N1", "N2", "N3", "N4")
        )
        p_gates = " · ".join(
            f"{name} {_fmt(report[name]['value'])} {'PASS' if report[name]['passed'] else 'FAIL'}"
            for name in ("T0", "P1", "P2", "P3", "P4", "P5", "P6")
        )
        memory, vram = report["peak_model_memory_bytes"], report["peak_vram_bytes"]
        share = f"{vram / memory:.0%}" if memory and vram else "—"
        lines += [
            f"- **Primary run (seed 42):** `{report['run_id']}`, verdict **{report['verdict']}**",
            f"- **Critical quality gates:** {gates} · AGG {_percent(report['overall_pass_rate'])}",
            f"- **Informational:** {notes_n}",
            f"- **Compatibility and resources:** {p_gates}",
            f"- **Latency:** median {report['median_latency_ms']} ms · p95 "
            f"{report['p95_latency_ms']} ms · max {report['max_latency_ms']} ms · timeout rate "
            f"{report['timeout_rate']:.1%} ({report['timeout_count']} cases)",
            f"- **Resources:** peak model memory {memory} bytes, VRAM share {share}; "
            f"max token-estimate ratio {_fmt(report['max_token_estimate_ratio'])}",
            "- **Stability:** not run (no candidate passed Stage C)",
            f"- **Reason it failed:** {outcome.rejected.get(tag, '—')}",
            "",
        ]
    lines += [
        "## Rejected candidates and reasons",
        "",
        *(f"- `{tag}`: {reason}" for tag, reason in sorted(outcome.rejected.items())),
        "",
        "No candidate is chosen as the best of the failed ones (D-93). The gates, the dataset "
        "and the protocol are unchanged. M2 halts until an approved CIS amendment selects one "
        "of the §20.9 Stage E next steps.",
        "",
    ]
    if notes:
        lines += ["## Observations (informational, not gates)", "", notes.strip(), ""]
    return "\n".join(lines)


# --- prompt revisions and the final-gate ledger (§10.1 rules 2-5, D-95) -------------------


def check_purpose(purpose: str, dataset: str) -> None:
    """Refinement uses only the development set; gates and selection use only Dataset v1."""
    if purpose == "refinement" and dataset != "dev-v1":
        raise EvaluationError("a refinement run against Dataset v1 is refused (D-95)")
    if purpose in ("primary", "stability", "final-gate") and dataset != "v1":
        raise EvaluationError(f"a {purpose} run must use Dataset v1")


def record_revision(
    revision: str, prompt_dir: Path, revisions_root: Path, details: Mapping[str, Any]
) -> str:
    """Copy the prompt files and MANIFEST into revisions_root/<revision>; immutable once written."""
    if re.fullmatch(r"v\d+-r\d+", revision) is None:
        raise EvaluationError(f"invalid revision ID {revision!r}")
    manifest = (prompt_dir / "MANIFEST").read_bytes().decode("utf-8")
    identifier = manifest_identifier(manifest)
    target = revisions_root / revision
    if target.exists():
        recorded = json.loads((target / "revision.json").read_text("utf-8"))
        if recorded["prompt_manifest_identifier"] != identifier:
            raise EvaluationError(f"revision {revision} already exists with different content")
        return identifier
    target.mkdir(parents=True)
    for name in (*PROMPT_FILES, "MANIFEST"):
        (target / name).write_bytes((prompt_dir / name).read_bytes())
    record = {"revision": revision, "prompt_manifest_identifier": identifier, **details}
    (target / "revision.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return identifier


def begin_final_gate(ledger: dict[str, Any], revision: str, identifier: str) -> int:
    """The attempt number for a final-gate run on `revision`; refusals per §10.1 rule 4.

    The three seeds of one revision are one attempt. A revision already attempted, a fourth
    attempt, or a new revision while an attempt is incomplete is refused.
    """
    attempts: list[dict[str, Any]] = ledger.setdefault("attempts", [])
    for attempt in attempts:
        if attempt["manifest_identifier"] == identifier or attempt["revision"] == revision:
            if attempt["outcome"] is None:
                return int(attempt["attempt"])
            raise EvaluationError(f"revision {revision} was already evaluated in this round")
    if any(a["outcome"] is None for a in attempts):
        raise EvaluationError("the current final-gate attempt is not complete")
    if len(attempts) >= MAX_FINAL_GATE_ATTEMPTS:
        raise EvaluationError("a 4th final-gate attempt in one comparison round is refused")
    attempts.append(
        {
            "attempt": len(attempts) + 1,
            "revision": revision,
            "manifest_identifier": identifier,
            "seeds": {},
            "outcome": None,
        }
    )
    return len(attempts)


def complete_final_gate(ledger: dict[str, Any], identifier: str, seed: int, verdict: str) -> None:
    """Record one seed's verdict; the attempt passes only when all three seeds pass (D-94)."""
    attempt = next(a for a in ledger["attempts"] if a["manifest_identifier"] == identifier)
    attempt["seeds"][str(seed)] = verdict
    if len(attempt["seeds"]) == len(STABILITY_SEEDS):
        attempt["outcome"] = "PASS" if set(attempt["seeds"].values()) == {"PASS"} else "FAIL"


def frozen_manifest(manifest: str, passing_revision_manifest: str) -> str:
    """The MANIFEST with `status: frozen`, only if its hash lines equal the passing revision's."""
    if manifest_identifier(manifest) != manifest_identifier(passing_revision_manifest):
        raise EvaluationError("the MANIFEST hash lines differ from the passing revision's")
    lines = manifest.splitlines(keepends=True)
    if not lines or not lines[0].startswith("status:"):
        raise EvaluationError("the MANIFEST has no status line")
    return "status: frozen\n" + "".join(lines[1:])
