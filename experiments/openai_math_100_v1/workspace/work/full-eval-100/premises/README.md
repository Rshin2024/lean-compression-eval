# Trusted OM100 premise interfaces

`PremiseExporter.lean` is a separate trusted preparation helper. It uses the same
request fields as the proof evaluator, snapshots the environment after elaborating
the source prefix and **before** elaborating the selected reference target, then
exports only declarations present in that snapshot. No evaluated model is called.

The policy is explicitly **reference-informed retrieval**. Constants appearing in
the target type and reference proof seed retrieval; constants reached through newly
generated auxiliaries are traced back to existing declarations. The target and every
post-target auxiliary are excluded. Prior declaration types and non-proof definition
dependencies expand the interface pool **only for OAI/source-local declarations**.
Direct external library constants expose signatures only; their implementation
bodies and transitive core/typeclass infrastructure are not retrieved. Known
external transitive omissions are counted. Direct target-type constants come first,
then direct reference constants, then transitive interface dependencies. Within
these tiers OAI/local definitions, distance, and stable declaration name determine order.
Every model/condition must receive the same
frozen result.

The helper prints fully quantified, fully qualified types. Theorem/lemma/axiom/opaque
implementations are never pretty-printed. Non-proof definitions use `pp.proofs=false`
and threshold zero, so compound proof fields display `⋯`. Structures expose constructor
and projection interfaces. This is descriptive Lean interface text, not a replacement
Lean program; `⋯` is an omitted proof term, not a model-provided admission. Prior proof
constants may occur by name, but their proof bodies are never shown.

Each context also includes the exact current source namespace, resolved open-name
declarations, and active scoped-notation namespaces read from Lean's pre-target command
state/environment. These interpret unqualified names in the unchanged written target;
they are not reconstructed from curator prose or copied source proof blocks.

Imported private constants are not advertised under inaccessible source aliases.
Definition bodies mentioning them are omitted; their type/non-proof dependencies are
still considered. Local source-prefix private helpers remain available in their original
module scope. These omissions are counted separately in the export diagnostics.

Defaults are 64,000 UTF-8 bytes of model context, 4,096 considered constants and 5,000
bytes per printed definition body. A bounded expression walk omits definition bodies
with more than 1,000 nodes before pretty-printing them. Interfaces exceeding the
context cap are omitted whole; collection stops when fewer than 256 bytes remain.
Both unvisited and individually omitted interfaces are counted. Omission counts
describe known candidates, not every possible declaration in
the imported environment. Interface errors and unresolved omissions remain explicit.
This export does not perform independent kernel replay or a semantic equivalent-answer
audit; it must be combined with materialization and reference-check evidence.

The full OM100 run uses **56,000** bytes uniformly (`--max-context-bytes 56000`),
leaving room for the written target and instructions under the 64,000-byte prompt cap.
Its final handoff reads the repaired `materialized_final_v1` tasks and unchanged
reference bodies. Export and official reference-check worker counts are coordinated
with the root so their combined memory allocations fit the Azure runtime.
The v3 export policy permits a separate 600-second preparation wall budget; the
strict proof-validation budget remains unchanged at 180 seconds. This preparation
budget does not affect candidate evaluation or ranking.

## Isolated execution

Use the finalized stripped-source Docker runtime. Actual OAI references must not be
run through the host-only synthetic test path.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 work/full-eval-100/premises/export_premises.py \
  --environment /path/to/environment.json \
  --config /path/to/protocol.json \
  --output work/full-eval-100/premises/export_v1 \
  --workers 2
```

Defaults read `preparation/draft_v1/private/scoped_tasks.jsonl` and its private reference
bodies. `--tasks` can instead point to the materialized manifest. `--task-id OM100-001`
limits an explicitly selected preparation batch. Source-scoped trailing `omit/include/
open ... in` wrappers are lifted only inside the single-target process, matching the
materializer. Unknown wrapper forms fail closed.

The wrapper uses `lean_compression.runner.execute_engine` with this helper path; core
package files are unchanged. It records per-task private results bound to the exact
request, reference, source provenance, helper code, config, environment and retrieval
policy. Reusing an output directory requires identical policy and identities.

`audit_exports.py` binds these records to all 100 materialized inputs, checks every
rendered context against all 100 private reference bodies and curator summaries,
checks source-environment membership and proof suppression, and measures the complete
three-condition prompts against the protocol byte cap. It writes a merged JSONL
candidate and an audit report. Generation and final answer-access approval remain
disabled until the root completes the remaining reviews.

Outputs are `exports.json`, `tasks_with_premises.json`, `summary.json`, `policy.json`, and
private per-task `records/`. Merge `premise_context` and its provenance by task ID into
the final materialized manifest. Do not replace materialized signatures with draft
signatures. Generation remains disabled in these preparation outputs.

## Offline tests

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s work/full-eval-100/premises -p 'test_export_premises.py' -v
```

The Lean fixtures import only `Init`, use the installed 4.34.1 toolchain and place
temporary files inside this directory. They do not build OAI modules or call APIs.
