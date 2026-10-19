# Artifact index — exact files behind each experimental step

**Upload status (2026-10-19):** The frozen source files below are prepared in the downloadable reviewer archive, but have **not yet been committed to this repository**. Consequently the GitHub paths shown in code formatting are **planned paths, not working hyperlinks**. We will activate links only after uploading and verifying their targets. No fabricated prompts or substituted model outputs.

The **full reviewer ZIP** is available in the ChatGPT conversation as `GiftAid_dev38_Reviewer_Release_WITH_CLAUDE_RETRY_20261019.zip`. GitHub cannot open a local ChatGPT ZIP unless its contents are committed here.

## Source and scenario construction

| Role | Exact archive path | What to inspect |
|---|---|---|
| Original ConditionalQA item | `data/official_dev38_original.json` | Unmodified question/background and native conditional answer |
| Frozen 13 policy passages | `data/policy_dev38_13.json` | Source passage text and E01–E13 IDs |
| Researcher-synthesized scenario definitions | `data/scenarios_R0_R4.json` | R1/R2/R3 Low/High case evidence |
| Actual selected input | `model_inputs/R2_L.json` | Robyn Low input, as frozen for the run |
| Researcher reference conditions | `gold/reference_conditions_and_logic.json` | C1–C3, X1–X5, Boolean logic |
| Per-run reference outcomes | `gold/gold_per_run.json` | Gold final decision and intermediate reference states |

## A1 — Define conditions and logical structure

- **Prompt template:** `prompts/A1_extract.txt`
- **Actual full rendered prompt:** `runs/qwen_nothink_v7_rep01/R2_L/A1_PROMPT.txt`
- **Raw output:** `runs/qwen_nothink_v7_rep01/R2_L/A1_raw.txt`
- **Parsed JSON:** `runs/qwen_nothink_v7_rep01/R2_L/A1_parsed.json`
- **Code:** `scripts/pilot.py` — `prepare`, `format_shared`, `parse_json_envelope`, `schema_validate`; `scripts/stage_schemas.py`
- **Actual inference implementation:** `scripts/ollama_run.py` — `run_stage` (Qwen); `scripts/api_run.py` — `run_stage` (Claude)

## A2 — Populate the same IR with case evidence

- **Prompt template:** `prompts/A2_match.txt`
- **Actual rendered prompt:** `runs/qwen_nothink_v7_rep01/R2_L/A2_PROMPT.txt`
- **Raw output:** `runs/qwen_nothink_v7_rep01/R2_L/A2_raw.txt`
- **Parsed JSON:** `runs/qwen_nothink_v7_rep01/R2_L/A2_parsed.json`
- **Code:** `scripts/pilot.py` — `format_a2`, `record`, `schema_validate`, `diagnostic_flags`
- **Dependency:** A2 receives A1 condition definitions and the case evidence; it does not invent a second IR.

## A3 — Final LLM decision

- **Prompt template:** `prompts/A3_llm_decide.txt`
- **Actual rendered prompt:** `runs/qwen_nothink_v7_rep01/R2_L/A3_PROMPT.txt`
- **Raw output:** `runs/qwen_nothink_v7_rep01/R2_L/A3_raw.txt`
- **Parsed JSON:** `runs/qwen_nothink_v7_rep01/R2_L/A3_parsed.json`
- **Code:** `scripts/pilot.py` — `format_a3`, `record`, `audit_run`
- **Dependency:** Original sources + A1 and A2 outputs. A3 remains an LLM decision.

## B1 — Direct baseline

- **Prompt template:** `prompts/B1_direct.txt`
- **Actual rendered prompt:** `runs/qwen_nothink_v7_rep01/R2_L/B1_PROMPT.txt`
- **Raw output:** `runs/qwen_nothink_v7_rep01/R2_L/B1_raw.txt`
- **Parsed JSON:** `runs/qwen_nothink_v7_rep01/R2_L/B1_parsed.json`
- **Code:** `scripts/pilot.py` — `prepare`, `record`, `audit_run`; model execution in `scripts/ollama_run.py` or `scripts/api_run.py`
- **Dependency:** Same 4 source components, no A1/A2 output.

## Evaluation and retry provenance

- **Stage/output validation:** `scripts/stage_schemas.py`, `scripts/pilot.py` (`schema_validate`, `diagnostic_flags`)
- **Reference comparison and summary:** `scripts/pilot.py` (`audit_run`, `summarize_runs`)
- **Run-specific post-hoc comparison:** `runs/qwen_nothink_v7_rep01/R2_L/POST_RUN_GOLD_COMPARISON.json`
- **Model configuration:** `runs/qwen_nothink_v7_rep01/R2_L/run_metadata.json`
- **Claude original failed attempt:** `runs/claude46_qorig_all_rep03/R3_H/`
- **Claude successful original-question retry:** `runs/claude46_qorig_all_retry_rep01/R3_H/` (included in the updated reviewer ZIP; retain both records)

**Publication gate:** Before saying “click to audit”, each of these files must be present on the default branch, checked against its original SHA-256 hash and linked from the relevant step in [the step-by-step guide](STEP_BY_STEP_EXPERIMENT.md).
