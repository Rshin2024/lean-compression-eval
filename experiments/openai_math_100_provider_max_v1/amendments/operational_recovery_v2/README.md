# Provider-maximum run: natural-exit recovery and one resume

This version supplements the preserved
[first recovery archive](../operational_recovery_v1/README.md). It records the
19 original Fable disconnects before streaming, 3 natural Sonnet provider
timeouts, and the executed one-use handoff for the resulting **30 original
failures**. The earlier 8 terminal failures remain documented in version 1.

The root operator authorized one replacement per exact original within the
user's separate full-run request and unrestricted-cost instruction. These were
not direct human approvals of individual attempt IDs. All original uncertain
charges remain `null`, not zero. New uncertain replacement outcomes do not gain
another retry authorization. The release helpers and resume watcher themselves
make no provider calls.

## What actually ran

- The 19 Fable and 3 Sonnet release receipts attest one release each, retaining
  the original claims, transport/stream evidence hashes, partial-output hashes
  where applicable, and unknown billing.
- The original generation child exited naturally. A metadata-only manifest
  ctime change from a report hardlink test had triggered its strict cache guard;
  its remaining provider threads continued until they ended. Source and manifest
  bytes remained unchanged. No provider process was signalled or cancelled.
- The superseded eight-attempt watcher was retired after identity and
  publication-race checks. Its retirement receipt is included.
- The final thirty-attempt watcher validated all **9,570 other saved
  generations**, zero in-flight markers, the exact released set, the parent's
  reaped-child state, and unchanged source/manifest bytes. It published exactly
  one existing parent resume control. The publication and activation receipts
  are included. Replacement results belong to the run, not this source snapshot.

The intermediate 27-attempt plan was **never activated**. Proposed interruption
of four silent Sonnet streams was **cancelled and unapplied** when one response
completed naturally after about 58 minutes; that saved response is excluded
from the retry set. The other three then ended with provider timeout events.
Silence was not converted into an invented terminal result.

The unchanged text-only reconciliation watchdog was restarted after checking
its process and exclusive lock. Its versioned restart receipt preserves the
original activation/log references. It executes no tools and makes no model
calls; a complete saved replacement response must never be repeated merely
because its original-failure prefix requires a separate reconciliation review.

## Archive boundaries

`source-hash-manifest.json` records all copied file hashes and their original
workspace paths, and links the unchanged version-1 manifest. Included files are
reviewed helper/test sources, proposals, release/activation/publication receipts,
root reviews, and hash-only baseline/final generation snapshots.

Raw SSE, partial answers, provider captures, credentials, SSH material, runtime
logs, and bulk run records are excluded. Omitted evidence remains locally
preserved; relevant file hashes are recorded without its text. The archive does
not independently authorize another run or another resume. Main experiment
documentation and refresh scripts were not changed by this archival operation.
