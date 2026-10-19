# Reproduction and integrity runbook

## What is included

The package contains the **unaltered v9.9 experiment code/data/gold/templates**, plus available original-question run records copied byte-for-byte from the saved `runs(1).zip` archive. This documentation is new; the archived model outputs are not regenerated or edited. The raw artifacts allow **audit of the observed run** independently of whether exact model outputs can be reproduced.

## Offline verification (no API keys required)

```bash
python3 scripts/check_release.py
python3 -m unittest discover -s tests -v
python3 checks/validate_lowhigh.py
```

Run commands in the repository root. The first command verifies file checksums, the four stage prompts and parsed JSON in the saved paired run, source/gold separation, and the published per-run decision table. The original tests may require packages specified by imports in the v9.9 scripts; install only into an isolated virtual environment. Do not treat successful offline unit tests as a model reproduction.

## Running the original experiment code

Start by inspecting the original v9.9 [`scripts/`](../scripts/) and [`prompts/`](../prompts/) (especially `pilot.py`, `ollama_run.py`, `api_run.py`, `stage_schemas.py`). Model providers differ in API configuration; do not claim a single command reproduces all runs. Use a **new run directory** and do not overwrite the archived `runs/`.

For Qwen, inspect [`run_metadata.json`](../runs/qwen_nothink_v7_rep01/R2_L/run_metadata.json) for exact model digest, Ollama settings and stage schemas. A local tag with the same name is **not sufficient** to prove model identity. For Claude, use each saved run's model identifier, API format and parameters from its metadata. Preserve exact prompts and responses; record failures and retries without replacing raw text.

The v9.9 README and scripts are preserved as original experiment artifacts; this reviewer guide does not silently rewrite their behavior. Verify CLI help and provider dependencies before running any API call.

## Reproducibility constraints

- Original question is frozen for the published 36 spreadsheet-reported paired synthetic pipeline executions (35 original completed folders plus 1 successful retry folder) (6 presentations × 3 repeats × 2 models). R0 is separate.
- A/B use the same 4 original inputs but different prompt workflows and call counts.
- Stage JSON Schema constrains **format**, not factual or logical correctness.
- Do not feed reference gold or post-run comparisons into a model prompt.
- `runs/` includes exact prompts and original raw/parsed responses; review `*_stage_attempts.json` and provider response metadata for technical retries.
- New reruns must have new cohort identifiers, version manifests and raw response preservation.

**Archive completeness:** See [missing raw-run audit](ARCHIVE_COMPLETENESS.md).

**Successful original-question retry:** see [`ARCHIVE_COMPLETENESS.md`](ARCHIVE_COMPLETENESS.md).
