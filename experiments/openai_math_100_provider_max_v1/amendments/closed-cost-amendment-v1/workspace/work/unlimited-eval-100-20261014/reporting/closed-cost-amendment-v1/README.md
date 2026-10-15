# Closed cloud-cost display amendment v1

Prepared helper only; not activated. It never contacts a provider or Azure, runs Lean, modifies frozen source pins, rebuilds per-model reports, or changes original evidence. Root must review the helper before using it, after all six VM deallocation receipts and the final ledger are available.

The helper pins the successful full-cohort validation at `reporting/final-validation-v1/validation-20261015T044217.374393Z`, including its 324 input hashes. It verifies the original assembly and finalizer receipts against those retained copies. The only exception to unchanged inputs is the original mutable cloud ledger: its exact pre-closure bytes must be supplied separately and must match the original report's source hash. Every original report, question viewer, baseline artifact, frozen source and other bound input remains unchanged.

## Prepare after deallocation

First retain a byte copy of the closed ledger at a stable path; do not overwrite it later. This helper deliberately does not fetch prices or create lifecycle receipts. The closed ledger must retain all six original VM IDs, start times, regions, sizes, USD hourly rates, price sources and effective dates. It checks each end time against the matching `PowerState/deallocated` receipt and recomputes allocation hours and costs. A 0.00000001 USD tolerance only accommodates the existing ledger's floating-point serialization; it is not a billing tolerance. Unknown invoice components remain unknown.

From the workspace root, after substituting the actual stable closed-ledger path:

```sh
python3 work/unlimited-eval-100-20261014/reporting/closed-cost-amendment-v1/close_cost_display.py prepare \
  --baseline-ledger work/unlimited-eval-100-20261014/cloud/cost-ledger-history/20261015T041657.519311Z/after.json \
  --closed-ledger PATH_TO_RETAINED_CLOSED_LEDGER_JSON \
  --lifecycle-dir work/unlimited-eval-100-20261014/cloud/cleanup-plan/lifecycle
```

This creates `closed-cost-amendment-v1/application-v1/`, with ordinary byte copies of the original and proposed page/data, original receipts, validation evidence, both ledgers, helper source and all six lifecycle receipts. It writes the immutable intent last. It does not modify the live report. Root can inspect the exact proposal before applying it.

## Apply and recover

```sh
python3 work/unlimited-eval-100-20261014/reporting/closed-cost-amendment-v1/close_cost_display.py apply
```

Apply revalidates source/receipt/snapshot hashes, lifecycle arithmetic and the entire allowed transformation. It replaces only `comparison-data.json` and `index.html`, using an atomic replacement for each file. The pair is not a filesystem-wide atomic transaction: an interrupted process may leave one old and one new file. The retained intent records both exact states. Do not present an interrupted pair as final.

A lock prevents two helper applications. Rerunning the same apply command resumes only if each target is exactly its recorded before or after bytes and all guarded evidence is unchanged. Unknown target bytes, changed prompts/reports, missing receipts, changed source, changed rates or altered intent facts cause a refusal without overwriting the unexpected evidence. Error receipts and the intent are preserved. The final immutable `receipt.json` is atomically published only after both target hashes and all guards pass; a completed rerun verifies that receipt and does not rewrite the files.

The permitted data change is solely `cloudCosts`: the closed allocation estimate, source hash, completion status and `closureEvidence`. All other parsed fields must match exactly. HTML outside its JSON payload changes only by the explicit closure note and the label “Original assembly receipt.” The original `suite-build-receipt.json` and `finalization-status.json` remain byte-for-byte unchanged and continue describing the pre-closure page. The external amendment receipt contains the final page/data hashes. No new local-viewer route is needed; the existing comparison-data download contains the closure evidence.

A completed compute estimate is still not an invoice. Storage, IP, network, tax and credit charges remain unmeasured; API costs and the 30 uncertain original request charges are not changed. This helper neither deletes resources nor pauses monitoring. Browser QA and the user-facing completion record should reference the original assembly plus the new cost-closure receipt.

## Tests

`test_close_cost_display.py` uses isolated temporary byte-copy fixtures. It covers nonactivation, exact cloud-only edits, six lifecycle/rate guards, immutable receipts, idempotence, a crash between the two atomic writes and safe resume, a crash after the final receipt is fsynced but before publication (reusing its exact staged bytes and timestamp), altered inputs, duplicate identities, changed source, modified intent/snapshots, and rejection of fabricated receipt facts. It does not run on or mutate the actual report.
