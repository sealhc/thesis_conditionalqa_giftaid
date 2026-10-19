# Experimental workflow — a step-by-step guide for new reviewers

> **Status:** This document explains the frozen study design. The archived prompts, JSON outputs and code are being uploaded separately. No link should be treated as an original run record until its target exists in this repository.

## 1. What is the question?

Can a language model make more reliable, auditable decisions from policy documents when it first writes down **the conditions that govern the decision** and **the evidence for each condition**, rather than answering directly?

This is an exploratory pilot using **one original ConditionalQA question (dev-38, Gift Aid)**. We compare two pipelines under the same source input. We do **not** compare two different policies or different underlying cases.

**[Artifact map: exact policy, synthetic evidence, prompt templates, raw outputs and code paths for every step](ARTIFACT_INDEX.md)**. The artifact index identifies what is prepared and which files are not yet published; it does not disguise missing uploads as working links.

## 2. What does the model receive? Four source components

| Component | What it contains | Origin | Fixed or varied? |
|---|---|---|---|
| **1. Policy evidence** | 13 passages (E01–E13) about Gift Aid eligibility and exclusions | ConditionalQA's selected supporting evidence | Fixed |
| **2. Original background** | “I run a small, independent charity which attempts to help people struggling with their vet costs. I have several people who pay monthly by direct debit and also receive some one off donations.” | Original ConditionalQA dev-38 | Fixed |
| **3. Additional information** | Facts about a specific £120/£260/£95 payment, its donor, documents and circumstances | Researcher-synthesized | Varied |
| **4. Question** | “Am I eligible for Gift Aid?” | Original ConditionalQA dev-38 | Fixed for original-question runs |

**Important:** The model does not receive the benchmark's native conditional answer or the researcher's expected decision and intermediate-stage reference states. Those are used only for evaluation **after** the run.

## 3. Why synthesize additional evidence?

The original benchmark asks a conditional question and often answers **YES if conditions are satisfied; NO if an exclusion applies**. It does not supply enough facts to classify a specific donation as definitely eligible or ineligible. We therefore construct evidence variants under a frozen policy:

| Scenario | Intended final decision | What makes the decision possible |
|---|---|---|
| **R0** | Original conditional answer | Unmodified original item; evaluated separately |
| **R1: Elaine £120** | **YES** | Required facts established, no exclusion |
| **R2: Robyn £260** | **NO** | £260 was the agreed price of grooming-salon advertising, triggering the service-payment exclusion (E10) |
| **R3: Devon £95** | **NEED_MORE_INFORMATION** | Tax sufficiency for the relevant tax year and a declaration covering this gift remain unresolved |

For each R1–R3, **Low** presents relatively direct evidence in one place; **High** spreads the evidence across records, adds indirect clues and distractors. **Gold is unchanged between Low and High.** This is a bundled evidence-presentation manipulation, not a pure test of logical-rule complexity.

## 4. The two pipelines, side by side

```text
SAME INPUT: policy + original background + additional evidence + question
         │
         ├─ PIPELINE A
         │    A1: build condition definitions + combination formula (JSON)
         │       ↓ A1 output + original sources
         │    A2: fill each condition with a case-specific evidence state (JSON)
         │       └── A1 + A2 together form ONE explicit intermediate representation
         │       ↓ populated IR + original sources
         │    A3: final LLM decision (JSON: YES / NO / NEED_MORE_INFORMATION)
         │
         └─ PIPELINE B
              B1: read the same original sources and decide directly in one LLM call
                  (JSON: YES / NO / NEED_MORE_INFORMATION)
```

**A1 and A2 are two construction operations on one intermediate layer, not two separate layers.** Both final decisions are made by an LLM, not by a deterministic Python rule engine. Pipeline A uses three calls; Pipeline B uses one. Therefore any difference is a **whole-pipeline effect**, not causal proof that the intermediate representation alone is responsible.

## 5. A1 — Extract the condition structure

**[A1: source input, prompt template, rendered prompt, JSON output and implementation code](ARTIFACT_INDEX.md#a1--define-conditions-and-logical-structure)**



**Input:** fixed policy evidence and the decision question; see the actual rendered A1 prompt for the exact additional context included in the call.

**Prompt objective:** identify conditions that govern Gift Aid and write a machine-readable JSON representation, **without deciding this donor's case**.

**Output:** an array of named conditions (`atoms`) and an `eligibility_expression` specifying **how they combine**.

- **Atom:** a named proposition such as `a5 = “The payment is NOT for goods or services”`.
- **`ref: "a5"`:** a reference to the already-defined condition a5; it does not mean a new condition.
- **`eligibility_expression`:** the complete Boolean eligibility rule combining the conditions, not a final YES/NO label.

The researcher reference policy is:

`(C1 AND C2 AND C3) AND NOT (X1 OR X2 OR X3 OR X4 OR X5)`.

By De Morgan's law, `NOT (X1 OR ... OR X5)` equals `NOT X1 AND ... AND NOT X5`. If A1 writes its last five conditions as **negated exclusion propositions**, the formula should combine all eight with AND.

**Actual Qwen R2_L rep01 atom meanings:**

| Atom | Meaning | Corresponding reference |
|---|---|---|
| a1 | Individual donation to recognised charity | C1 plus donor scope |
| a2 | Sufficient donor tax | C2 |
| a3 | Valid Gift Aid declaration | C3 |
| a4 | Not Payroll Giving | NOT X3 |
| a5 | Not a payment for goods/services | NOT X1 |
| a6 | Donor benefit within allowed limit | NOT X2 |
| a7 | Not shares | NOT X4 |
| a8 | Not charity cards/vouchers | NOT X5 |

**Actual A1 defect:** Qwen outputs eight atoms but its entire `eligibility_expression` is `{"ref":"a1"}`. This means **only the first condition is used by the rule**; the other seven are not linked into the decision formula. This is a logic-composition error, even if all relevant policy topics were extracted.

## 6. A2 — Populate the SAME representation using evidence

**[A2: source evidence, prompt, model output and code](ARTIFACT_INDEX.md#a2--populate-the-same-ir-with-case-evidence)**



**Input:** A1's condition definitions and the case evidence, with original sources as specified by the rendered prompt.

**Prompt objective:** for **each condition already defined by A1**, assign `TRUE`, `FALSE`, `UNKNOWN` or `CONFLICTING`; cite an evidence quote and explain the assignment. **Do not make the final decision.**

**Output:** JSON condition statuses, evidence quotes and justifications. The condition IDs refer back to A1, so A1+A2 together form **one populated IR**.

For Robyn R2_L, Qwen produces:

| Condition | Actual status | Why |
|---|---|---|
| a2: sufficient tax | UNKNOWN | The donor's qualifying tax sufficiency was not checked |
| a3: valid declaration | UNKNOWN | A declaration covering this £260 payment was not checked |
| a5: **not** payment for services | **FALSE** | Emails establish £260 purchased advertising services — this is the decisive E10 exclusion |
| a1: individual donation to recognised charity | TRUE | Model treats recognition as established; the researcher reference is UNKNOWN because date-specific recognition was not established |

**Observed A2 discrepancy:** the model asserts a1 TRUE where the researcher's evidence threshold requires UNKNOWN. The exact reference threshold needs independent domain review; do not present it as a legally adjudicated finding.

## 7. A3 — Decide using the populated IR and original sources

**[A3: populated IR, prompt, final JSON and code](ARTIFACT_INDEX.md#a3--final-llm-decision)**



**Input:** original sources plus the A1 condition definitions and A2 evidence states, as rendered in the saved A3 prompt.

**Prompt objective:** check the source evidence and the intermediate states, identify decisive conditions and produce one of the three labels with reasons.

**Actual Qwen R2_L rep01 output:** `decision = NO`, citing condition a5 and policy E10. **Researcher Gold = NO** because £260 bought advertising services.

**Why it matters:** A1's Boolean expression is incomplete and A2 has a reference-relative status error, yet A3 returns the correct final label. This is **observed non-propagation / error absorption**. Since A3 also reads original policy and evidence, we cannot prove that the IR itself caused the correct decision.

## 8. B1 — Direct decision without persistent IR

**[B1: identical source inputs, direct prompt, final JSON and code](ARTIFACT_INDEX.md#b1--direct-baseline)**



**Input:** the same four original source components.

**Prompt objective:** identify relevant rules, match them to the evidence and produce the final decision **in one LLM call**. No explicit, inspectable condition/state table is retained.

**Actual Qwen R2_L rep01 output:** `NEED_MORE_INFORMATION`, while its justification acknowledges the £260 advertising-service transaction and the E10 exclusion. **Researcher Gold = NO.** The model recognised the decisive rule but failed to apply it consistently to the final label.

## 9. What counts as correct?

- **Final decision:** exact match with researcher-derived YES / NO / NEED_MORE_INFORMATION.
- **A1 condition coverage:** did the model extract relevant conditions? **Separate from Boolean logic correctness.**
- **A1 logic correctness:** does `eligibility_expression` combine conditions correctly?
- **A2 status accuracy:** are individual states correct, including negated-condition polarity and cited evidence?
- **Missing-information quality:** for R3, did the model name the **right missing facts**, including correct tax year and declaration coverage? The NEED_MORE_INFORMATION label alone is insufficient.
- **Error propagation/absorption:** track each intermediate discrepancy jointly with the final outcome; do not infer a causal propagation rate from stage-wise averages.


## Verified original Qwen R2_L rep01 JSON outputs (now on GitHub)

These are **parsed model outputs**, not the raw response or the rendered prompt. They are linked for direct inspection; the corresponding exact prompts and raw response logs remain pending upload.

- [A1 — actual parsed JSON](../runs/qwen_nothink_v7_rep01/R2_L/A1_parsed.json)
- [A2 — actual parsed JSON](../runs/qwen_nothink_v7_rep01/R2_L/A2_parsed.json)
- [A3 — actual parsed JSON](../runs/qwen_nothink_v7_rep01/R2_L/A3_parsed.json)
- [B1 — actual parsed JSON](../runs/qwen_nothink_v7_rep01/R2_L/B1_parsed.json)

## 10. What a reviewer should be able to click

For **every** run, the repository release must contain: `A1_PROMPT.txt`, `A1_raw.txt`, `A1_parsed.json`, equivalent A2/A3/B1 files, run metadata, provider request/attempt logs, source input and **post-run** Gold comparison. The raw outputs must not be replaced with cleaned summaries.

**Release gate:** The complete run files are not yet uploaded here. This document explains the workflow, but the repository is not yet a complete reproducibility archive. Original failed API attempts and successful retries must remain separately identifiable.

## 11. Limitations

One original benchmark item, researcher-synthesized evidence, 13 selected policy passages, researcher-formalized gold, bundled Low/High manipulation, unequal call counts, nonzero model stochasticity, and a small sample. Strong final-label performance does not imply error-free intermediate reasoning.
