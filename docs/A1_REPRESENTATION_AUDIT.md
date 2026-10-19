# A1 representation audit — Qwen3-4B, R2_L, rep01

**Status:** Retrospective diagnostic annotation, 2026-10-19. The frozen A1 prompt, model output, code, and original reference Gold are **unchanged**. This document does not rewrite the observed run.

## Evidence

- [Actual A1 prompt](../runs/qwen_nothink_v7_rep01/R2_L/A1_PROMPT.txt)
- [Actual A1 parsed output](../runs/qwen_nothink_v7_rep01/R2_L/A1_parsed.json)
- [Actual A2 parsed output](../runs/qwen_nothink_v7_rep01/R2_L/A2_parsed.json)
- [Researcher reference conditions and logic](../gold/reference_conditions_and_logic.json)
- [Original policy passages](../data/policy_dev38_13.json)

## Three separate A1 evaluation dimensions

| Dimension | Observation | Judgment |
|---|---|---|
| Condition coverage | Eight model-generated atoms broadly cover the three requirements and five exclusions, but a1 also folds in individual-donor scope. | Coverage is not sufficient to claim structural correctness. |
| Atomicity | a1 states that the gift is from an individual **and** to a recognised charity/CASC. These are independently verifiable facts, although one composite status is assigned. | **Atomicity violation** relative to the prompt's “Keep atoms atomic” instruction. |
| Polarity | a4–a8 express *non-occurrence* of exclusion conditions (e.g., a5: “not a payment for goods or services”). | **Not inherently a logical error**. It is equivalent to negating the corresponding X condition, but does not follow the prompt's preferred positive wording literally. |
| Logical composition | `eligibility_expression` is `{"ref":"a1"}` although eight atoms were defined. | **Confirmed composition error**: a2–a8 are unused in the eligibility formula. |

## How the expected rule relates to the model's atoms

Researcher reference: `(C1 AND C2 AND C3) AND NOT (X1 OR X2 OR X3 OR X4 OR X5)`.

By De Morgan's law: `C1 AND C2 AND C3 AND NOT X1 AND NOT X2 AND NOT X3 AND NOT X4 AND NOT X5`.

The model's observed mapping is:

| Model atom | Model proposition (short paraphrase) | Reference relationship |
|---|---|---|
| a1 | individual gift to recognised charity | C1 **plus** donor-scope fact |
| a2 | sufficient donor tax | C2 |
| a3 | valid declaration | C3 |
| a4 | not Payroll Giving | NOT X3 |
| a5 | not goods/services payment | NOT X1 |
| a6 | benefit does not exceed threshold | NOT X2 |
| a7 | not shares | NOT X4 |
| a8 | not cards/vouchers | NOT X5 |

Given those atom meanings, the expected *composition* in the experiment's actual expression grammar would be:

```json
{
  "eligibility_expression": {
    "op": "ALL",
    "args": [
      {"ref":"a1"}, {"ref":"a2"}, {"ref":"a3"}, {"ref":"a4"},
      {"ref":"a5"}, {"ref":"a6"}, {"ref":"a7"}, {"ref":"a8"}
    ]
  }
}
```

This is **illustrative expected logic**, not native ConditionalQA Gold or an observed model output. The composite a1 should ideally be split into separate conditions in a future revised representation.

## Downstream consequences

- In A2, a1 is marked TRUE even though the researcher reference treats date-specific charity recognition as UNKNOWN. A composite atom **may** make this error more likely, but causation is **not established**.
- In A2, a5 is FALSE, correctly expressing that the payment was for advertising services and triggers E10.
- A3 returns NO correctly despite the incomplete A1 formula and a1 status discrepancy. This is observed **error non-propagation / absorption**, not proof that A3 executed or repaired the defective formula; A3 also sees original sources.

## Version-control decision

**Frozen pilot v9.9: do not edit** the original prompt, model responses, parsed JSON, Gold, or scoring code retroactively. Preserve these deviations as results.

**For a future v10 experiment only (not a patch to existing runs):**
1. Clarify that an atom must contain one independently verifiable fact, e.g. split individual-donor identity from charity recognition.
2. Specify whether exclusion atoms must be expressed as positive exclusion facts with explicit NOT in the expression, or as negated eligibility conditions. Both are logically legitimate if composed correctly.
3. Evaluate coverage, atomicity, polarity normalisation and Boolean composition separately.
4. Treat a revised prompt as a **new protocol version** with new run IDs and separately reported results. Do not mix its outcomes with v9.9 as though the prompt were unchanged.

**Interpretation:** The model generated the composite and negated atoms; the code did not preassign that exact eight-atom decomposition. The prompt constrained the task but did not guarantee compliance.
