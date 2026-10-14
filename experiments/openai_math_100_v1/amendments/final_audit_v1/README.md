# Final cohort evidence audit

This archive contains the read-only auditor and its tests for the ongoing 100-question evaluation. It does not mean the 9,600-attempt run has finished. The original generation and verification sources and all records remain unchanged.

Restore the two files to their `workspace/` paths beside the existing experiment workspace. The auditor uses the frozen core package, model manifests, captures, generations, v2 record filenames, completed per-model reports, and the reviewed recovery helper from the `text_tool_stop_recovery_v1` archive. It writes only a fresh timestamped audit directory. It makes no model, Lean, or network calls and never acquires or releases attempt markers.

From the workspace root, after all 9,600 responses, v2 records, and eight local cost reports exist:

```sh
python3 work/full-eval-100/final-audit/audit_final_cohort.py --require-complete
```

Only `status: pass_complete` together with `cohort_complete: true` confirms this auditor's completion gate. A partial snapshot is never a full-run result. The audit retains generation/capture SHA maps and rehashes those exact files before success, so same-name file replacement cannot silently pass. Twelve offline tests cover the guards; an independent review approved the final version.

This audit counts v2 record filenames. It does **not** replace the separate validated phase-aware proof/metric reports, exact cloud/local file-hash comparison, or final combined-report and rendering checks.

Costs are estimates from reported token usage at frozen rates, including failed and truncated outputs. The received HTTP 520 failure had no reported token usage; any charge for that failed request remains unknown, never zero. Its subsequent single recorded response is priced normally. Infrastructure estimates remain separate from provider invoices.

Raw audit evidence, model responses, private references, access files, and credentials are deliberately excluded from this source archive. Retain the operational outputs locally for reproducibility.
