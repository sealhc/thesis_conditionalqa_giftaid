# Walkthrough: one real paired run — Qwen3-4B / R2_L / rep01

This page is a **click-through audit**, not an invented illustration. Both arms receive the same four inputs. Select **R2_L** (Robyn Ellis £260; Low evidence complexity), original question, and the saved Qwen3-4B-Instruct rep01 run.

## 0. Freeze the input and configuration

- [Exact case input](../model_inputs/R2_L.json) · [13 source policy passages](../data/policy_dev38_13.json) · [native benchmark record](../data/official_dev38_original.json)
- [Run metadata](../runs/qwen_nothink_v7_rep01/R2_L/run_metadata.json) · [researcher gold](../gold/gold_per_run.json)
- The saved Qwen metadata reports `qwen3:4b-instruct`, `think=false`, temperature **0.6**, top_p **0.95**, top_k **20**, seed **4101**, context **32768**, output budget **8192**, quantization **Q4_K_M**, and a model digest. Do not describe this run as temperature 0.7. Exact per-run settings are in the saved metadata.
- Gold decision **NO**: £260 purchased advertising services, triggering E10; tax/declaration are not decisive after this exclusion is established.

## 1. Pipeline A (3 model calls, 1 intermediate representation)

| Step | Exact prompt | Original raw output | Parsed JSON | Audit |
|---|---|---|---|---|
| **A1** build condition structure | [A1_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A1_PROMPT.txt) | [A1_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A1_raw.txt) | [A1_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A1_parsed.json) | 8 atoms extracted, but `eligibility_expression` is only `{"ref":"a1"}`; 7 atoms unused by formula |
| **A2** populate same structure with evidence states | [A2_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A2_PROMPT.txt) | [A2_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A2_raw.txt) | [A2_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A2_parsed.json) | a5 FALSE correctly signals service-payment exclusion; a1 TRUE vs researcher reference UNKNOWN |
| **A3** final LLM decision | [A3_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/A3_PROMPT.txt) | [A3_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/A3_raw.txt) | [A3_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/A3_parsed.json) | Decision NO, decisive a5/E10; correct final label |

**A1 logic audit:** `atom` = one named condition. `ref` = pointer to an atom. `eligibility_expression` = formula combining conditions. Qwen defines eight conditions, but its actual formula points only to a1. Our reference requires C1,C2,C3 and no X1–X5 exclusions; for Qwen's negated a4–a8 atoms this can be expressed as AND(a1,...,a8), subject to the composite scope of a1.

**A2 evidence audit:** a1 merges donor scope with charity recognition; a recognition letter is on file but date-specific recognition was not established under the frozen researcher standard. The model overstates a1 as TRUE. a5 is “not payment for goods/services”; FALSE is correct because £260 purchased advertising.

**A3 absorption observation:** A3's NO is correct despite these upstream defects. It can still see original policy/case, so we cannot prove whether it used the IR or independently recovered E10.

## 2. Pipeline B (1 model call, no explicit IR)

| Exact prompt | Original raw output | Parsed JSON | Run record |
|---|---|---|---|
| [B1_PROMPT.txt](../runs/qwen_nothink_v7_rep01/R2_L/B1_PROMPT.txt) | [B1_raw.txt](../runs/qwen_nothink_v7_rep01/R2_L/B1_raw.txt) | [B1_parsed.json](../runs/qwen_nothink_v7_rep01/R2_L/B1_parsed.json) | [B1_record.json](../runs/qwen_nothink_v7_rep01/R2_L/B1_record.json) |

**B1 actual decision:** NEED_MORE_INFORMATION — **incorrect**. Its justification recognizes that the £260 purchased advertising and that E10 excludes payments for services. It nevertheless returns an unresolved label. This is a **reasoning-to-decision consistency failure**, not simply an inability to retrieve E10.

## 3. Reproduce the audit, not the outcome

- [A1 record](../runs/qwen_nothink_v7_rep01/R2_L/A1_record.json) / [A2 record](../runs/qwen_nothink_v7_rep01/R2_L/A2_record.json) / [A3 record](../runs/qwen_nothink_v7_rep01/R2_L/A3_record.json) / [B1 record](../runs/qwen_nothink_v7_rep01/R2_L/B1_record.json).
- [Post-run gold comparison](../runs/qwen_nothink_v7_rep01/R2_L/POST_RUN_GOLD_COMPARISON.json) is generated **after** model calls; it is not fed to the model.
- Inspect the exact `*_PROMPT.txt`, `*_raw.txt`, `*_parsed.json` in order; verify input equality and version/seed in metadata. Re-running stochastic models may not reproduce the same exact text even at a fixed seed/provider.
