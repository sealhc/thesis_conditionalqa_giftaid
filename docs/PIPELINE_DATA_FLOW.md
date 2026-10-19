# Pipeline architecture: exact inputs, outputs and what flows between calls

## Shared inputs

Both pipelines receive the **same** 4 source components: 13 policy passages (E01–E13), original charity background, the selected researcher-synthesized additional information, and the original question. **No reference labels or native gold are supplied to the model.** See each run's `*_PROMPT.txt` for the *rendered* input.

```text
                   ┌──────────── 1 EXPLICIT INTERMEDIATE REPRESENTATION ────────────┐
Pipeline A: INPUT ──[A1: define conditions + logic] ──[A2: fill evidence states]────┘──[A3: LLM decision]──OUTPUT
Pipeline B: INPUT ───────────────────────────────────────[B1: LLM direct decision]──────────────────────────────OUTPUT
```

The box above is **one** IR constructed in **two calls**. It is not 2 separate representation layers. The actual pipeline code and stage schema are under `scripts/`. The precise per-call content must be checked in saved `*_PROMPT.txt` and `*_record.json`.

## A1 — Define the IR structure

**Prompt purpose:** identify policy-grounded propositions (atomic conditions), their sources and how they combine. Do **not** assign case truth values or decide the donation. **Output format:** JSON object, with fields including `atoms` and `eligibility_expression`.

- An **atom** is one named proposition: `{"id":"a5", "proposition":"The donation is not a payment for goods or services", ...}`.
- `{"ref":"a5"}` means **“use the previously defined condition a5”**. It is a pointer, not a new rule or model call.
- **`eligibility_expression`** is the *Boolean formula combining those conditions into eligibility*. It is not a decision output or a reference to an external file.

The reference rule is `C1 ∧ C2 ∧ C3 ∧ ¬(X1 ∨ X2 ∨ X3 ∨ X4 ∨ X5)`. Applying De Morgan: `¬(X1∨...∨X5) = ¬X1 ∧ ... ∧ ¬X5`. If a model writes a4–a8 as **already negated exclusion propositions**, then all eight atoms should be combined by AND. **Do not assume atom order is the same as X1–X5 order**:

| Model atom in R2_L rep01 | Meaning | Relation to researcher reference |
|---|---|---|
| a1 | Individual gift to recognised charity/CASC | C1 plus individual-donor scope |
| a2 | Sufficient donor tax | C2 |
| a3 | Valid Gift Aid declaration | C3 |
| a4 | Not Payroll Giving | NOT X3 |
| a5 | Not a goods/services payment | NOT X1 |
| a6 | Donor benefit within limit | NOT X2 |
| a7 | Not shares | NOT X4 |
| a8 | Not charity cards/vouchers | NOT X5 |

For the model's own atom polarity, a *correct composition* would look like:

```json
{"eligibility_expression":{"op":"ALL","args":[
  {"ref":"a1"},{"ref":"a2"},{"ref":"a3"},{"ref":"a4"},
  {"ref":"a5"},{"ref":"a6"},{"ref":"a7"},{"ref":"a8"}
]}}
```

**Syntax note:** `op: ALL` is the actual schema grammar; `and` is not a valid operator key here. This example describes the desired Boolean composition of the model's own atom meanings, not the model's actual output.\n\n**Important:** this JSON is an explanatory **expected logical structure**, not an unmodified saved output or the native ConditionalQA answer. In the audited Qwen run, the *actual* A1 output is `{"eligibility_expression":{"ref":"a1"}}`: it defines 8 atoms but uses **only a1** in the formula. This is a **logic-composition error**, even though condition coverage can be high.

## A2 — Populate the same IR with case evidence

**Prompt purpose:** take A1's conditions and the case evidence, assign a status to each atom and cite supporting evidence. **Output:** JSON `statuses` list with `id`, `status`, `evidence_quote`, `justification` and other schema-defined fields. It **does not construct a second IR**.

In Qwen R2_L rep01:
- `a2 = UNKNOWN`: Robyn's qualifying tax sufficiency has not been checked.
- `a3 = UNKNOWN`: a declaration covering the £260 has not been checked.
- `a5 = FALSE`: a5 is the *negated* proposition “not payment for services”, but emails show the £260 bought advertising services. This FALSE correctly captures the E10 exclusion.
- `a1 = TRUE`: the model treats charity recognition as established, while the researcher intermediate reference is UNKNOWN because date-specific validity is not established. This is a **reference-relative over-inference**, not an independently adjudicated legal fact.

A2's actual status output is **not the same object as A1's atom definitions**; it is the state assignment associated with those atoms. Together A1+A2 form the usable IR.

## A3 — Final decision from IR and original sources

**Prompt purpose:** read the original sources and populated IR, check discrepancies and return the final 3-class decision. **Output:** JSON with `decision`, `decisive_condition_ids`, `supporting_policy_ids`, `justification`, `missing_facts`, etc. In the audited Qwen run, A3 returns **NO** and cites the E10 advertising-services exclusion. Because A3 can read the original sources, the correct result **does not prove** the incomplete A1 formula was successfully executed.

## B1 — Direct baseline

**Prompt purpose:** use the same original sources to identify rules, match evidence and decide **in one LLM call**, without producing a persistent explicit IR. **Output:** the same 3-class decision family as A3, plus justification and missing facts. In Qwen R2_L rep01, B1 returns **NEED_MORE_INFORMATION** while acknowledging that advertising-service payments are excluded under E10: **the final label conflicts with its stated reasoning**.

## Audit contract for each saved stage

- `A1_PROMPT.txt` / `A2_PROMPT.txt` / `A3_PROMPT.txt` / `B1_PROMPT.txt`: **exact rendered prompt**, including stage instructions and included source material.
- `*_raw.txt` and `*_RECEIVED_RAW_UNPROCESSED.txt` (when present): original model response records.
- `*_parsed.json`: parsed machine-readable model output, not an independently validated gold answer.
- `*_record.json`, `*_stage_attempts.json`, `*_ollama_request.json` or API metadata: execution settings, parse status, attempts and diagnostics.
- `POST_RUN_GOLD_COMPARISON.json`: reference comparison generated after the run. **Do not mistake this file for a model input.**
