# ConditionalQA dev-38 — Gift Aid Pilot

## START HERE — for Pauline and research supervisors

**[Read the full step-by-step experimental walkthrough](docs/STEP_BY_STEP_EXPERIMENT.md)**

This guide explains the **four shared inputs**, why we synthesized YES/NO/NEED_MORE_INFORMATION cases, **exactly what A1, A2, A3 and B1 do**, what JSON each produces, the meaning of `atom`, `ref` and `eligibility_expression`, the intermediate Gold comparisons, and how the observed errors affected the final decision.

**Five-minute orientation:** We compare Pipeline A (**A1 builds condition structure → A2 fills evidence into that same structure → A3 decides**) against Pipeline B (**B1 decides directly**) using the same policy, original background, additional donation evidence and question. A1+A2 form **one** intermediate representation. The original ConditionalQA Gold is conditional; our concrete decisions and intermediate references are researcher-derived.

**Audit status:** Detailed documentation is now online. The complete raw-run archive and code are **not yet uploaded**; do not interpret this README as proof of full reproducibility.

---


**Exploratory single-case research; not tax advice.** This repository is being prepared for a fully auditable research release. The original ConditionalQA answer is conditional; the specific YES/NO/NEED_MORE_INFORMATION outcomes and intermediate reference states are researcher-derived.

## Research question

Does one explicit, inspectable **condition-based intermediate representation (IR)** affect decision accuracy, missing-information quality and error propagation compared with a direct LLM answer?

Both pipelines receive exactly 4 source components: **13 ConditionalQA-provided policy evidence passages (E01–E13)**, **original charity background**, **researcher-synthesized donation evidence**, and the **original question** (“Am I eligible for Gift Aid?”). The 13 passages are selected benchmark evidence, not the entire GOV.UK policy.

```text
Pipeline A: INPUT → A1 (define conditions and Boolean logic)
                  → A2 (populate the same conditions with case evidence)
                  → [one complete explicit IR]
                  → A3 (LLM final decision) → OUTPUT

Pipeline B: SAME INPUT → B1 (direct LLM decision) → SAME OUTPUT LABELS
```

A1 and A2 build **one** IR in 2 calls, not 2 IR layers. A3 and B1 both use an LLM for the final decision. The arms differ in prompt workflow and call count as well as explicit IR.

## Scenarios

| Case | Evidence presentation | Researcher-derived final gold |
|---|---|---|
| R0 | Original dev-38, no L/H | Native conditional answer; not included in 3-class accuracy |
| R1_L / R1_H | Elaine £120, direct vs distributed evidence | YES |
| R2_L / R2_H | Robyn £260 advertising-service payment | NO |
| R3_L / R3_H | Devon £95, tax/declaration unresolved | NEED_MORE_INFORMATION |

Low/High vary length, evidence distribution, indirectness and distractors together; they do not independently manipulate formal rule complexity.

## Original-question results (3 repetitions per synthetic presentation)

| Case | Qwen3-4B A | Qwen3-4B B | Claude Sonnet 4.6 A | Claude Sonnet 4.6 B |
|---|---:|---:|---:|---:|
| R1_L | 3/3 | 3/3 | 3/3 | 3/3 |
| R1_H | 3/3 | 3/3 | 3/3 | 3/3 |
| R2_L | 2/3 | 0/3 | 3/3 | 3/3 |
| R2_H | 0/3 | 0/3 | 3/3 | 3/3 |
| R3_L | 2/3 | 3/3 | 3/3 | 3/3 |
| R3_H | 3/3 | 3/3 | 3/3 | 3/3 |
| **Total** | **13/18** | **12/18** | **18/18** | **18/18** |

The original Claude R3_H rep03 attempt failed because of insufficient API credits. A successful **original-question retry** was subsequently saved under `claude46_qorig_all_retry_rep01/R3_H` with A3 and B1 both returning NEED_MORE_INFORMATION. The failed and successful attempts must both be retained, not overwritten.

## Audited example: Qwen R2_L rep01

- **A1** extracted 8 policy conditions, but its `eligibility_expression` was only `{"ref":"a1"}`. `ref` means a pointer to a named condition. The formula wrongly omitted 7 extracted conditions. The researcher reference requires C1/C2/C3 and none of X1–X5. With the exclusion atoms already negated, De Morgan's law gives an AND of the 8 atom propositions (with a1 also including individual-donor scope).
- **A2** marked a5 (“not payment for goods/services”) FALSE, correctly recognizing that £260 purchased advertising services. It marked a1 TRUE, although the researcher reference is UNKNOWN under the date-specific recognition criterion.
- **A3** returned NO, correctly citing E10; the intermediate defects did not change the final label. This is observed error non-propagation, not proof of how A3 repaired or bypassed the errors.
- **B1** returned NEED_MORE_INFORMATION despite identifying the advertising-service exclusion in its reasoning. The final label was wrong.

R3 evaluation must assess the *correct missing facts* (tax sufficiency for the appropriate year and valid declaration coverage), not merely the NEED_MORE_INFORMATION label.

## Sources and release status

- [ConditionalQA benchmark](https://github.com/haitian-sun/ConditionalQA) · [ACL 2022 paper](https://aclanthology.org/2022.acl-long.253/) · [GOV.UK Gift Aid](https://www.gov.uk/claim-gift-aid)

**Publication status:** This README documents the study, but the complete frozen code, raw runs, rendered prompts, gold references and checksums are **not yet uploaded to this GitHub repository**. Do not cite this repository as a complete reproducibility archive until those files are uploaded and verified.
