"""Live model evaluation (CIS §20.7, §20.9, §20.10). Opt-in: needs Ollama and the candidates.

    uv run python scripts/run_eval.py eligibility --candidate <tag>          # Stage A
    uv run python scripts/run_eval.py run --candidate <tag> --seed 42        # one run (Stage B, D)
    uv run python scripts/run_eval.py compare --round 1                      # Stages C-E
    uv run python scripts/run_eval.py run --candidate <tag> --seed 42 \\
        --dataset dev-v1 --purpose refinement --revision v1-r0               # development set
    uv run python scripts/run_eval.py record-revision --revision v1-r0 --dev-report <run-id> \\
        --reason "..."                                                       # D-95 record

A run drives the real backend application in process (httpx.ASGITransport): every case is
POSTed and polled every 250 ms through the API, with AI_PROVIDER=ollama. The backend's own
stage records are captured to read the per-call metrics. Nothing here pulls a model, changes a
gate or edits a prompt; refusals are errors, never warnings.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # run as `python scripts/run_eval.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from scripts import check_environment  # noqa: E402
from scripts.evaluation import (  # noqa: E402
    CONTROLLED_CONFIG,
    PROMPT_FILES,
    ROOT,
    Call,
    Candidate,
    Case,
    CaseResult,
    EvaluationError,
    Issue,
    begin_final_gate,
    build_report,
    check_purpose,
    comparison_markdown,
    complete_final_gate,
    load_cases,
    manifest_identifier,
    parameters_b,
    record_revision,
    report_markdown,
    run_id,
    sanitized,
    select,
    sha256,
    verify_dataset,
)

OLLAMA = "http://127.0.0.1:11434"
PROMPTS = ROOT / "ai" / "prompts" / "v1"
REPORTS = ROOT / "docs" / "evaluation" / "reports"
REVISIONS = ROOT / "docs" / "evaluation" / "prompt-revisions"
POLL_S = 0.25
CLIENT_CAP_S = 2 * 300 + 30  # a review not terminal by then is a hang (P6)
WARM_UP = "def add(a, b):\n    return a + b\n"
TERMINAL = ("COMPLETED", "PARTIAL", "FAILED")


def ollama(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    with httpx.Client(base_url=OLLAMA, timeout=600) as client:
        response = client.request(method, path, json=body)
        response.raise_for_status()
        return response.json()


def git(*argv: str) -> str:
    return check_environment.run("git", "-C", str(ROOT), *argv) or ""


# --- preconditions (§20.7 step 1) ---------------------------------------------------------


def verify_environment() -> dict[str, Any]:
    """The running environment must match the environment record, hardware included."""
    record = check_environment.load_record()
    if record is None:
        raise EvaluationError("docs/evaluation/environment-record.json is missing")
    installed = check_environment.installed_versions()
    checks = check_environment.evaluate(check_environment.pinned_versions(), installed, record)
    bad = [f"{c.name} {c.expected}→{c.actual}" for c in checks if c.status != "ok"]
    if bad:
        raise EvaluationError(f"the environment does not match its record: {bad}")
    if sys.platform == "win32":
        machine = check_environment.windows_hardware()
        if machine["hardware"] != record["hardware"] or machine["os"] != record["os"]:
            raise EvaluationError("the hardware or OS differs from the environment record")
    return record


def verify_commit() -> str:
    """The backend commit identifies the code: only evaluation outputs may be uncommitted."""
    changed = [
        line[3:]
        for line in git("status", "--porcelain", "--untracked-files=all").splitlines()
        if not line[3:].startswith("docs/evaluation/")
    ]
    if changed:
        raise EvaluationError(f"uncommitted changes outside docs/evaluation/: {changed}")
    return git("rev-parse", "HEAD")


def prompt_identity() -> tuple[str, str]:
    """(manifest identifier, status) after checking every prompt file against its MANIFEST."""
    manifest = (PROMPTS / "MANIFEST").read_bytes().decode("utf-8")
    lines = manifest.splitlines()
    listed = {name: digest for digest, name in (line.split("  ") for line in lines[1:])}
    actual = {name: sha256((PROMPTS / name).read_bytes()) for name in PROMPT_FILES}
    if listed != actual:
        raise EvaluationError("the prompt files do not match their MANIFEST")
    return manifest_identifier(manifest), lines[0].removeprefix("status: ")


def model_facts(tag: str) -> dict[str, Any]:
    """Tag, digest and details from /api/tags and /api/show (§20.9 Stage A, §20.10)."""
    listed = {m["name"]: m for m in ollama("GET", "/api/tags")["models"]}
    if tag not in listed:
        raise EvaluationError(f"{tag} is not installed (ollama pull {tag})")
    shown = ollama("POST", "/api/show", {"model": tag})
    details, info = shown.get("details", {}), shown.get("model_info", {})
    licence_text = shown.get("license", "") or ""
    apache = "Apache License" in licence_text and "Version 2.0" in licence_text
    context = next((v for k, v in info.items() if k.endswith(".context_length")), None)
    return {
        "model_tag": tag,
        "model_digest": listed[tag]["digest"],
        "download_size_bytes": listed[tag]["size"],
        "family": details.get("family"),
        "parameter_size": details.get("parameter_size"),
        "quantization": details.get("quantization_level"),
        "licence": "Apache-2.0" if apache else (licence_text.strip().splitlines() or ["?"])[0],
        "native_context_length": context,
    }


def unload_all_but(tag: str | None) -> None:
    for loaded in ollama("GET", "/api/ps").get("models", []):
        if loaded["name"] != tag:
            ollama("POST", "/api/generate", {"model": loaded["name"], "keep_alive": 0})


def loaded_size(tag: str) -> tuple[int | None, int | None]:
    for loaded in ollama("GET", "/api/ps").get("models", []):
        if loaded["name"] == tag:
            return loaded.get("size"), loaded.get("size_vram")
    return None, None


# --- one run (§20.7 steps 2-5) ------------------------------------------------------------


class Capture(logging.Handler):
    """The backend's JSON stage records, as written, kept in memory."""

    def __init__(self) -> None:
        from backend.logging_setup import JsonFormatter

        super().__init__(logging.INFO)
        self.setFormatter(JsonFormatter())
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("backend."):
            self.records.append(json.loads(self.format(record)))


def settings_for(tag: str, seed: int) -> Any:
    from backend.config import AIProviderName, AppEnv, Settings

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        app_env=AppEnv.DEVELOPMENT,  # the v1 prompts are a draft until the freeze (§10.1)
        ai_provider=AIProviderName.OLLAMA,
        ollama_model=tag,
        ollama_seed=seed,
        review_max_concurrent=1,
    )
    actual = {
        "temperature": settings.ollama_temperature,
        "num_ctx": settings.ollama_num_ctx,
        "num_predict": settings.ollama_num_predict,
        "think": False,  # always sent by the adapter (D-77)
        "retry_count": settings.ai_output_retry_count,
        "review_timeout_s": settings.review_timeout_seconds,
        "ollama_timeout_s": settings.ollama_timeout_seconds,
        "max_source_bytes": settings.max_source_bytes,
        "max_source_lines": settings.max_source_lines,
    }
    if actual != CONTROLLED_CONFIG:
        raise EvaluationError(f"the configuration differs from §20.7: {actual}")
    return settings


def observe(case: Case, resource: dict[str, Any] | None, records: list[dict[str, Any]],
            duration_ms: int, memory: tuple[int | None, int | None]) -> CaseResult:  # fmt: skip
    """One case's result from its final API resource and its backend stage records."""
    if resource is None or resource["status"] not in TERMINAL:
        return CaseResult(case.case_id, case.kind, case.size_class, "HUNG", duration_ms,
                          ("UNKNOWN", None), ("UNKNOWN", None), None, None)  # fmt: skip
    review_id = resource["review_id"]
    mine = [r for r in records if r.get("review_id") == review_id]
    calls = tuple(
        Call(
            operation="review" if r["stage"] == "AI_ANALYSIS" else "improve",
            **{
                k: r.get(k)
                for k in (
                    "attempt",
                    "prompt_eval_count",
                    "eval_count",
                    "total_duration",
                    "input_token_estimate",
                    "token_estimate_exceeded",
                    "thinking_emitted",
                )
            },
        )  # fmt: skip
        for r in mine
        if r["event"] == "review.stage.finished"
        and r.get("stage") in ("AI_ANALYSIS", "IMPROVEMENT")
        and r.get("status") != "SKIPPED"
    )
    result = resource.get("result")
    codes: list[str] = []
    if resource.get("failure"):
        codes.append(resource["failure"]["code"])
    if result is None:
        failure = resource["failure"]["code"]
        return CaseResult(case.case_id, case.kind, case.size_class, resource["status"],
                          duration_ms, ("FAILED", failure), ("SKIPPED", None), None, None,
                          (), tuple(codes), calls, *memory)  # fmt: skip
    analysis = result["analysis"]
    outcomes = [analysis["ai_analysis"], analysis["improvement"], analysis["static_analysis"]]
    outcomes += [t["outcome"] for t in analysis["static_tools"]]
    codes += [o["error_code"] for o in outcomes if o.get("error_code")]
    issues = tuple(
        Issue(
            category=i["category"],
            severity=i["severity"],
            start_line=(i["location"] or {}).get("start_line"),
            end_line=(i["location"] or {}).get("end_line"),
            complete=all(i[k].strip() for k in ("summary", "impact", "recommendation")),
        )
        for i in result["issues"]
    )
    ai, improvement = analysis["ai_analysis"], analysis["improvement"]
    return CaseResult(
        case.case_id, case.kind, case.size_class, resource["status"], duration_ms,
        (ai["status"], ai["error_code"]), (improvement["status"], improvement["error_code"]),
        result["improved_code"]["status"], result["score"]["overall"], issues,
        tuple(sorted(set(codes))), calls, *memory,
    )  # fmt: skip


async def review(client: httpx.AsyncClient, source: str, key: str) -> tuple[Any, int]:
    """POST one review, then poll every 250 ms until terminal; (resource, duration_ms)."""
    started = time.monotonic()
    created = await client.post(
        "/api/v1/reviews",
        json={"language": "python", "source_code": source},
        headers={"Idempotency-Key": key},
    )
    if created.status_code not in (200, 202):
        raise EvaluationError(f"the API rejected a submission: {created.status_code}")
    location, resource = created.headers["Location"], created.json()
    while resource["status"] not in TERMINAL and time.monotonic() - started < CLIENT_CAP_S:
        await asyncio.sleep(POLL_S)
        resource = (await client.get(location)).json()
    return resource, int((time.monotonic() - started) * 1000)


async def execute(tag: str, seed: int, cases: tuple[Case, ...], run: str) -> list[CaseResult]:
    from backend.composition import build_context
    from backend.main import create_app

    settings = settings_for(tag, seed)
    context = build_context(settings)
    app = create_app(settings, context)
    capture = Capture()
    logging.getLogger().addHandler(capture)
    results = []
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=60
            ) as client,
        ):
            await review(client, WARM_UP, f"eval-{run}-warm-up"[:128])  # discarded (step 2)
            for n, case in enumerate(cases, start=1):
                resource, ms = await review(
                    client, case.source, f"eval-{sha256(run.encode())[:24]}-{n:03d}"
                )
                result = observe(case, resource, capture.records, ms, loaded_size(tag))
                results.append(result)
                print(f"  {n:2}/{len(cases)} {case.case_id:38} {result.status:9} {ms:>7} ms "
                      f"{','.join(result.error_codes) or '-'}", flush=True)  # fmt: skip
    finally:
        logging.getLogger().removeHandler(capture)
    return results


def command_run(args: argparse.Namespace) -> int:
    check_purpose(args.purpose, args.dataset)
    record = verify_environment()
    commit = verify_commit()
    dataset_hash = verify_dataset(args.dataset)
    cases = load_cases(args.dataset)
    identifier, status = prompt_identity()
    if args.purpose in ("final-gate", "refinement") and not args.revision:
        raise EvaluationError(f"a {args.purpose} run names its prompt revision")
    revision = args.revision or "v1-r0"
    facts = model_facts(args.candidate)
    run = run_id(args.candidate, args.dataset, identifier, args.seed)
    target = REPORTS / f"{run}.json"
    if target.exists():
        raise EvaluationError(f"{target.name} exists: runs are never overwritten")
    ledger_path = REPORTS / f"final-gate-round-{args.round}.json"
    ledger: dict[str, Any] = {"round": args.round}
    if args.purpose == "final-gate":
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text("utf-8"))
        begin_final_gate(ledger, revision, identifier)
    print(f"run {run} ({args.purpose}, {len(cases)} cases)", flush=True)
    unload_all_but(None)  # step 2: nothing else loaded; the warm-up loads the candidate
    started_at = datetime.now(UTC).isoformat()
    results = asyncio.run(execute(args.candidate, args.seed, cases, run))
    identity = {
        "run_id": run,
        "purpose": args.purpose,
        "round": args.round,
        "candidate": args.candidate,
        **{
            k: facts[k]
            for k in ("model_tag", "model_digest", "parameter_size", "quantization", "licence")
        },  # fmt: skip
        "ollama_version": record["versions"]["ollama"],
        "dataset_version": args.dataset,
        "dataset_manifest_sha256": dataset_hash,
        "prompt_version": "v1",
        "prompt_revision": revision,
        "prompt_manifest_sha256": identifier,
        "prompt_status": status,
        "seed": args.seed,
        "git_commit": commit,
        "environment": {k: record[k] for k in ("hardware", "os", "versions")},
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    report = build_report(identity, cases, results)
    REPORTS.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    target.with_suffix(".md").write_text(report_markdown(report), encoding="utf-8")
    if args.purpose == "final-gate":
        complete_final_gate(ledger, identifier, args.seed, report["verdict"])
        ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"verdict {report['verdict']} {report['failed_gates']} → {target.relative_to(ROOT)}")
    return 0


# --- Stage A (§20.9) ----------------------------------------------------------------------


def command_eligibility(args: argparse.Namespace) -> int:
    from ai.ollama.provider import OllamaAIReviewProvider, OllamaOptions
    from ai.prompts.renderer import load_prompts
    from ai.schemas import AIReviewOutput
    from shared.domain.enums import Language
    from shared.domain.interfaces import AIReviewRequest
    from shared.domain.models import SourceText

    verify_environment()
    facts = model_facts(args.candidate)
    case = next(c for c in load_cases("dev-v1") if c.case_id == args.smoke_case)
    settings = settings_for(args.candidate, 42)
    unload_all_but(None)

    class Deadline:
        def __init__(self) -> None:
            self.end = time.monotonic() + float(settings.review_timeout_seconds)

        def remaining(self) -> float:
            return self.end - time.monotonic()

    async def smoke() -> str:
        async with httpx.AsyncClient(base_url=OLLAMA) as client:
            provider = OllamaAIReviewProvider(
                client,
                OllamaOptions(
                    model=args.candidate,
                    num_ctx=settings.ollama_num_ctx,
                    num_predict=settings.ollama_num_predict,
                    timeout_s=settings.ollama_timeout_seconds,
                    retry_count=0,
                ),  # fmt: skip
                load_prompts("v1"),
                {Language.PYTHON: "Python"},
            )
            request = AIReviewRequest(
                language=Language.PYTHON, source=SourceText.of(case.source), static_findings=()
            )
            try:
                await provider.review(request, Deadline())
            except Exception as error:  # recorded as the smoke result, never hidden
                return type(error).__name__
            return "PASS"

    smoke_result = asyncio.run(smoke())
    has_refs = "$defs" in json.dumps(AIReviewOutput.model_json_schema())
    criteria = {
        "1_runs_locally": {
            "passed": True, "tag": facts["model_tag"], "digest": facts["model_digest"]
        },
        "2_appropriate": {"passed": "instruct" in args.candidate, "family": facts["family"]},
        "3_download_size_le_5_5_gb": {
            "passed": facts["download_size_bytes"] <= 5.5e9, "bytes": facts["download_size_bytes"]
        },
        "4_structured_output": {
            "passed": smoke_result == "PASS"
            and (facts["native_context_length"] or 0) >= 16_384,
            "smoke_case": case.case_id,
            "smoke_result": smoke_result,
            "native_context_length": facts["native_context_length"],
            "ref_check": {"schema_has_defs": has_refs, "passed": smoke_result == "PASS"},
        },
        "5_licence": {"passed": facts["licence"] == "Apache-2.0", "licence": facts["licence"]},
    }  # fmt: skip
    eligible = all(c["passed"] for c in criteria.values())
    record = {
        "candidate": args.candidate,
        **facts,
        "criteria": criteria,
        "eligible": eligible,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    target = REPORTS / f"eligibility-{sanitized(args.candidate)}.json"
    target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0 if eligible else 1


# --- Stages C-E (§20.9) -------------------------------------------------------------------


def command_compare(args: argparse.Namespace) -> int:
    identifier, _ = prompt_identity()
    dataset_hash = verify_dataset("v1")
    eligibility = {
        record["candidate"]: bool(record["eligible"])
        for record in (json.loads(p.read_text("utf-8")) for p in REPORTS.glob("eligibility-*.json"))
    }
    reports = [
        report
        for report in (json.loads(p.read_text("utf-8")) for p in REPORTS.glob("*__ds-v1__*.json"))
        if report["round"] == args.round
        and report["purpose"] in ("primary", "stability")
        and report["prompt_manifest_sha256"] == identifier
        and report["dataset_manifest_sha256"] == dataset_hash
    ]
    primary = {r["candidate"]: r for r in reports if r["purpose"] == "primary" and r["seed"] == 42}
    missing = [tag for tag, ok in eligibility.items() if ok and tag not in primary]
    if missing:
        raise EvaluationError(f"Stage B is incomplete: no primary run for {missing}")
    candidates = [
        Candidate(
            tag=tag,
            parameters_b=parameters_b(report["parameter_size"]),
            verdict=report["verdict"],
            failed_gates=tuple(report["failed_gates"]),
            agg=report["overall_pass_rate"]["value"] or 0.0,
            p95_ms=report["p95_latency_ms"],
            median_ms=report["median_latency_ms"],
            peak_memory_bytes=report["peak_model_memory_bytes"] or 0,
        )
        for tag, report in primary.items()
        if eligibility.get(tag)
    ]
    stability: dict[str, dict[int, str]] = {}
    for report in reports:
        stability.setdefault(report["candidate"], {})[report["seed"]] = report["verdict"]
    outcome = select(candidates, stability)
    target = REPORTS / f"comparison-round-{args.round}.md"
    target.write_text(
        comparison_markdown(args.round, eligibility, primary, outcome), encoding="utf-8"
    )
    target.with_suffix(".json").write_text(
        json.dumps(asdict(outcome), indent=2) + "\n", encoding="utf-8"
    )
    print(target.read_text("utf-8"))
    return 0


# --- prompt revisions (D-95) --------------------------------------------------------------


def command_record_revision(args: argparse.Namespace) -> int:
    identifier, _ = prompt_identity()
    report = json.loads((REPORTS / f"{args.dev_report}.json").read_text("utf-8"))
    if report["dataset_version"] != "dev-v1" or report["prompt_manifest_sha256"] != identifier:
        raise EvaluationError("the development-set report does not match this prompt revision")
    details = {
        "reason": args.reason,
        "development_set_report": args.dev_report,
        "development_set_verdict": report["verdict"],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    print(record_revision(args.revision, PROMPTS, REVISIONS, details))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="one evaluation run")
    run.add_argument("--candidate", required=True)
    run.add_argument("--seed", type=int, required=True)
    run.add_argument("--dataset", choices=("v1", "dev-v1"), default="v1")
    run.add_argument(
        "--purpose", choices=("primary", "stability", "final-gate", "refinement"), default="primary"
    )
    run.add_argument("--revision")
    run.add_argument("--round", type=int, default=1)
    compare = commands.add_parser("compare", help="Stages C-E")
    compare.add_argument("--round", type=int, default=1)
    eligibility = commands.add_parser("eligibility", help="Stage A")
    eligibility.add_argument("--candidate", required=True)
    eligibility.add_argument("--smoke-case", default="dev_security_hardcoded_password")
    revision = commands.add_parser("record-revision", help="record a prompt revision (D-95)")
    revision.add_argument("--revision", required=True)
    revision.add_argument("--dev-report", required=True)
    revision.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    handler = {
        "run": command_run,
        "eligibility": command_eligibility,
        "compare": command_compare,
        "record-revision": command_record_revision,
    }[args.command]
    try:
        return handler(args)
    except EvaluationError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
