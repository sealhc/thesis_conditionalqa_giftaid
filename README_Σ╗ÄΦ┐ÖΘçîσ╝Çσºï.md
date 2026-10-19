# Gift Aid dev-38 — Paired Low/High pilot v9.4

## v9.9: JSON Schema (structure-only) for all three arms, four-stage smoke

Decision (2026-10-18): Qwen3-4B-Instruct, Claude and GPT all receive the SAME per-stage JSON Schema (`scripts/stage_schemas.py`), so format enforcement no longer differs between arms.
- Ollama: `format` = schema. Anthropic: `output_config.format` json_schema. OpenAI: `response_format` json_schema, strict. `--format-mode native` (API runner) restores the old OpenAI json_object / Anthropic prompt-only behaviour; Ollama keeps `json|none|schema`. Default everywhere: `schema`.
- The schema fixes STRUCTURE only (field names, types, allowed enum values). It contains no case-specific values, no atom count, no condition IDs: A1 still builds its own structure and can omit, add or mislink. Content errors remain model errors and are never retried.
- Portability: schemas are the intersection of what the three providers accept (every object `additionalProperties:false`, all properties required, no recursion, no minLength, minItems 0/1, root object). `stage_schemas.portable_problems()` checks this and is tested.
- Limitation: the A1 logic expression (NOT / ALL / ANY) cannot be recursive, so it is unrolled to depth 4 (`EXPR_DEPTH`). A deeper expression cannot be expressed; none occurs in the Gift Aid gold.
- Schema SHA-256 hashes are stored in the settings of each run and are part of the smoke approval: a changed schema needs a new smoke.
- Smoke is now a four-stage chain (A1 -> A2 -> A3, plus B1; R0 input, no Gold) for Ollama and both APIs. Approval requires every stage complete and schema-valid on the first attempt. A rejected parameter or schema (e.g. HTTP 400) shows up as `api_error` in `smoke_reports/` and nothing is approved.
- NOT verified live: that OpenAI strict mode and the chosen OpenAI/Claude models accept this schema together with temperature 0.6. Only the API smoke can tell; it has not been run.
- Qwen arm = `qwen3:4b-instruct` (non-thinking). Runs made with `qwen3:4b` or without schema are stale; use new run IDs.

## v9.8.1: failed stages are re-sampled until valid (cap 3, decided 2026-10-18); what is still unfinished is counted as UNFINISHED

Why: A/B accuracy can only be compared in a run where both pipelines produced a final decision. Rule (all runners; `--max-attempts`, default 3, `1` = old behaviour):
- Only a MODEL format failure is retried: invalid JSON, wrong schema (e.g. `missing_facts` written as a string), repetition abort. Only the failed STAGE is repeated; earlier valid stages are kept.
- Same frozen prompt, NO error text fed back, output never edited. New seed each time: attempt k uses base + 1000*(k-1) (4101, 5101, 6101); a given seed reproduces the same text, so retrying with it would be pointless. Seedless providers (Anthropic) simply call again. Retry seeds are derived from the smoke-approved base seed and are not smoke-probed separately.
- Every failed attempt is kept in full under `runs/<id>/<world>/attempts/<stage>_attemptNN/`; `<stage>_stage_attempts.json` lists seed, outcome and reason of each attempt. The first valid output is the one used.
- Technical failures (timeout, truncation, server errors) are still NOT retried here; they need a new run ID as before.
- After the cap the stage stays failed: the path is UNFINISHED (`unfinished_after_retries`; the old `model_format_failures` count is the same number).
Reported per path: `retry_calls` (calls beyond one per stage = pure retry overhead; the base cost of A's three calls versus B's one is NOT retry cost, report the two separately), `first_attempt_rate` (valid at the first attempt of every stage = single-shot reliability), `unfinished_after_retries`, calls and tokens summed over ALL attempts (`usage`), `mean_calls_per_run`.
Thesis caveats: (1) the final decision is conditional on a format-valid output, so accuracy must be read together with first_attempt_rate and attempts; (2) A has three stages and so more chances to fail and retry than B: always report calls/tokens; (3) a stage that failed and was re-sampled is a different sample, not a repaired output.

## v9.7: conditional answers, cost per path (prompts unchanged, no new smoke needed for this change)

Design decision (2026-10-18): a conditional explanation is required only when the model answers NEED_MORE_INFORMATION. For a decided YES/NO the conditions are its decisive conditions,
already judged by `reasoning.decisive_conditions_correct`; forcing "what would change it" branches would invite invented facts. A3/B1 prompts are therefore NOT changed.
Consequence for the thesis: the conditional-answer score exists only for runs where the model itself said NEED_MORE_INFORMATION; report how many runs that is (a model that wrongly decides YES/NO on R3 gets no conditional score).
- Worksheet/judge: new `conditional_answer` block per final answer (`applicable` is frozen = decision is NEED_MORE_INFORMATION). Judged: `branches_match_reference` FULL/PARTIAL/NONE and `asserts_unsupported_condition`. Not applicable -> must stay null. Included in judge-vs-human agreement as two new families.
- Flags (non-blocking, nothing repaired): `NEI_WITHOUT_CONDITIONAL_BRANCHES`, `DECIDED_WITH_CONDITIONAL_BRANCHES`.
- Cost: each path now carries `usage` (calls, prompt/generated tokens as reported by the server for each stage's final attempt, attempts); cohort summaries give mean calls and tokens per run, so A (3 calls) and B (1 call) can be compared on cost. Missing counts are never imputed.
- Repetition aborts get their own line (`repetition_loop_aborts`) with a note that they depend on the decoding configuration, not only on the model.
- Still NOT done: schema decoding is not wired into the runners (waiting for the diagnostic on `qwen3:4b-instruct`); A/B comparison remains confounded by 3 vs 1 calls and Low/High changing length and complexity together.

## v9.6: the Qwen arm is now `qwen3:4b-instruct` (thinking could not be switched off on `qwen3:4b`)

Measured on the user's Mac (Ollama 0.40.6), same prompt "17 times 23", seed 1, num_predict 600:
- `qwen3:4b` (digest 359d7dd4bcda, context 262144, capability thinking default=true): `think=false` leaves the reasoning in `content` (600 tokens used, no answer);
  `/no_think` appended to the prompt is ignored (the model quotes it); `think="low"` is ignored (1307 thinking chars, 600 tokens). Thinking cannot be disabled.
- `qwen3:4b-instruct`: `think=false` and `think=true` both give `eval_count=4`, `thinking_chars=0`: it does not think and ignores the parameter.
  `ollama show` still lists "thinking" under capabilities, so the capability list is NOT evidence; the behavioural test is.
Consequences: (1) default model of all Ollama scripts is now `qwen3:4b-instruct`; every earlier smoke approval and run with `qwen3:4b` is void for this arm (different weights; rerun smoke, new run IDs).
(2) The tag's own defaults are temperature 0.7 / top_p 0.8; this experiment sets temperature 0.6, top_p 0.95, top_k 20 explicitly, so the values are ours, not the model card's; report it.
(3) The model is small and answers directly (it gave 411 for 17x23): expect weak A1/A2/A3 and judge it by the stage evaluation, not by assumption.
(4) Report the model as Qwen3-4B-Instruct (non-thinking), not as "Qwen3-4B with thinking off". The `think` parameter is still sent as false; it has no effect here and is recorded as such.
(5) The earlier A2 loop was observed on the thinking model and may not recur; re-run `ollama_schema_diagnose.py` (modes schema json none) on the new model before deciding about schema decoding.

## v9.5: read-only diagnostic for JSON-Schema constrained decoding (pipeline defaults NOT changed)

`scripts/ollama_schema_diagnose.py` runs the real A1→A2→A3 chain plus B1 for one world under `--modes schema json none` and several seeds,
and writes everything (requests, responses, raw outputs, metrics) to `diagnostics/<timestamp>/` only: no `runs/`, no scoring, no smoke approval, no repair of outputs.
Per call it reports done_reason, tokens, characters, whitespace share, characters before the first `{`, number of complete JSON objects in the output,
thinking-field size, and whether the pipeline's own `parse_json_envelope` + `schema_validate` accept it.
`stage_schemas.py` holds structure-only schemas (identical in content to each prompt's OUTPUT SCHEMA; no atom-ID enums, no atom counts, no policy IDs),
because dropping/renaming atoms and citing non-existent policy IDs are failures this experiment measures.
`ollama_chat` now also accepts `format_mode='schema'`; the main runner/smoke/approval do not use it yet.

    python3 scripts/ollama_schema_diagnose.py --think-check          # is think=false honoured? (seconds)
    python3 scripts/ollama_schema_diagnose.py --world R1_L --seeds 4101 4102 4103 --modes schema json none

Decision rule: integrate schema mode only if schema runs end with done_reason=stop and pass parse+schema_validate for all four stages in all seeds.
If integrated, Claude/GPT arms must either get an equivalent structured-output mode or the format difference is reported as a limitation.

## v9.4: repetition-loop aborts are counted as model failures
On a Mac, Qwen3-4B (format=json, seed 4101) looped while writing A2 and Ollama aborted with HTTP 500 `prediction aborted, token repeat limit reached`; the server log contains no error text (only the HTTP reply does). That is the model failing to produce an answer, so `ollama_run.py` now records it as `MODEL_DEGENERATE_REPETITION` (a model-attributable completion failure, `retry_required=false`, counted in the pipeline's completion cost) instead of `TRANSPORT_ERROR` (technical, excluded). Treating it as technical would silently remove failures that fall only on Pipeline A. If you disagree, remove the reason from `MODEL_FORMAT_FAILURES` in `pilot.py`, but then report the exclusion explicitly. Whether `format=none` or another seed avoids the loop has not been established here, and the smoke test only covers the A1 prompt.

# v9.3 notes

## v9.3: Ollama error diagnostics
HTTP errors from Ollama now keep the server's own error text in the stage record (it used to be dropped), and `scripts/ollama_diagnose.py --run runs/<id>/<world> --stage A2` replays one stored prompt under several settings (format json/none, num_ctx 32768/16384/8192) and prints the server's reply for each. Nothing is recorded or scored. The cause of an A2 HTTP 500 seen on a Mac is not established by this package. Note: `ollama_run.py` has no `--no-think` flag; thinking is OFF unless you pass `--think`.

## v9.2: API runner for the strong-model arm, closed calibration loophole
- **`scripts/api_run.py`** (Anthropic Messages API or OpenAI Chat Completions): `smoke` -> `run` / `batch`. Same run-folder layout, prompts, recorder, technical-failure rules and audit as the Qwen runner, so judge/stepwise/summary work unchanged. Thinking OFF (no `thinking` parameter; OpenAI `reasoning_effort` only if you pass it). Per-configuration smoke approval (provider, exact model ID, temperature, max tokens, format, reasoning effort). Records the model the API says answered every call and flags a run whose calls were answered by different models. Retries only transient errors (429/5xx/529/timeouts) with the identical request and logs every attempt. Keys come from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
  ```
  python3 scripts/api_run.py smoke --provider anthropic --model <EXACT_MODEL_ID>
  python3 scripts/api_run.py batch --provider anthropic --model <EXACT_MODEL_ID> --worlds R1_L R1_H --repeats 3
  ```
  **Never run against a live API in this package** (mock-HTTP tests only). Whether a model accepts `temperature`, `response_format` or `seed` is unknown here: the smoke test is where a rejected parameter shows up (`api_error`). Format enforcement differs between arms (Qwen: Ollama JSON mode; Anthropic: prompt only; OpenAI: json_object) and Anthropic exposes no seed: report both as limitations.
- **Judge provider**: `judge_review.py judge --judge-provider openai --judge-model <ID>` (default anthropic). The judge must not be a tested model: if the strong arm is Claude, judge with GPT, and the reverse.
- **Calibration loophole closed**: an undefined kappa (no variation) can never pass, even with `--min-minority 0` (that option only relaxes the minority-count check). `min_minority=3` is an admission floor, not proof of reliability; report the label distribution, kappa and disagreements.

# v9 notes

## v9 changes: your A1 judge design merged into the v8 stage-wise framework (audit of v7_A1Judge_v1)
Based on v8 (smoke gate A, stage-wise interface B, R0 separation C); nothing in scenarios, Gold, model inputs or the A/B prompts changed. What the merge did:
- **A1 worksheet/judge fields are now your richer scheme**: per reference condition `coverage` = FULL / PARTIAL / MISSING / INCORRECT (with the entity of the reference condition shown); per model atom `atom_assessment` = MATCHES_REFERENCE / SUPPORTED_ADDITIONAL / REDUNDANT / UNSUPPORTED / AMBIGUOUS; `logic_assessment` EQUIVALENT / NOT_EQUIVALENT / UNDETERMINED and `scope_assessment` MATCH / MISMATCH / UNCLEAR (a concrete issue list is mandatory for the negative values); `confidence` and `uncertain_cases`. Strict recall counts FULL only (lenient = FULL+PARTIAL is reported next to it); precision is now computable because every atom is assessed.
- **Judge replies are validated strictly** (every case that the standalone validator accepted is now a test): FULL without atoms, MISSING with atoms, empty rationale, an atom never assessed, an atom both matched and unsupported, invalid confidence, missing keys (clean rejection instead of a crash), duplicate/unknown IDs, INCORRECT attempts labelled as supported. Rejected replies are stored, never repaired.
- **Inputs are locked**: hashes of A1/A2/A3/B1 outputs and the three Gold files sit in a frozen `provenance` field, so a trimmed reference list (8 -> 1 condition), an A1 edited after judging, or different Gold is rejected. Judge identity and settings are recorded; a judge equal to the model under test is refused.
- **Prompts rewritten** (`judge_prompts/map.txt`): the four coverage levels and the extra-atom labels are defined, entity scope is supplied, the "reference exclusion = positive circumstance with NOT in the expression" convention is explained, `uncertain_cases` format fixed. Your original text is kept in `evaluation/A1_JUDGE_PROMPT_original.txt`; the mapping table is in `evaluation/README_CN.md`.
- **Offline use without an API**: `judge_review.py judge --run RUN --judge-model NAME --manual-dir DIR` writes each prompt to DIR; save the model's pure-JSON reply next to it (`<hash>.response.json`) and run the same command again.
- **Calibration/aggregation/integration**: judge-vs-human agreement uses categorical kappa on `A1_coverage`, `A1_atom_assessment`, `A1_logic_and_scope` plus the A2 and final-answer families; `summary` counts judge runs flagged `needs_human_check`; `pilot.py audit` shows the stage-wise status.
- **Calibration needs errors, not just agreement (added after an external review):** a label family where the human labels are almost constant (fewer than `--min-minority`, default 3, labels outside the most common one) is reported as `INSUFFICIENT_VARIATION`, never a pass, because 100% agreement on "everything correct" does not show the judge can find errors. Pairs within one run are correlated, so 20 pairs are not 20 independent samples. If the real runs contain too few genuine errors, add clearly marked adversarial samples. `correct_for_right_reasons` is judge-assessed reasoning plus a mechanical label check and must not be pooled with `decision_correct` as one accuracy.
- **Not done**: no real judge call, no human labels, no agreement result yet; A1 for R0 against the official native condition text is still manual. With min-pairs 20 the A1 coverage family needs >= 3 human-labelled runs (8 pairs per run), the atom family about the same, and the logic/scope family >= 10 runs (2 pairs per run); the final-answer families need the most runs, so plan the calibration sample at 12-15 runs, not 9.

# v8 notes (still valid except where the A1 field names above replace the older ones)

## v8 changes: stage-wise Gold review interface and R0 reporting (audit items B and C)

**What is and is not automated.** `pilot.py audit/summary` still compare only the final label (and R0 citation/label proxies). It does **not** judge whether an A1 atom means a reference condition, whether an A2 status is right for the right reason, or whether `missing_facts` mean the reference missing facts. Those are semantic judgements. `scripts/stepwise_review.py` is the controlled interface for them; an LLM judge is optional and only valid after calibration (see below).

```
python3 scripts/stepwise_review.py template --run runs/<run_id>/<world>   # worksheet + hidden pipeline key
cp runs/<run_id>/<world>/STEPWISE_REVIEW_TEMPLATE.json runs/<run_id>/<world>/STEPWISE_REVIEW.json   # fill it in
python3 scripts/stepwise_review.py score --run runs/<run_id>/<world>      # refuses unless complete and untampered
python3 scripts/stepwise_review.py summary                                # reviewed runs only; pending runs are counted, never imputed
```
- **A1:** per reference condition, which model atoms (if any) mean the same thing (`coverage` FULL/PARTIAL/MISSING/INCORRECT, with atom IDs); per model atom a five-way assessment (precision); logic and entity-scope assessments. Output: strict and lenient reference-condition recall, atom precision, logic/scope assessments.
- **A2:** scored only for reference conditions that A1 actually matched (otherwise "not scorable because A1 missed it", counted separately, not as an A2 error): `status_correct`, `quote_supports_status`. Exact-status equality is reported as a *mechanical proxy* next to the human number; any disagreement between the two is listed.
- **Missing information (A3 and B1):** each model-listed fact is matched to a reference missing fact (`G_C2`, `G_C3` in R3) or marked `unnecessary`. Output: reference-fact recall, share of listed facts that are not unnecessary. For R1/R2 (nothing missing) every listed fact must be judged.
- **Right answer for the right reasons:** `decisive_conditions_correct`, `asserts_unsupported_fact`, `scope_correct` -> `correct_for_right_reasons = decision correct AND reasoning grounded`.
- **Safeguards:** the two final outputs are shown as F1/F2 in a hidden order (key in `STEPWISE_BLIND_KEY.json`); every Gold/model field is frozen and a changed copy is rejected; the reviewer name is required; inconsistent combinations (a "match" with no atom, an A2 verdict on a condition A1 never matched) are rejected. `audit` reports `PENDING_MANUAL_REVIEW` for every synthetic run until a score exists.
- **Scale: every run is stage-wise evaluated, a human labels only a calibration sample.** All 36 synthetic run folders (2 models x 3 repetitions x 6 worlds) cannot realistically be labelled by hand, so `scripts/judge_review.py` lets an LLM judge fill the *same* worksheet (`STEPWISE_REVIEW_JUDGE.json`, never the human file):
  ```
  python3 scripts/judge_review.py sample --n 9 --seed 20261018          # stratified by model x world; human labels these
  # ...human completes STEPWISE_REVIEW.json for those runs (see above)...
  python3 scripts/judge_review.py judge-all --judge-model <MODEL>       # needs ANTHROPIC_API_KEY; labels every synthetic run
  python3 scripts/judge_review.py agreement --min-kappa 0.7 --min-pairs 20   # judge vs human on the sample -> reports/STEPWISE_AGREEMENT.json
  python3 scripts/stepwise_review.py score --run <RUN> --source judge   # per run, then: stepwise_review.py summary
  ```
  The judge sees narrow multiple-choice-style questions at temperature 0, with no model, pipeline or run identity; every prompt and response is logged in `judge_logs/`; its output must pass the same validator as a human review or it is stored as `*_REJECTED.json` (no repair). The acceptance rule (every label family needs enough paired labels and kappa >= 0.7) is fixed before any results exist. `summary` keeps **human-confirmed** and **LLM-judge-labelled** numbers apart and prints "UNCALIBRATED: do not report as a result" next to judge numbers until a passing agreement report exists.
  Choose a judge that is not a model under test (a same-family judge is a possible self-preference bias; state it if unavoidable). The judge API call itself has not been exercised live in this package (no key here): run `judge --run` on one run first and read `judge_logs/`.
- **Still needed:** the human labels on the calibration sample, the judge smoke run, and (ideally) a second human on part of the sample to measure human-human agreement; a judge cannot be better validated than its human reference.

**R0 is reported separately.** R0 is the original ConditionalQA case scored against the official conditional answers (3 YES + 5 NO conditions); R1_L..R3_H are scored against researcher labels (YES / NO / NEED_MORE_INFORMATION). `pilot.py summary` therefore computes `path_A`, `path_B` and `paired_quality` for the synthetic worlds only, and reports R0 in its own `R0_official` block: completion, the manual native-Gold semantic review status (reviewed vs pending, pooled official-condition recall), the citation-only proxy (labelled as a proxy) and label-level branch coverage. For the Monday presentation show R0 in its own table; do not merge it with the R1-R3 accuracy. Note that the R0 semantic review is **still a manual step** (`R0_NATIVE_SEMANTIC_REVIEW.json` in each R0 run); until it is done, the R0 block says "pending", not a number.

## v8 changes: smoke approval now covers the full sampling configuration
- **Approval is per configuration, stored in `smoke_reports/approved/<config_id>.json`.** The configuration is: model, digest, think, format mode, num_ctx, num_predict, **temperature, top_p, top_k**. Any difference means no approval.
- **Seeds are approved as a list.** `ollama_smoke.py --seeds 4101 4102 4103` (default, equal to the batch script's `seed_start + repetition - 1`) probes every planned seed once and approves exactly those; a run with another seed is refused.
- **`--matrix` is diagnostic only.** It never writes or overwrites an approval; the formal smoke test is always a single-configuration run. Previously the matrix wrote `approved.json` once per combination, so the last combination silently became the "approved" one.
- A failed seed writes no approval and leaves existing approvals untouched. The thinking-OFF main arm and the thinking-ON control arm can be approved side by side.
- Smoke defaults now equal the runner defaults (temperature 0.6; previously the smoke test used 0.0 while the runs used 0.6).
- `run_qwen_repeats.py` checks every planned (configuration, seed) pair **before starting any run**.
- New `tests/test_smoke_gate.py` (7 tests; mocked Ollama, no network). Mutation-tested: removing temperature or top_k from the approval, skipping the seed check, letting `--matrix` write an approval, or approving despite a failed seed each make a test fail.
- Still not verified: that Ollama actually honours `seed` with `temperature>0` on your build. The smoke test only records the request and response; it does not compare two identical-seed outputs. (Open item for the real smoke run.)

## v7 changes (audit of v6, 2026-10-18)
1. **Per-world Gold.** Each of the six synthetic worlds now has its own evidence notes and verbatim quotes (L and H no longer share notes that cite details only present in H). Evidence typing is split into `evidence_basis` (explicit / entailed / unknown / conflicting), `evidence_source_form` and `outcome_relevance`; the old mixed `evidence_type` is removed. Gold JSON, stepwise worksheet and `review/condition_evidence_crosswalk.csv` come from one source and are checked for identity.
2. **R1:** recognition is now stated for 16 March 2026 (L and H); R1_H states that £80 is well below £980 and that the sales/booking/event records show nothing linked to the payment.
3. **R3_H:** 341 -> 297 words; the thank-you email is no longer called a "benefit"; X3-X5 keep the explicit remitter-page sentence. **R3_L:** the conclusion sentence ("cannot establish whether...") is replaced by the fact (the coverage/duration page is missing).
4. **Length control:** High variants are 272 / 271 / 297 words (spread 26; validator limit 40). Low variants are 114 / 84 / 93.
5. **Thinking is OFF by default** in `ollama_run.py`, `ollama_smoke.py`, `run_qwen_repeats.py` (opt in with `--think`, which runs as a separate cohort/prefix). The smoke approval records the think setting, so a fresh smoke test is required for the main matrix.
6. B1 wording "eligibility question" -> "decision question"; `pilot.py check` regains the E01-E13 and prompt-leak assertions; new `checks/validate_lowhigh.py` (run from the tests).

## Experiment (frozen design, not run)
- R0: original official ConditionalQA dev-38; evaluate against **original conditional answers and conditions** only.
- R1_L/H: definite YES; R2_L/H: definite NO; R3_L/H: NEED_MORE_INFORMATION.
- LOW and HIGH in each pair share the same **researcher-defined scoped Gold state** and decision; HIGH distributes facts across records and introduces relevant distractors.
- A1 receives original policy + decision question + full scenario; A2 evaluates scenario against actual A1 IR; A3 reads **original policy + original scenario + question + A1 IR + A2 state** and decides by LLM. B1 receives original policy + scenario + question and directly decides by LLM.
- Python only orchestrates, stores, and audits; **it never makes A3's decision**. No Pipeline C and no interactive questioning in this phase.

## Matrix
7 scenarios × 2 pipelines × 2 models × 3 repetitions = **84 end-to-end pipeline runs**, approximately 168 LLM calls if all A stages complete.
For each Qwen repetition, one run ID per world and seed. Claude web repetition settings/seeds may not be controllable; record actual interface/version and prompt hashes.

## Start
```
python3 scripts/pilot.py check
python3 scripts/pilot.py prepare --world R1_L --run-id manual_rep01 --model "Claude Sonnet 5.5"
```
For Qwen, first run the Ollama smoke test on the Mac with the SAME settings, then:
```
python3 scripts/ollama_smoke.py --model qwen3:4b-instruct          # think OFF; temperature 0.6; probes seeds 4101-4103 and approves them
python3 scripts/run_qwen_repeats.py --repeats 3          # main matrix, thinking OFF (refuses to start without a matching approval)
# thinking-ON control arm: run BOTH commands with --think (separate approval, run prefix qwen_think_v7)
# optional diagnostic: python3 scripts/ollama_smoke.py --matrix   (never approves anything)
```
Never start bulk runs before reviewing the generated prompts and smoke compatibility.

## Gold and evaluation
- `data/official_dev38_original.json`: official source; **do not replace native R0 conditional Gold** with a researcher NEI label.
- `gold/reference_conditions_and_logic.json`: 3 YES + 5 NO atoms only; Boolean interpretation is researcher scoped, not official executable law.
- `gold/gold_per_run.json`: synthetic Gold only.
- `gold/STEPWISE_GOLD_REVIEW.json`: per-stage human-review worksheet (not an automated semantic accuracy claim).
- `docs/DESIGN_AUDIT.json`: pairing checks and remaining risks.
- `SHA256_MANIFEST.json`: hashes of shipped files.

## Interpretation
The runner reports synthetic label accuracy and completion separately. R0 is not assigned synthetic final-label accuracy. Native R0 condition semantic alignment requires manual review; citation overlap is only a proxy. This is a **feasibility pilot**, not a statistically independent benchmark.

## Important unresolved
- Independent semantic/domain review of synthetic Gold is still outstanding.
- Strong/weak model versions and local Ollama configuration must be frozen at runtime.
- The package contains the v5 runner with matrix, R0 evaluation and thinking-default changes; **there is no Claude API runner yet** (planned after the Gold is frozen); **the stage-wise semantic Gold worksheet is not an automated evaluator**.
