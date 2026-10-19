# Artifact index — exact files behind each experimental step

**Upload status (2026-10-19):** The experiment code, synthetic scenarios, canonical and rendered prompts, original model responses, parsed JSON and run metadata are uploaded. The original failed Claude attempt and successful retry are separately preserved.

The files below are direct GitHub links. For one complete run, start with [Qwen R2_L rep01 walkthrough](REVIEWER_WALKTHROUGH.md).

## Source and scenario construction

| Role | Exact archive path | What to inspect |
|---|---|---|
| Original ConditionalQA item | [data/official_dev38_original.json](../data/official_dev38_original.json) | Unmodified question/background and native conditional answer |
| Frozen 13 policy passages | [data/policy_dev38_13.json](../data/policy_dev38_13.json) | Source passage text and E01–E13 IDs |
| Researcher-synthesized scenario definitions | [data/scenarios_R0_R4.json](../data/scenarios_R0_R4.json) | R1/R2/R3 Low/High case evidence |
| Actual selected input | [model_inputs/R2_L.json](../model_inputs/R2_L.json) | Robyn Low input, as frozen for the run |
| Researcher reference conditions | [gold/reference_conditions_and_logic.json](../gold/reference_conditions_and_logic.json) | C1–C3, X1–X5, Boolean logic |
| Per-run reference outcomes | [gold/gold_per_run.json](../gold/gold_per_run.json) | Gold final decision and intermediate reference states |

## A1 — Define conditions and logical structure

- **Prompt template:** [prompts/A1_extract.txt](../prompts/A1_extract.txt)
- **Actual full rendered prompt:** [A1_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A1_PROMPT.txt)
- **Raw output:** [A1_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A1_raw.txt)
- **Parsed JSON:** [A1_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A1_parsed.json)
- **Code:** [scripts/pilot.py](../scripts/pilot.py) — `prepare`, `format_shared`, `parse_json_envelope`, `schema_validate`; [scripts/stage_schemas.py](../scripts/stage_schemas.py)
- **Actual inference implementation:** [scripts/ollama_run.py](../scripts/ollama_run.py) — `run_stage` (Qwen); [scripts/api_run.py](../scripts/api_run.py) — `run_stage` (Claude)

## A2 — Populate the same IR with case evidence

- **Prompt template:** [prompts/A2_match.txt](../prompts/A2_match.txt)
- **Actual rendered prompt:** [A2_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A2_PROMPT.txt)
- **Raw output:** [A2_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A2_raw.txt)
- **Parsed JSON:** [A2_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A2_parsed.json)
- **Code:** [scripts/pilot.py](../scripts/pilot.py) — `format_a2`, `record`, `schema_validate`, `diagnostic_flags`
- **Dependency:** A2 receives A1 condition definitions and the case evidence; it does not invent a second IR.

## A3 — Final LLM decision

- **Prompt template:** [prompts/A3_llm_decide.txt](../prompts/A3_llm_decide.txt)
- **Actual rendered prompt:** [A3_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A3_PROMPT.txt)
- **Raw output:** [A3_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A3_raw.txt)
- **Parsed JSON:** [A3_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A3_parsed.json)
- **Code:** [scripts/pilot.py](../scripts/pilot.py) — `format_a3`, `record`, `audit_run`
- **Dependency:** Original sources + A1 and A2 outputs. A3 remains an LLM decision.

## B1 — Direct baseline

- **Prompt template:** [prompts/B1_direct.txt](../prompts/B1_direct.txt)
- **Actual rendered prompt:** [B1_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/B1_PROMPT.txt)
- **Raw output:** [B1_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/B1_raw.txt)
- **Parsed JSON:** [B1_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/B1_parsed.json)
- **Code:** [scripts/pilot.py](../scripts/pilot.py) — `prepare`, `record`, `audit_run`; model execution in [scripts/ollama_run.py](../scripts/ollama_run.py) or [scripts/api_run.py](../scripts/api_run.py)
- **Dependency:** Same 4 source components, no A1/A2 output.

## Evaluation and retry provenance

- **Stage/output validation:** [scripts/stage_schemas.py](../scripts/stage_schemas.py), [scripts/pilot.py](../scripts/pilot.py) (`schema_validate`, `diagnostic_flags`)
- **Reference comparison and summary:** [scripts/pilot.py](../scripts/pilot.py) (`audit_run`, `summarize_runs`)
- **Run-specific post-hoc comparison:** [POST_RUN_GOLD_COMPARISON.json](../runs/qwen_nothink_v7_rep01/R2_L/POST_RUN_GOLD_COMPARISON.json)
- **Model configuration:** [run_metadata.json](../runs/qwen_nothink_v7_rep01/R2_L/run_metadata.json)
- **Claude original failed attempt:** `runs/claude46_qorig_all_rep03/R3_H/`
- **Claude successful original-question retry:** `runs/claude46_qorig_all_retry_rep01/R3_H/` (uploaded; retain both records)

**Integrity note:** Original responses and the failed/successful Claude runs are retained; see [archive completeness](ARCHIVE_COMPLETENESS.md). The repository manifest and scripts support file checks, but the researcher Gold is not independently certified.

## Quality and reproducibility caveats

Read the [2026-10-19 quality audit](QUALITY_AUDIT_20261019.md) before citing repository integrity or the 18/18 Claude count. The frozen archive manifest has not been reconciled with all current GitHub files.
