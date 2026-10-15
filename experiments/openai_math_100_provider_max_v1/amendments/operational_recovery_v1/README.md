# Provider-maximum run: operational recovery evidence

This source archive records the reviewed recovery operations for the separate
9,600-attempt provider-maximum run. The frozen prompts, generation manifests,
core evaluator, Lean evaluator, and live controller sources were unchanged.
`source-hash-manifest.json` identifies every copied file and its original
workspace path. Copies were checked against the live files without modifying them.

## Saved text reconciliation

The text-only recovery accepts an already captured Anthropic response only when
its complete SSE stream reaches `message_stop`, the request/capture/stream hashes
agree, and the content consists solely of text and thinking blocks. It preserves
the full returned text, including any serialized tool-request prose, and retains
the original `tool_use` stop reason. The existing Lean evaluator decides the
result. The helper makes **zero model calls and executes zero tools**. Other
incomplete streams remain unresolved under this policy.

## Eight separately authorized replacements

The root operator authorized one replacement for each of these exact terminal
provider failures, within the user's new full-run request and explicit statement
that cost was not limiting. This was **not direct human approval of the eight
individual attempt IDs**. The root review record preserves that distinction.

| Original failure | Attempts | Preserved evidence |
|---|---:|---|
| Opus terminal `overloaded_error` | 1 | Empty answer, incomplete stream, original claim and transport prefix |
| Fable terminal `overloaded_error` | 1 | Empty answer, incomplete stream, original claim and transport prefix |
| Sonnet terminal `api_error` | 6 | Full partial answers and incomplete streams; no final `message_stop` |

Each release is bound to its original marker and evidence hashes. Live claims,
saved final responses, different attempts, and replacement claims cannot be
released by these proposals. The release helpers make no model calls and do not
resume the generation controller. Original outcomes and original charges remain
**unknown**, with charges represented as `null`, never zero. An uncertain
replacement cannot inherit another retry from this authorization.

The eight `release-receipt.json` files attest that the original markers were
released once. Immutable proposal files retain their earlier preparation status;
the later receipts are authoritative for execution state.

## One-use resume watcher

The archived watcher waits for the original generation child to exit naturally
and the credential parent to hold. Before publishing one existing resume control,
it requires all 9,592 other scheduled generations to exist unchanged, zero
in-flight markers, all eight approved release receipts, unchanged source and
launch pins, and no earlier resume or extra unresolved attempt. It consumes its
own one-use intent before publication and cannot restart failed replacements.

This snapshot contains its activation receipt and a hash-only baseline of 9,118
already recorded generations. At archive creation, the watcher was still waiting;
activation does not establish that the replacement calls occurred. Later handoff
and completion evidence must be read separately.

## Contents and verification

Included: helper and test sources, exact proposals, root review, release intents
and receipts, dry validations, watcher plan/activation, and hash-only generation
snapshots. The five recovery test groups passed 37 offline tests, including
no-network checks, live-lock refusal, immutable idempotence, corrupted-evidence
rejection, exact-scope enforcement, and one-use publication.

Excluded: raw SSE, partial answer text, response captures, API credentials, SSH
material, runtime logs, and bulk generation/verification records. The manifest
lists hashes for the 14 omitted original streams and partial-answer files; those
bytes remain in the local run evidence. Other pinned dependencies are archived
with the [parent experiment](../../README.md). Execution requires those
dependencies and the omitted original evidence; this is not a standalone runtime
bundle or a new authorization to run an evaluation.
