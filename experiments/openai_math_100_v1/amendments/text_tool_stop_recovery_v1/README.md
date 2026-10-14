# Text-only tool-stop operational amendment

This source-only amendment sits **outside the frozen evaluator core**. It does not
replace the original experiment source manifest, run manifests, model prompts,
prices, Lean engine, references, or already recorded generations/evaluations.

The frozen Anthropic parser holds a captured `tool_use` response as unknown. A
small number of observed responses contain only text/thinking blocks, with a
serialized `<invoke>` request in the text. The recovery helper recognizes this
one representation and stores **every text block verbatim**, joined by the frozen
parser's newline convention. It preserves the complete provider response and
usage, records the original stop reason, and uses the frozen pricing path. It
executes no tool, makes no API request, selects no proof from the text, and never
assigns a Lean verdict. The ordinary unchanged validator decides the outcome.

The original manually applied helper is preserved byte-for-byte under `historical/`
in the archived workspace. Its three recovered generation records remain
immutable. The hardened v2 helper also supports read-only verification of those
v1 records against their exact original helper hash and captured evidence.

## Narrow evidence rule

A recovery requires a frozen Anthropic single-shot slot; an abandoned marker
locked exclusively; exactly one capture; intact base64/body SHA-256; matching
attempt, transport index, request ID, captured path, model, and frozen request
hash; and exactly `request_started`, `response_received`, `outcome_unknown` with
the known unsupported-stop diagnostic. Marker/request/capture/response timestamps
must be ordered. Structured tool blocks, other stops, ambiguous retries or logs,
live locks, altered records, and incomplete evidence remain held. Captured XML is
never executed or treated as proof text to extract.

The watchdog additionally requires the reviewed launch-plan/helper/watchdog
hashes, unchanged frozen core/Lean hashes and run manifests, fresh blocked-shard
status, a matching latest child error and transport tail, and no other unresolved
calls in that shard. It rechecks state immediately before publishing an atomic,
non-overwriting `resume_shards.json`. Existing root requests are preserved. A
matching pending or applied handoff prevents duplicate publication after restart.
The watchdog itself makes no model/tool calls; the existing controller may resume
later, already authorized scheduled work after consuming the handoff.

A single OS file lock protects the watchdog. Marker locks protect reconciliation.
The 60-second default poll avoids repeatedly hashing all manifests at short
intervals. Provider credential variables are discarded inside the watchdog.
Failures produce held diagnostics rather than releasing unknown calls.

## Tests and review evidence

18 offline tests use temporary synthetic runs and captured responses only. They
cover full raw text/provider usage preservation, hashes and transport/timing
bindings, wrong stop/block rejection, live locks, dry-run nonmutation, immutable
idempotence, stale-marker crash recovery, partial logs, changed blocked epochs,
other unresolved shards, non-clobbering handoffs, and crashes before/after atomic
publication. Network calls are forbidden in the recovery tests.

From the original workspace:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s work/full-eval-100 -p 'test_*text_tool_stops.py' -v
```

The parent task independently reran all 18 tests and reviewed the final actual dry
run before starting the pinned watcher. Operational launch/status/audit logs and
all provider responses remain in the private workspace and are intentionally not
copied into this source archive. The manifest records the exact reviewed hashes.
No helper changes are allowed after adoption without a new versioned amendment
and explicit compatibility for any already recorded helper hash.
