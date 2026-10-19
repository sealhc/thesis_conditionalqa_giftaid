# Study design, assumptions and provenance

## 1. What is being studied?

ConditionalQA (Sun et al., ACL 2022) contains questions, scenarios, policy documents, conditional answers and evidence annotations. Our source is **one item, dev-38**, about a small charity and Gift Aid. The original question is exactly: **“Am I eligible for Gift Aid?”** The original benchmark gold gives conditional branches, not a definite verdict on a particular payment.

**Our extension:** We keep the original question and charity background fixed, use 13 dataset-provided evidence passages as the fixed policy input, and write controlled *additional information* for individual payments. We construct three outcomes: R1 YES, R2 NO, R3 NEED_MORE_INFORMATION, each in Low/High evidence-complexity presentations. R0 is original and evaluated separately.

## 2. Four input components and provenance

1. **Policy evidence (fixed):** [`data/policy_dev38_13.json`](../data/policy_dev38_13.json). These 13 passages come from ConditionalQA's human evidence annotations and were numbered E01–E13 by our experiment package. They are **not** the full policy webpage, and not necessarily the same input sections used by every previous paper.
2. **Original case background (fixed):** [`data/official_dev38_original.json`](../data/official_dev38_original.json).
3. **Researcher-synthesized additional information (varied):** [`data/scenarios_R0_R4.json`](../data/scenarios_R0_R4.json); exact rendered model input in [`model_inputs/`](../model_inputs/).
4. **Question (fixed for this cohort):** “Am I eligible for Gift Aid?” The v9.9 source package also includes a *synthetic reworded question*; those runs must be kept in a separate cohort. The saved `*_PROMPT.txt` files, not assumptions about templates, determine what was actually sent.

Native ConditionalQA gold and researcher reference labels **must not be included in model prompts**. The model may receive the policy evidence and case facts, but not our answer key.

## 3. Decision semantics

- **YES:** all applicable necessary conditions are established and no exclusion applies.
- **NO:** at least one decisive exclusion is established (e.g., payment for advertising services), even if other non-decisive facts are unknown.
- **NEED_MORE_INFORMATION:** the available evidence does not settle eligibility; the system should identify *which decision-relevant facts* must be established, not merely return a label.

Reference policy rule: `(C1 AND C2 AND C3) AND NOT (X1 OR X2 OR X3 OR X4 OR X5)`. De Morgan's law gives `C1 AND C2 AND C3 AND NOT X1 AND ... AND NOT X5`. This is a **researcher-formalized reference**, not native executable benchmark gold. Some real policy subtleties and exceptions may be outside the 13 snippets.

For R2, the £260 was an agreed payment for grooming-salon advertising. The E10 service-payment exclusion makes the decision NO even if tax and declaration are unchecked. For R3, a valid declaration covering the £95 gift and sufficient qualifying tax for the relevant 2025–26 tax year are unresolved. A model should not demand a particular form (e.g., P60) when the required *fact* can be established otherwise.

## 4. Evidence complexity

- **Low:** one relatively direct paragraph, with facts explicitly related to policy conditions.
- **High:** multiple records, distributed and indirect clues, possible distractors, longer text; the same intended gold decision.
- **Not controlled separately:** word count, document distribution, ambiguity and distractors all change together. Thus Low/High is a *bundled evidence-presentation treatment*, **not a validated independent measure of formal logical depth**.

## 5. Experimental factors

- Pipeline: A (3 LLM calls, one IR constructed in A1+A2) vs B (1 LLM call).
- Model: Qwen3-4B-Instruct (local Ollama, `think=false`) vs Claude Sonnet 4.6 (API).
- Outcome: R1 YES, R2 NO, R3 NEED_MORE_INFORMATION.
- Presentation: Low/High.
- Repetition: 3 per model × presentation × pipeline, with original question.
- R0: original benchmark question; not pooled into synthetic accuracy.

**Confounds:** A has more calls and different prompts; any A/B difference is a *pipeline effect*, not isolated causal proof of IR alone. A3 can re-read the original policy/case and may repair earlier IR errors. Claude 18/18 indicates ceiling on final-label accuracy in this pilot, not universal correctness.

## 6. Integrity and assumptions

- Original dataset provenance: [ConditionalQA GitHub](https://github.com/haitian-sun/ConditionalQA) and [ACL 2022 paper](https://aclanthology.org/2022.acl-long.253/).
- Public policy source: [GOV.UK Claim Gift Aid](https://www.gov.uk/claim-gift-aid).
- This is an **exploratory synthetic single-case pilot**, not a population estimate, not a tax ruling and not a preregistered confirmatory study.
- Gold status for HMRC recognition on the payment date is researcher-defined; an existing recognition letter without proven date-specific validity is classified UNKNOWN in the frozen reference. Independent domain review is still needed.
- Do not change prompts, schemas, source policy, model version or question wording within a cohort; preserve changes as new versions.
