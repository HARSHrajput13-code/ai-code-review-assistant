# Model-selection record

```text
Selection outcome:                 MODEL_SELECTION_FAILED
Selected model:                    (none)
Selected quantization:             (none)
Reason (deciding Stage D step):    (none: no candidate passed the mandatory gates)
Licence (+ restrictions):          (none selected)
Evaluation dataset version:        v1 (DATASET_MANIFEST c06926606e80eb44c5bb883c0b965af592a5201f7e536f07645c2b461fcf4a2b)
Prompt version:                    v1, revision v1-r0 (manifest identifier 06e536ade58f511848741b0a274365583e66cc3f7ac39ce88b76f0525c6a3ff8, status draft; not frozen)
Ollama version:                    0.40.2
Hardware (reference environment):  {"cpu": "AMD Ryzen 7 7435HS", "ram_bytes": 16989736960, "gpus": [{"name": "NVIDIA GeForce RTX 3050 Laptop GPU", "vram_mib": 4096}]}
Prompt refinement history (D-95):  none (refinement starts only after a selection)
Final candidate designation:       none
Final-gate attempts (1-3):         none
```

## Candidates

### `qwen2.5-coder:7b-instruct-q4_K_M`

- **Stage A:** eligible (digest `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364`, 7.6B, Q4_K_M, licence Apache-2.0)
- **Primary run (seed 42):** `qwen2.5-coder-7b-instruct-q4_K_M__ds-v1__prompt-06e536ad__seed-42`, verdict **FAIL**
- **Critical quality gates:** C1 94.3% FAIL · C2 100.0% PASS · C3 33.3% FAIL · C4 50.0% FAIL · AGG 83.5% PASS
- **Informational:** N1 87.5% PASS · N2 87.5% PASS · N3 100.0% PASS · N4 100.0% PASS
- **Compatibility and resources:** T0 0 PASS · P1 0.143 FAIL · P2 180727 PASS · P3 1 FAIL · P4 0 PASS · P5 5813221456 PASS · P6 0 PASS
- **Latency:** median 49848 ms · p95 180727 ms · max 180906 ms · timeout rate 14.3% (3 cases)
- **Resources:** peak model memory 5813221456 bytes, VRAM share 41%; max token-estimate ratio 0.858
- **Stability:** not run (no candidate passed Stage C)
- **Reason it failed:** Stage C: failed C1, C3, C4, P1, P3

### `qwen3:4b-instruct-2507-q4_K_M`

- **Stage A:** eligible (digest `0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0`, 4.0B, Q4_K_M, licence Apache-2.0)
- **Primary run (seed 42):** `qwen3-4b-instruct-2507-q4_K_M__ds-v1__prompt-06e536ad__seed-42`, verdict **FAIL**
- **Critical quality gates:** C1 100.0% PASS · C2 100.0% PASS · C3 33.3% FAIL · C4 35.7% FAIL · AGG 83.1% PASS
- **Informational:** N1 87.5% PASS · N2 87.5% PASS · N3 100.0% PASS · N4 100.0% PASS
- **Compatibility and resources:** T0 0 PASS · P1 0.095 FAIL · P2 193693 PASS · P3 0 PASS · P4 0 PASS · P5 5404801103 PASS · P6 0 PASS
- **Latency:** median 66143 ms · p95 193693 ms · max 300053 ms · timeout rate 9.5% (2 cases)
- **Resources:** peak model memory 5404801103 bytes, VRAM share 42%; max token-estimate ratio 0.957
- **Stability:** not run (no candidate passed Stage C)
- **Reason it failed:** Stage C: failed C3, C4, P1

## Rejected candidates and reasons

- `qwen2.5-coder:7b-instruct-q4_K_M`: Stage C: failed C1, C3, C4, P1, P3
- `qwen3:4b-instruct-2507-q4_K_M`: Stage C: failed C3, C4, P1

No candidate is chosen as the best of the failed ones (D-93). The gates, the dataset and the protocol are unchanged. M2 halts until an approved CIS amendment selects one of the §20.9 Stage E next steps.

## Observations (informational, not gates)

These facts explain the failures. None of them changes a gate, a threshold or the outcome.

- **Improved-code rejections (§14.3), from the backend's stage-11 records:**
  - `qwen2.5-coder:7b-instruct-q4_K_M`: 5 `DOES_NOT_PARSE`, 2 `INTERFACE_CHANGED`;
  - `qwen3:4b-instruct-2507-q4_K_M`: 6 `DOES_NOT_PARSE`, 2 `INTERFACE_CHANGED`.
- **Cause of `DOES_NOT_PARSE` (diagnosed on the development set only, after both primary runs, with `qwen3:4b-instruct-2507-q4_K_M` and prompt revision v1-r0 unchanged):**
  - In the two rejected development-set candidates, `improved_code` contained literal `\n` escape sequences and no newline. The model had escaped the code a second time inside the JSON string.
  - The four accepted candidates used real newlines.
  - §14.3 allows fence stripping as the only rewriting, and `improved_code` is validated verbatim (§11), so rejecting such candidates is the specified behaviour. It is not an implementation defect.
  - The schema itself was satisfied: C1 is 100 % for the 4B candidate.
  - Whether a common prompt change would avoid this is not known. It would have to be tested as a new comparison round (Stage E (b)), because the comparison snapshot never changes within a round (D-84, D-95).
- **Timeouts (P1):** both candidates timed out on `size_medium` (about 5 KB), where no timeout is allowed.
  - The 7B timed out at the 180 s attempt limit (`OLLAMA_TIMEOUT_SECONDS`), and also on `good_inventory` and `size_near_limit`.
  - The 4B reached the 300 s review deadline (`REVIEW_TIMEOUT_SECONDS`), and also timed out on `size_near_limit`.
  - P1 therefore fails for both candidates at the frozen V1 configuration, independently of the improvement results.
- **Availability (P3), 7B:** Ollama answered one `POST /api/chat` with HTTP 500 after 38 s, mid-generation at about 7.6 tokens/s (`logic_off_by_one`). The service stayed up, and no Windows resource-exhaustion event was logged.
- **Resources (`GET /api/ps`):** the 7B peaked at 5,813,221,456 bytes (5.41 GiB), 41 % on the GPU; the 4B peaked at 5,404,801,103 bytes (5.03 GiB), 42 % on the GPU. Both pass P5 (≤ 6.0 GiB). With 4 GB of VRAM, most of each model ran on the CPU.
- **Token estimator (A17):** the maximum `prompt_eval_count / est(input)` ratios were 0.858 (7B) and 0.957 (4B), both ≤ 1.0. `BYTES_PER_TOKEN_ESTIMATE` stayed conservative, so no A17 amendment is needed.
- **`$ref` compatibility (§9.2):** both candidates returned schema-valid output for the generated schema with its `$defs` (Stage A), so `inline_local_refs` is not needed. `maxLength` enforcement was not separately observed.
- **Run conditions:**
  - free RAM at the start was 8.33 GB (7B run) and 8.80 GB (4B run), with heavy applications closed;
  - mains power for both;
  - both runs used the same backend commit `928e00a` and the same dataset and prompt manifests.
- **Tooling:** both runs exited with status 1 after their reports had been written. The final console line contained a character the Windows console codepage could not encode. The reports are complete, and the defect was fixed after the round.
- **Stage E next steps:** these need an approved CIS amendment with a new decision ID.
  - The pre-documented `qwen2.5-coder:7b-instruct-q5_K_M` fallback is **not** available under §20.9. It requires that the 7B Q4_K_M candidate "failed quality gates while passing T0 and P1–P6", and the 7B failed P1 and P3.
  - The remaining options are (a) another candidate that satisfies Stage A, or (b) an approved evaluation or configuration change (for example, a common prompt revision on the development set, or a configuration baseline change with the startup validation and boundary cases re-run), followed by a full new round for every candidate.
