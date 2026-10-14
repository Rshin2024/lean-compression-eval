# Explicit Lean thread-limit verification amendment

This archive records the separate `v2_threads1` verification phase for the OpenAI
Math 100 evaluation. At archive preparation, model generation and this verification
phase are running. **This source/metadata package does not establish completion
of the 9,600-attempt experiment or report a final model ranking.**

The original experiment source snapshot remains unchanged. Original question
prompts, eight generation manifests, attempt IDs, model settings, provider
responses and API cost records retain their original identities. The verifier
checks saved responses under a separately identified runtime; it does not ask
models to regenerate them.

## Runtime change and evidence

The only intended runtime filesystem change adds `-j1` before `--run` in the
Lean launcher. `build_runtime.py` requires the pinned original launcher, preserves
the original image layer chain, installs the exact changed wrapper in one new
layer, and checks all compiled-library/source-exclusion evidence again. The
build record retains the literal one-line diff and wrapper hashes.

The source runtime is
`sha256:07d4113f0330521740d2d9a3f97ef5f5d386b84b8d123119d97d1e05e3cebd29`.
The verification runtime is
`sha256:3889fa8d304ace87a58cc24fadb4fd68709c7f27167f75cf33736d9883114978`,
with verification environment hash
`4fd89c0042a31c59cdd991585254b77d47a2210e77f9f5f82b57411a414e7dc0`.
The Lean engine and Python evaluator remain frozen. Validation still permits two
CPUs, 8 GiB memory, 10,000,000 heartbeats and 180 seconds; extraction still permits
900 seconds. The cgroup PID limit remains 64.

The amendment follows a native checker/thread-limit incident: the original shell
setting did not impose the intended Lean thread cap. The explicit invocation is
applied uniformly in the second verification phase, rather than silently changing
resources for selected mathematical failures. Existing v1 records remain separate
and unchanged. `snapshot_verification_files.py` supports exact cloud/local file
identity checks; matching file hashes prove backup identity, not mathematical
validity.

The archived preflight reports **100 reference checks available for each of the
eight original runs** under the new runtime, with unchanged hidden reference
proof identities. It also records the source/run/schedule bindings. This preflight
is not completion of the model-output replay.

## Phase identity and reporting

`verify_runtime_v2.py` stores original `generation_identity` separately from the
new verification result, environment, configuration, policy and record identity.
It binds saved generation bytes, the original manifest and schedule, unchanged
reference proofs, the new runtime/reference index and scorer. Infrastructure
incidents remain incidents; they are not stored as terminal mathematical failures.

The supervisor is credential-free, performs no provider calls, and checks only
saved generations. The original authenticated generation controller continues its
already scheduled work. Replay does not repeat model requests or reprice captured
responses.

The phase-aware report adapter is included under `repository/reporting/`.
Primary v2 comparisons must use v2 rows and v2 reference anchors consistently.
They do not mix v1/v2 results or silently fall back to v1 references. Pending replay
stays pending. Original API cost estimates remain attached to original generation
records, independently of verification compute.

## Exact archive boundaries

`amendment-manifest.json` records every copied file's SHA-256, byte length and
workspace source path. Source copies preserve their original relative layout:

- `workspace/work/full-eval-100/` contains the reviewed verifier, supervisor,
  runtime recipe, offline tests, diagnostic source, live monitor source, and
  allowed bounded runtime/build/environment/preflight metadata.
- `repository/reporting/` contains the reviewed phase-aware reporting source.
- `package_amendment.py` contains the explicit source/metadata allowlist and
  refuses source drift, credential patterns, unexpected proof/response/graph
  fields in metadata, and replacement of a different existing archive.

The original source archive and its manifest are not rewritten. The original
manual-recovery/watchdog amendment remains a separate sibling archive.

**The hidden `reference-index.json` is excluded**, along with hidden proof/source
records, full reference checks, diagnostic result bodies, dependency graphs,
provider responses/captures/reasoning, credentials, SSH/cloud access files,
container images and runtime/event/status logs. The runtime amendment retains
the excluded index's path and exact SHA-256; its contents remain in private
execution/backup storage. Environment metadata contains artifact paths and hashes,
not library source bodies. Diagnostic summaries retain outcomes/identities, not
captured model text.

Archived `verification-launch-v2.json` is historical reviewed launch metadata.
Copying or packaging it does not start any process or authorize new model calls.
This archive alone is not a self-contained executable replay bundle: the pinned
runtime, frozen generation manifests/responses and excluded reference index must
be restored independently and must match their recorded identities. If the index
must be rebuilt, all 100 reference checks must be regenerated under the pinned
verification policy and re-audited; never relabel another index or runtime with
these recorded hashes.

## Offline checks

The included verifier/supervisor/reporting tests use synthetic fixtures and
mocked checker operations. They require the repository's frozen Python package;
they do not need API credentials, provider requests or new model generations.
The separate source-manifest and reporting-freeze identities in the amendment
manifest identify exactly which tests and sources were copied.

Restore sources into their original workspace/repository locations before using
operational entry points, and review their required pinned launch/amendment files.
Do not indiscriminately run diagnostics over existing immutable evidence.
