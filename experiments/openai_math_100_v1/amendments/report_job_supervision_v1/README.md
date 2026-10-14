# Per-model report job supervision

This source-only amendment covers report production while the OpenAI Math 100
experiment is ongoing. It does not claim final cohort completion or create a
combined comparison. The frozen core, report adapter and verification reader are
unchanged. No provider requests, Lean checks, VM cleanup, or generation/evaluation
record changes are performed by these report controllers.

`report_jobs.py` is the supported controller/worker entry point. It allows at most
two report builds through shared OS locks. The historically named
`haiku_report_job.py` is its generic per-model backend. Builds require exactly
1,200 explicit terminal `v2_threads1` files matching the scheduled attempt IDs;
the pinned adapter performs the deeper complete-phase and original-record
validation. Report checks reconcile Decimal cost totals and preserve separate
generation/verification identities. Remote report files receive SHA-256 manifests;
local transfer uses staging, exact hashes, phase/amendment checks and atomic
publication. Existing report bytes are never overwritten to recover a failure.

The recovery fix adds a completion/hash check after acquiring a build slot and
another inside the backend's model lock. A queued duplicate therefore cannot
regenerate a completed report manifest. Completed local bundles are hash-checked
before being treated as complete. Missing or corrupt bytes produce an explicit
`completed_local_integrity_hold` for root repair, without deleting, downloading
over, or replacing those files. Restoring the exact expected bundle clears the
hold. A failed integrity check cannot make the local controller exit as complete.

Recovery remains coordinated by the parent task: inspect the private per-model
and controller checkpoints, fix the recorded cause, then restart the appropriate
controller or explicitly resume a failed cloud model through `report_jobs.py`.
Do not run the backend directly to bypass global build slots. Do not remove
checkpoints or completed reports merely to force a rebuild. Quiesce report
controllers before deploying source changes; generation and verification are
separate processes. Final cross-model validation and combined-report publication
remain explicit parent-task actions.

The five original offline tests passed independently. The reviewed version has
ten passing offline tests, including missing/corrupt local bundles, restoration,
controller integrity holds, queued duplicate handling, the backend lock guard,
the two-build cap, exact record counts, traversal protection and byte tampering.
Tests use temporary local fixtures and mocked builds/transfers.

The manifest binds exact reviewed sources and the exact original revision under
`historical/original/`. The original experiment snapshot is unchanged. No model
reports, graphs, raw responses, credentials, checkpoint/access files, process IDs,
logs, or live handoff data are included. Restore the source layout beside the
frozen repository before using operational entry points; this archive itself
starts nothing. Deployment state is retained only in the private task handoff.
