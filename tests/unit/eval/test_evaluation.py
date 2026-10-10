"""Evaluation tooling (CIS §20.3 "Evaluation tooling", §20.6-§20.10, D-93 to D-95).

Fixture run data only: no model, no Ollama, no network.
"""

import itertools
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from scripts import evaluation as ev
from scripts.evaluation import Call, Candidate, Case, CaseResult, EvaluationError, Issue

OK = ("SUCCEEDED", None)


def case(case_id: str, kind: str, planted: tuple[int, ...] = (5,), **expect: Any) -> Case:
    if kind in ("good", "size"):
        details: dict[str, Any] = {"expected_min_score": 75} if kind == "good" else {}
    else:
        details = {"expected_categories": ["SECURITY"], "planted_lines": list(planted)}
    return Case(case_id, kind, "x = 1\n", {"kind": kind, **details, **expect}, kind != "syntax")


def result(c: Case, **fields: Any) -> CaseResult:
    security = Issue("SECURITY", "HIGH", 5, 5, True)
    values: dict[str, Any] = {
        "case_id": c.case_id,
        "kind": c.kind,
        "size_class": c.size_class,
        "status": "COMPLETED",
        "duration_ms": 1000,
        "review": OK,
        "improvement": OK,
        "improved_code_status": "AVAILABLE",
        "score": 80,
        "issues": () if c.kind in ("good", "size") else (security,),
        "calls": (Call("review", 1, 900, 100, 1, 1000, False, 0), Call("improve", 1, 800)),
        "model_size_bytes": 5 * 2**30,
        "model_vram_bytes": 3 * 2**30,
    }
    return CaseResult(**(values | fields))


DATASET = (
    case("injection_a", "injection"),
    case("good_a", "good"),
    case("logic_a", "logic"),
    case("security_a", "security"),
    case("syntax_a", "syntax"),
    case("size_small", "size", size_class="small"),
)


def gates(results: list[CaseResult]) -> dict[str, Any]:
    return ev.compute_gates(DATASET, results)


def passing() -> list[CaseResult]:
    return [result(c) for c in DATASET]


def swap(results: list[CaseResult], case_id: str, **fields: Any) -> list[CaseResult]:
    return [replace(r, **fields) if r.case_id == case_id else r for r in results]


# --- gates (§20.8) ------------------------------------------------------------------------


def test_a_clean_run_passes_every_gate() -> None:
    computed = gates(passing())
    assert computed["verdict"] == "PASS" and computed["failed_gates"] == []
    assert all(g["passed"] for g in computed["gates"].values())
    assert computed["gates"]["AGG"]["value"] == 1.0


def test_nearest_rank_percentiles() -> None:
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert ev.nearest_rank(values, 95) == 100
    assert ev.nearest_rank(values, 50) == 50
    assert ev.nearest_rank([7], 95) == 7
    assert ev.nearest_rank(list(range(1, 21)), 95) == 19


@pytest.mark.parametrize(
    ("changes", "failed"),
    [
        ({"case_id": "security_a", "issues": ()}, {"C2"}),
        ({"case_id": "security_a", "issues": (Issue("SECURITY", "HIGH", 8, 8, True),)}, {"C2"}),
        ({"case_id": "injection_a", "improvement": ("FAILED", "IMPROVED_CODE_INVALID")}, {"C3"}),
        ({"case_id": "logic_a", "improved_code_status": "UNAVAILABLE"}, {"C4"}),
        ({"case_id": "logic_a", "review": ("FAILED", "AI_OUTPUT_INVALID")}, {"C1"}),
    ],
    ids=["undetected", "outside-tolerance", "injected-invalid-improvement", "c4", "c1"],
)
def test_each_critical_property_fails_its_gate(changes: dict[str, Any], failed: set[str]) -> None:
    case_id = changes.pop("case_id")
    computed = gates(swap(passing(), case_id, **changes))
    assert failed <= set(computed["failed_gates"])
    assert computed["verdict"] == "FAIL"


def test_c2_accepts_an_issue_within_two_lines_and_uses_the_location_span() -> None:
    near = (Issue("SECURITY", "HIGH", 7, 7, True),)  # planted line 5, distance 2
    spanning = (Issue("SECURITY", "HIGH", 1, 3, True),)  # end_line 3, distance 2
    assert gates(swap(passing(), "security_a", issues=near))["gates"]["C2"]["passed"]
    assert gates(swap(passing(), "security_a", issues=spanning))["gates"]["C2"]["passed"]
    no_location = (Issue("SECURITY", "HIGH", None, None, True),)
    assert not gates(swap(passing(), "security_a", issues=no_location))["gates"]["C2"]["passed"]


def test_c1_counts_only_operations_that_ended_with_a_model_response() -> None:
    timed_out = swap(passing(), "logic_a", review=("FAILED", "REVIEW_TIMEOUT"))
    rejected = swap(passing(), "logic_a", improvement=("FAILED", "IMPROVED_CODE_INVALID"))
    assert gates(timed_out)["gates"]["C1"]["value"] == 1.0  # judged by P1, not C1
    assert gates(rejected)["gates"]["C1"]["value"] == 1.0  # schema-valid, rejected by §14.3
    invalid = gates(swap(passing(), "size_small", improvement=("FAILED", "AI_OUTPUT_INVALID")))
    assert invalid["gates"]["C1"]["value"] == pytest.approx(11 / 12)  # size cases count for C1


def test_c4_skips_cases_whose_original_does_not_parse() -> None:
    computed = gates(swap(passing(), "syntax_a", improved_code_status="UNAVAILABLE"))
    assert computed["gates"]["C4"]["passed"]
    assert "C4" not in computed["properties"]["syntax_a"]


def test_n_properties_are_reported_and_only_count_through_agg() -> None:
    wrong = (Issue("CORRECTNESS", "LOW", 40, 40, True),)  # wrong category and far away
    results = swap(passing(), "logic_a", issues=wrong)
    results = swap(results, "syntax_a", issues=wrong)  # N1 and N2: 2 of 4 flawed cases
    results = swap(results, "good_a", score=60)
    computed = gates(results)
    for name in ("N1", "N2", "N3"):
        assert not computed["gates"][name]["passed"]
        assert name not in computed["failed_gates"]
    assert computed["gates"]["AGG"]["value"] < 1.0


def test_agg_is_the_pass_rate_over_every_property_check() -> None:
    computed = gates(swap(passing(), "good_a", score=10))
    checks = [v for p in computed["properties"].values() for v in p.values()]
    assert computed["gates"]["AGG"]["value"] == pytest.approx(sum(checks) / len(checks))
    assert computed["properties"]["size_small"] == {}  # size cases have no property checks


def test_n4_requires_complete_explanations() -> None:
    incomplete = (Issue("SECURITY", "HIGH", 5, 5, False),)
    computed = gates(swap(passing(), "security_a", issues=incomplete))
    assert computed["gates"]["N4"]["value"] < 1.0 and not computed["gates"]["N4"]["passed"]


def test_t0_fails_on_any_thinking_output() -> None:
    thinking = (Call("review", 1, 900, 100, 1, 1000, False, 1),)
    assert "T0" in gates(swap(passing(), "good_a", calls=thinking))["failed_gates"]


def test_p1_allows_five_percent_but_no_small_or_medium_timeout() -> None:
    dataset = DATASET + tuple(case(f"good_{n}", "good") for n in range(15))  # 21 cases
    results = [result(c) for c in dataset]
    late = [replace(r, error_codes=("REVIEW_TIMEOUT",)) if r.case_id == "logic_a" else r
            for r in results]  # fmt: skip
    assert ev.compute_gates(dataset, late)["gates"]["P1"]["passed"]  # 1/21 ≤ 5 %
    small = [replace(r, error_codes=("REVIEW_TIMEOUT",)) if r.case_id == "size_small" else r
             for r in results]  # fmt: skip
    assert not ev.compute_gates(dataset, small)["gates"]["P1"]["passed"]


@pytest.mark.parametrize(
    ("changes", "gate"),
    [
        ({"duration_ms": 240_001}, "P2"),
        ({"error_codes": ("AI_MODEL_UNAVAILABLE",)}, "P3"),
        ({"error_codes": ("AI_CONTEXT_EXCEEDED",)}, "P4"),
        ({"model_size_bytes": 6 * 2**30 + 1}, "P5"),
        ({"status": "HUNG"}, "P6"),
    ],
)
def test_each_performance_gate(changes: dict[str, Any], gate: str) -> None:
    results = [replace(r, **changes) for r in passing()]
    assert gate in gates(results)["failed_gates"]
    assert gate not in gates(passing())["failed_gates"]


def test_the_measurements_are_recorded() -> None:
    results = swap(passing(), "logic_a", duration_ms=9000)
    measured = gates(results)["measurements"]
    assert (measured["max_latency_ms"], measured["median_latency_ms"]) == (9000, 1000)
    assert measured["max_token_estimate_ratio"] == pytest.approx(0.9)
    assert measured["peak_model_memory_bytes"] == 5 * 2**30
    assert measured["peak_vram_bytes"] == 3 * 2**30


def test_results_must_cover_exactly_the_dataset() -> None:
    with pytest.raises(EvaluationError):
        gates(passing()[:-1])


def test_the_report_holds_every_field() -> None:
    identity = {name: "x" for name in ev.REPORT_FIELDS[:12]} | {
        "run_id": "r", "dataset_manifest_sha256": "d", "git_commit": "c", "purpose": "primary",
    }  # fmt: skip
    report = ev.build_report(identity, DATASET, passing())
    assert set(ev.REPORT_FIELDS) <= set(report)
    assert report["config"] == ev.CONTROLLED_CONFIG
    assert report["per_case"][0]["properties"] and report["counting_rules"]
    assert "**PASS**" in ev.report_markdown(report)
    json.dumps(report)  # serialisable


# --- selection (§20.9, D-93, D-94) --------------------------------------------------------


def cand(tag: str, size: float, **fields: Any) -> Candidate:
    values: dict[str, Any] = {
        "verdict": "PASS", "failed_gates": (), "agg": 0.9, "p95_ms": 100_000,
        "median_ms": 60_000, "peak_memory_bytes": 4 * 2**30,
    }  # fmt: skip
    return Candidate(tag, size, **(values | fields))


@pytest.mark.parametrize(
    ("pool", "winner"),
    [
        ([cand("small:4b", 4.02), cand("big:7b", 7.62, agg=0.99)], "small:4b"),
        ([cand("a", 4.0, agg=0.85), cand("b", 4.4, agg=0.95)], "b"),
        ([cand("a", 4.0, agg=0.90, p95_ms=150_000), cand("b", 4.4, agg=0.91)], "b"),
        ([cand("a", 4.0, p95_ms=100_000), cand("b", 4.0, p95_ms=105_000, median_ms=40_000)], "b"),
        ([cand("a", 4.0, peak_memory_bytes=5 * 2**30), cand("b", 4.0)], "b"),
        ([cand("zeta", 4.0), cand("alpha", 4.0)], "alpha"),
    ],
    ids=["smallest", "equivalent-size-quality", "latency", "median", "memory", "lexical"],
)
def test_stage_d_is_deterministic_for_every_permutation(pool: list[Candidate], winner: str) -> None:
    for order in itertools.permutations(pool):
        assert ev.rank(order)[0].tag == winner


def stable(*tags: str) -> dict[str, dict[int, str]]:
    return {tag: {42: "PASS", 43: "PASS", 44: "PASS"} for tag in tags}


def test_the_stable_top_ranked_candidate_is_selected() -> None:
    pool = [cand("big:7b", 7.62), cand("small:4b", 4.02)]
    outcome = ev.select(pool, stable("small:4b", "big:7b"))
    assert (outcome.outcome, outcome.selected) == ("SELECTED", "small:4b")
    assert outcome.rejected["big:7b"].startswith("Stage D: ranked below")


@pytest.mark.parametrize("seed", [43, 44])
def test_stability_falls_through_to_the_next_ranked_candidate(seed: int) -> None:
    pool = [cand("big:7b", 7.62), cand("small:4b", 4.02)]
    stability = stable("small:4b", "big:7b")
    stability["small:4b"][seed] = "FAIL"
    outcome = ev.select(pool, stability)
    assert (outcome.outcome, outcome.selected) == ("SELECTED", "big:7b")
    assert "unstable" in outcome.rejected["small:4b"]


def test_missing_stability_runs_name_the_next_candidate() -> None:
    outcome = ev.select([cand("small:4b", 4.02)], {"small:4b": {42: "PASS"}})
    assert (outcome.outcome, outcome.next_stability_candidate) == ("STABILITY_PENDING", "small:4b")
    assert outcome.selected is None


@pytest.mark.parametrize("gate", ["C2", "AGG"])
def test_one_failing_seed_fails_stability_whatever_the_mean(gate: str) -> None:
    good = passing()
    if gate == "C2":
        bad = swap(good, "security_a", issues=())
    else:
        bad = swap(good, "logic_a", issues=(), improved_code_status="UNAVAILABLE", score=0)
        bad = swap(bad, "good_a", score=0)
        bad = swap(bad, "syntax_a", issues=())
    verdicts = {42: gates(good)["verdict"], 43: gates(good)["verdict"], 44: gates(bad)["verdict"]}
    assert verdicts == {42: "PASS", 43: "PASS", 44: "FAIL"}
    assert gate in gates(bad)["failed_gates"]
    outcome = ev.select([cand("only", 4.0)], {"only": verdicts})
    assert outcome.outcome == "MODEL_SELECTION_FAILED" and outcome.selected is None


def test_no_passing_candidate_is_model_selection_failed() -> None:
    pool = [
        cand("a", 4.0, verdict="FAIL", failed_gates=("P2",)),
        cand("b", 7.0, verdict="FAIL", failed_gates=("C2", "P5")),
    ]
    outcome = ev.select(pool, {})
    assert (outcome.outcome, outcome.selected, outcome.ranking) == (
        "MODEL_SELECTION_FAILED", None, [],
    )  # fmt: skip
    assert outcome.rejected == {"a": "Stage C: failed P2", "b": "Stage C: failed C2, P5"}


def test_every_passing_candidate_unstable_is_model_selection_failed() -> None:
    pool = [cand("a", 4.0), cand("b", 7.0)]
    stability = {
        "a": {42: "PASS", 43: "FAIL", 44: "PASS"},
        "b": {42: "PASS", 43: "PASS", 44: "FAIL"},
    }
    outcome = ev.select(pool, stability)
    assert outcome.outcome == "MODEL_SELECTION_FAILED" and outcome.selected is None
    assert set(outcome.rejected) == {"a", "b"}
    assert "MODEL_SELECTION_FAILED" in ev.comparison_markdown(
        1, {"a": True, "b": True}, {}, outcome
    )


def test_parameter_sizes_are_parsed() -> None:
    assert ev.parameters_b("4.0B") == 4.0
    assert ev.parameters_b("7.6B") == 7.6
    assert ev.parameters_b("494M") == pytest.approx(0.494)
    with pytest.raises(EvaluationError):
        ev.parameters_b("big")


# --- datasets (§20.6, D-72) ---------------------------------------------------------------


def test_both_datasets_match_their_manifests_and_conform() -> None:
    v1, dev = ev.load_cases("v1"), ev.load_cases("dev-v1")
    assert ev.composition_problems(v1) == []
    assert ev.composition_problems(dev, development=True) == []
    assert ev.disjoint("v1", "dev-v1")
    assert all(c.planted_lines for c in v1 if c.flawed)
    assert {c.case_id for c in v1 if not c.parses} == {c.case_id for c in v1 if c.kind == "syntax"}


def test_the_size_cases_meet_their_bounds() -> None:
    sizes = {c.size_class: len(c.source.encode()) for c in ev.load_cases("v1") if c.kind == "size"}
    lines = {c.size_class: c.source.count("\n") for c in ev.load_cases("v1") if c.kind == "size"}
    assert 800 <= sizes["small"] <= 1300 and 4500 <= sizes["medium"] <= 5500
    assert 0.95 * 12_000 <= sizes["near_limit"] <= 12_000 and lines["near_limit"] <= 500


@pytest.mark.parametrize("name", ["v1", "dev-v1"])
def test_a_modified_case_is_detected(tmp_path: Path, name: str) -> None:
    source, _ = ev.DATASETS[name]
    copy = tmp_path / "copy"
    shutil.copytree(source, copy)
    assert ev.verify_dataset(name, copy)
    first = sorted(copy.glob("*.py"))[0]
    first.write_bytes(first.read_bytes() + b"\n")
    with pytest.raises(EvaluationError):
        ev.verify_dataset(name, copy)


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError):
        ev.verify_dataset("v1", tmp_path)


# --- prompt refinement controls (§10.1, D-95) ---------------------------------------------

MANIFEST = "status: draft\naaa  improve_system.md\nbbb  improve_user.md\n"


def prompt_dir(tmp_path: Path, text: str = "v1") -> Path:
    directory = tmp_path / f"prompts-{text}-{len(list(tmp_path.iterdir()))}"
    directory.mkdir()
    for name in ev.PROMPT_FILES:
        (directory / name).write_text(f"{name} {text}", encoding="utf-8")
    hashes = "".join(f"{ev.sha256((directory / n).read_bytes())}  {n}\n" for n in ev.PROMPT_FILES)
    (directory / "MANIFEST").write_text(f"status: draft\n{hashes}", encoding="utf-8")
    return directory


def test_the_identifier_ignores_the_status_line() -> None:
    frozen = MANIFEST.replace("status: draft", "status: frozen")
    assert ev.manifest_identifier(MANIFEST) == ev.manifest_identifier(frozen)
    assert ev.manifest_identifier(MANIFEST) != ev.manifest_identifier(MANIFEST + "ccc  x.md\n")


def test_a_revision_is_immutable(tmp_path: Path) -> None:
    root = tmp_path / "revisions"
    first = ev.record_revision("v1-r0", prompt_dir(tmp_path), root, {"reason": "snapshot"})
    assert ev.record_revision("v1-r0", prompt_dir(tmp_path, "v1"), root, {}) == first  # unchanged
    assert sorted(p.name for p in (root / "v1-r0").iterdir()) == sorted(
        [*ev.PROMPT_FILES, "MANIFEST", "revision.json"]
    )
    with pytest.raises(EvaluationError, match="different content"):
        ev.record_revision("v1-r0", prompt_dir(tmp_path, "v2"), root, {})
    with pytest.raises(EvaluationError):
        ev.record_revision("r0", prompt_dir(tmp_path, "v3"), root, {})


def test_the_final_gate_refuses_a_repeat_and_a_fourth_attempt() -> None:
    ledger: dict[str, Any] = {}
    for n in range(3):
        assert ev.begin_final_gate(ledger, f"v1-r{n}", f"id{n}") == n + 1
        assert ev.begin_final_gate(ledger, f"v1-r{n}", f"id{n}") == n + 1  # the same attempt
        for seed in ev.STABILITY_SEEDS:
            ev.complete_final_gate(ledger, f"id{n}", seed, "FAIL" if seed == 44 else "PASS")
        assert ledger["attempts"][n]["outcome"] == "FAIL"
    with pytest.raises(EvaluationError, match="already evaluated"):
        ev.begin_final_gate(ledger, "v1-r1", "id1")
    with pytest.raises(EvaluationError, match="4th"):
        ev.begin_final_gate(ledger, "v1-r3", "id3")


def test_a_new_revision_waits_for_the_current_attempt() -> None:
    ledger: dict[str, Any] = {}
    ev.begin_final_gate(ledger, "v1-r0", "id0")
    ev.complete_final_gate(ledger, "id0", 42, "PASS")
    with pytest.raises(EvaluationError, match="not complete"):
        ev.begin_final_gate(ledger, "v1-r1", "id1")


@pytest.mark.parametrize(
    ("purpose", "dataset"),
    [
        ("refinement", "v1"),
        ("primary", "dev-v1"),
        ("stability", "dev-v1"),
        ("final-gate", "dev-v1"),
    ],
)
def test_runs_are_refused_on_the_wrong_dataset(purpose: str, dataset: str) -> None:
    with pytest.raises(EvaluationError):
        ev.check_purpose(purpose, dataset)
    ev.check_purpose("refinement", "dev-v1")
    ev.check_purpose(purpose if purpose != "refinement" else "primary", "v1")


def test_the_frozen_manifest_keeps_the_passing_revisions_hash_lines() -> None:
    frozen = ev.frozen_manifest(MANIFEST, MANIFEST)
    assert frozen.startswith("status: frozen\n")
    assert ev.manifest_identifier(frozen) == ev.manifest_identifier(MANIFEST)
    with pytest.raises(EvaluationError):
        ev.frozen_manifest(MANIFEST, MANIFEST + "ccc  x.md\n")


def test_run_ids_follow_the_report_naming() -> None:
    assert ev.run_id("qwen3:4b-instruct-2507-q4_K_M", "v1", "0123456789abcdef", 42) == (
        "qwen3-4b-instruct-2507-q4_K_M__ds-v1__prompt-01234567__seed-42"
    )


# --- the model-selection record (§20.10, D-93) --------------------------------------------


def test_a_failed_selection_record_lists_every_candidate_and_no_model() -> None:
    identity = {name: "x" for name in ev.REPORT_FIELDS[:12]} | {
        "run_id": "r", "dataset_manifest_sha256": "d", "git_commit": "c", "purpose": "primary",
    }  # fmt: skip
    failing = swap(passing(), "security_a", issues=())
    report = ev.build_report(identity, DATASET, failing) | {"candidate": "a:4b"}
    pool = [cand("a:4b", 4.0, verdict="FAIL", failed_gates=tuple(report["failed_gates"]))]
    outcome = ev.select(pool, {})
    eligibility = {"a:4b": {"eligible": True, "model_digest": "abc", "parameter_size": "4.0B"}}
    text = ev.selection_record_markdown(outcome, eligibility, {"a:4b": report}, "A note.")
    assert "Selection outcome:                 MODEL_SELECTION_FAILED" in text
    assert "Selected model:                    (none)" in text
    assert "`a:4b`: Stage C: failed C2" in text and "C2 50.0% FAIL" in text
    assert "## Observations (informational, not gates)" in text and "A note." in text
    selected = ev.SelectionOutcome(outcome="SELECTED", selected="a:4b")
    with pytest.raises(EvaluationError):
        ev.selection_record_markdown(selected, eligibility, {"a:4b": report})
