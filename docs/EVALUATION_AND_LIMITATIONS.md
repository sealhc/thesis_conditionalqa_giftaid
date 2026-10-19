# Evaluation, error audit and limits

## Reference labels are researcher-derived

Native ConditionalQA dev-38 has conditional YES/NO branches. Our R1/R2/R3 final labels and intermediate reference states come from frozen researcher-authored [`gold/reference_conditions_and_logic.json`](../gold/reference_conditions_and_logic.json) and [`gold/gold_per_run.json`](../gold/gold_per_run.json). These are **not native per-stage benchmark gold** and have not received independent tax-law certification.

## Primary measures

1. **Final label accuracy**: exact match of `YES`, `NO`, or `NEED_MORE_INFORMATION`, excluding R0.
2. **Missing-information quality (R3)**: whether the response identifies the correct **decision-relevant unknown facts** — tax sufficiency for the appropriate year and a declaration covering this gift — without adding unnecessary documents, wrong tax years or already-resolved conditions. A correct label is **not sufficient**.
3. **A1 condition coverage**: automated matching to reference conditions; **does not measure Boolean-expression correctness**.
4. **A1 logic correctness**: separately inspect `eligibility_expression` for AND/NOT composition, unused atoms and incorrect references.
5. **A2 evidence-state agreement**: map model atoms to reference conditions, accounting for negated proposition polarity, then compare status and cited evidence. Composite atoms and date-specific recognition are sources of adjudication uncertainty.
6. **Decisive-condition accuracy**: did the model correctly handle the exclusion that settles NO, even when other conditions are unknown?
7. **Error absorption / propagation audit**: per-run joint table of upstream errors and final result; upstream error + correct final is *observed non-propagation*, not necessarily a causal recovery mechanism.

## Final-label counts — original question

| Synthetic scenario | Qwen A | Qwen B | Claude 4.6 A | Claude 4.6 B |
|---|---:|---:|---:|---:|
| R1_L | 3/3 | 3/3 | 3/3 | 3/3 |
| R1_H | 3/3 | 3/3 | 3/3 | 3/3 |
| R2_L | 2/3 | 0/3 | 3/3 | 3/3 |
| R2_H | 0/3 | 0/3 | 3/3 | 3/3 |
| R3_L | 2/3 | 3/3 | 3/3 | 3/3 |
| R3_H | 3/3 | 3/3 | 3/3 | 3/3 |
| **Total** | **13/18** | **12/18** | **18/18** | **18/18** |

These are 18 executions per pipeline per model, **not 18 independent benchmark cases**. R0 is separate.

## What the results actually support

- Qwen's net A–B difference is **1/18**, small and mixed in direction; the study cannot establish a statistically significant IR benefit.
- R2_L: direct B returns NEED_MORE_INFORMATION in 3/3 despite Gold NO; in an audited B run it recognizes E10 but does not apply it to the label.
- R2_H: both Qwen arms fail 3/3. Low/High effects are not monotonic across R1–R3, and several complexity features vary together.
- R3: final label correctness can hide wrong-year tax questions and unnecessary requests. **Missing-fact scoring must be reported separately**; the frozen automated run labels do not constitute a complete human-audited missing-fact score.
- Spreadsheet-reported Claude 4.6 18/18 on final labels indicates a ceiling for this small set, not absence of intermediate errors.
- Example A1/A2 errors may be absorbed by A3 because it sees original sources. That mechanism remains a hypothesis pending interventions that isolate IR access.

## Avoid misleading claims

- Do not claim 100% A1 condition coverage implies a correct decision formula.
- Do not label every automatic status disagreement as an independently verified error.
- Do not report a causal *propagation rate* from stage-wise averages alone.
- Do not claim external validity from one ConditionalQA item or synthesized evidence.
- Do not treat the researcher decision reference as a current HMRC legal ruling.
- Do not pool different question wordings, model versions, inference settings or schema modes.

**Archive completeness:** See [missing raw-run audit](ARCHIVE_COMPLETENESS.md).

**Successful original-question retry:** see [`ARCHIVE_COMPLETENESS.md`](ARCHIVE_COMPLETENESS.md).
