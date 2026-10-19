# Original failure and successful retry — verified 2026-10-19

The original `claude46_qorig_all_rep03/R3_H` ended in a configuration/API-credit failure after A1. Preserve this original failed run unchanged.

The separate `GiftAid_dev38_QVARIANT_ALL.zip` archive contains a successful original-question retry at `runs/claude46_qorig_all_retry_rep01/R3_H/` with full A1, A2, A3, B1 prompts, raw responses, parsed JSON, API request/response records and metadata. Both A3 and B1 returned `NEED_MORE_INFORMATION`, matching the researcher final Gold.

The uploaded English summary workbook `Run Details` explicitly records both the failed `claude46_qorig_all_rep03` and the successful `claude46_qorig_all_retry_rep01`. Therefore the missing result is not permanently missing. The original failure is not scored as a completed decision.

**Cohort counting:** When reporting three successful original-question repetitions, use the successful retry in place of the failed original run, and document this substitution. Keep both folders and do not silently overwrite or double count. The exact model version and settings must still be checked from `run_metadata.json` and API requests when making strict configuration-equivalence claims.

The original v9.9 code suite previously passed 94 offline unit tests. Unit tests are not independent validation of policy Gold.
