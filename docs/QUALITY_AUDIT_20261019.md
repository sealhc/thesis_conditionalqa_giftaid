# Repository quality audit — 2026-10-19

**Scope:** live GitHub review of README, step-by-step guide, artifact index, policy data, saved Qwen R2_L rep01 stage outputs, Claude R3_H failure/retry, reference files, code function names and summary CSV. This is a **targeted audit**, not independent review of every saved run.

## Verified

- The 4 canonical prompt templates and actual Qwen R2_L rep01 rendered A1/A2/A3/B1 prompts, raw outputs and parsed JSON are in the repository.
- Original ConditionalQA dev-38, 13 selected policy passages, synthesized R1–R3 Low/High worlds, researcher Gold and experiment code are present.
- The original Claude R3_H rep03 A2/B1 records show a **technical API credit failure**, not an LLM decision. The separately archived `claude46_qorig_all_retry_rep01/R3_H` contains successful A3 and B1 JSON, both returning NEED_MORE_INFORMATION with the appropriate 2025–26 tax-year and declaration questions.
- `scripts/pilot.py` contains `format_shared`, `format_a2`, `format_a3`, `parse_json_envelope`, `schema_validate`, `prepare`, `record`, `audit_run`, and `summarize_runs`.
- In Qwen R2_L rep01, A1 defines 8 atoms but its formula only references a1; A2 marks a5 FALSE (advertising-service exclusion) and a1 TRUE (researcher reference UNKNOWN); A3 returns NO; B1 returns NEED_MORE_INFORMATION.

## Corrections made to reviewer documentation

1. Removed obsolete “files not uploaded” statements and activated links to the real files.
2. Corrected the **illustrative** Boolean JSON grammar: the actual A1 schema uses `{"op":"ALL","args":[...]}`, **not** a lowercase `{"and":[...]}` key.
3. Clarified that a correct R3 final label does not establish correct missing-information identification.
4. Clarified that original `reports/decision_by_run.csv` preserves the **failed** Claude rep03 row and does not itself contain the successful retry as a replacement. The retry is separately traceable, so final 18/18 counts must document the substitution.
5. Linked the exact run-stage artifacts and implementation paths for reviewer inspection.

## Open integrity and validity issues — do not conceal

**A. SHA-256 manifest mismatch:** The release `MANIFEST_SHA256.txt` was produced from the packaged files, whereas the GitHub root retained some pre-existing versions (`.gitignore`, `data/policy_dev38_13.json`, README and some docs). Thus the manifest is **not a trustworthy assertion that every current GitHub working-tree file matches**. Do not claim `python3 scripts/check_release.py` passes against the current GitHub checkout without rerunning and reconciling the manifest. Preserve the original manifest as an **archive-origin record**, then generate a separately labelled repository-state manifest if needed.

**B. Original run CSV:** `reports/decision_by_run.csv` contains the failed `claude46_qorig_all_rep03/R3_H` row and may not include the successful retry row. For 18/18 reported Claude accuracy, count the successful retry once, not the failed attempt. A revised analysis-ready table should be produced without modifying the original CSV.

**C. Gold validation:** The 3-class decisions and intermediate reference states are researcher-defined, not native ConditionalQA labels. Date-specific recognition and evidence thresholds require domain review.

**D. Treatment interpretation:** Low/High changes several evidence-presentation properties together. A/B differs in call count and prompts as well as explicit IR. Results are exploratory and cannot isolate a pure causal IR effect.

**E. Model output consistency:** A1 prompt says to keep atoms atomic and represent positive propositions, but the observed Qwen A1 contains a composite a1 and negated exclusion atoms. Those are *model behaviors*, not evidence that the prompt requested the exact eight-atom decomposition.

## Release readiness

**Auditable pilot artifact: yes, with explicit limitations.**
**Publication-ready, independently validated reproducibility release: not yet.**

Before claiming the latter: reconcile and rerun SHA-256 verification; produce a retry-aware decision table; validate all internal links and all run folders programmatically; obtain independent Gold review; record test outputs and environment/dependency versions.
