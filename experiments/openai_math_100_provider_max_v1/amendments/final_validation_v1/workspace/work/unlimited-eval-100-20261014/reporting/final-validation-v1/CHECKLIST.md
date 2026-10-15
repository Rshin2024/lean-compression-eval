# Provider-maximum rerun final validation

Status: prepared, not activated. The original 8,192-token experiment is a separate immutable baseline. This validator does not build a report, request a response, execute Lean, resolve a recovery hold, alter evidence, contact a provider/cloud API, or authorize cleanup.

## Activation gate

1. Root reviews the prepared source and its SHA-256 manifest.
2. The existing finalizer reaches `complete`; every one of the eight report jobs is `complete_synced_verified`.
3. The finalizer's hash-bound full streaming audit is `pass_complete`, with 9,600 distinct provider response IDs, 9,600 priced recorded responses, and eight complete model audits. The validator refuses before creating any output if these conditions fail.
4. Only then run `python3 work/unlimited-eval-100-20261014/reporting/final_validation_v1.py`. It creates a fresh timestamped directory immediately below `reporting/final-validation-v1/`. An existing output directory is never reused. A failed validation writes a failure receipt there and requires review; it does not change inputs or resume any work.

## Independent checks performed

- Reconstruct all eight schedules: 100 exact question IDs, three conditions, four samples, 1,200 records per model, 9,600 globally unique new attempt IDs, no overlap with the old attempt IDs.
- Bind pinned model IDs, high effort and the 128,000-token provider ceiling. This is a finite output cap, not unlimited generation.
- Match all 9,600 frozen message arrays and prompt hashes to both the old experiment and the 300 unique question/prompt viewer entries. The viewer exports actual frozen messages only.
- Verify all report file manifests and every listed artifact hash; check complete phase-separated records, generation hashes, verification policy hashes and uniform checker limits. Full verification-file hashes are checked against the completed streaming audit's file snapshot; the validator does not reread every large graph.
- Remeasure submitted/partial source lengths from the report exports. Independently classify completed nonempty submissions, accepted proofs, truncated responses and empty responses. Empty/truncated totals can overlap and are not added together. Reconcile all population totals with the dashboard's pure classification module. Completed-submission acceptance, scheduled solve rate and completion rate remain distinct.
- Recompute exact per-question geometric-mean comparisons with Python rational products and cross-powers for T0, I0 and W_B, all three prompt conditions, and coverage gates 0, 1, 25 and 50. Compare all 36 cases with the dashboard's JavaScript implementation. Ties use midranks; question weights are equal; W_B ranks smaller values first. This check does not add authority to the exploratory J0 metric or infer performance on unmeasurable questions.
- Independently recompute all recorded-response token costs with Decimal arithmetic and frozen prices. OpenAI costs follow each response's delivered Fast/priority or default tier; reasoning tokens are not charged twice. Anthropic cache read/write and TTL buckets, and applicable input-context thresholds, are preserved.
- Require precisely the reviewed 30 distinct original uncertain-charge incidents: 19 prestream disconnects, three natural provider timeout errors, six provider API errors, two overload errors, and zero operator abandonments. Bind incident details to the complete streaming audit and display the correct one-based sample identity. Original charges remain unknown, not zero; the recorded-response estimate is not an all-request or invoice total.
- Verify the old experiment's manifest and report-bundle hashes plus the four existing baseline viewer/data artifacts. Deep rehashing of the old raw graph backup is explicitly outside this validator's scope.
- Require all report/audit source pins except the separately reviewed viewer-only delta. The local `serve_comparison.py` amendment requires the exact root-review record binding old/new source and amendment hashes. Original source pins are not rewritten or superseded, and no cloud deployment is authorized by this exception.
- Rehash all bound inputs at the end. Retain byte-copy receipts and exact rank evidence in the new validation directory. No hard links are used.

## Separate delivery and preservation gates

Root still owns browser rendering/interaction QA, cloud/local raw evidence backup and archive hash checks, preserved original streams for all uncertain outcomes, stop/deallocation/deletion receipts, user-facing completion, and automation pause. The completed streaming audit checks request/capture/SSE/generation bindings and byte stability; the phase-aware report adapter checks proof graph/provenance correctness. This extra validator is a complementary report/metric check, not a substitute for either.

The cohort uses the same image and nominal per-proof limits but different physical CPU models across hosts. Wall-timeout outcomes may depend on host speed. Do not present wall-time or cloud expenditure as a controlled model speed comparison.

## Deferred closed-cloud-cost display amendment

The live pinned suite builder currently sets `cloudCosts.computeComplete` to false. An open allocation ledger is acceptable at the pre-cleanup validation stage. Do not edit or rerun that live pinned builder to hide this limitation.

After full report/audit/backup gates and root-authorized cleanup, a separate versioned helper may update only `cloudCosts` plus a narrowly specified HTML closure note and the label “Original assembly receipt.” Preserve the original `suite-build-receipt.json` and finalizer receipt byte-for-byte. Keep before/after ordinary byte copies and an external immutable amendment receipt containing final data and HTML hashes.

The amendment must bind each VM deallocation receipt to its exact resource ID, `PowerState/deallocated`, and observed deallocation time; bind the closed ledger and lifecycle evidence; verify every allocation has an end time and every VM rate is known; and preserve the already frozen hourly rates rather than silently substituting new quotes. It may set `computeComplete` true only after these facts are established. Disk/storage/IP/network/tax/credit charges remain unknown where unmeasured, and `totalUsd` remains null.

Add `closureEvidence` inside `cloudCosts` with source/lifecycle/helper and before-image hashes. Prove canonical equality of every other data field and exact equality of prompts and all old/new report files. Record HTML differences outside the designated payload/note explicitly. Final delivery links both the original assembly receipt and the cost-closure amendment. A finalizer receipt that bound the pre-cleanup page must not be misrepresented as binding the amended page.
