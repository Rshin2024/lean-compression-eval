# Metrics and reanalysis

This implementation is **paper-aligned**, not a claim of numerical reproduction
of the authors' implementation. Source tokens and expanded primitive references
are different units. Increasing the expansion numerator can increase T0 without
improving reasoning.

## Metric API

`score_graph(graph, root, W_S, W_B)` accepts a mapping of declaration names to
nodes, a list of nodes with `name` keys, or either form under a `nodes` key.
Each node requires an explicit Boolean `primitive` flag. Nonprimitive nodes
require a `signature` multiplicity mapping and a `body` multiplicity mapping
or explicit `null` for an absent body. Primitive flags must come from the trusted
extractor's frozen boundary manifest; the metric engine never infers them from
names. References with multiplicity zero do not create edges.

The result preserves the original nonnegative source counts, decimal-string
`U_S_decimal`, `U_B_decimal`, and `U_decimal`, depth `D`, and exact unreduced
`T0`/`I0` numerators and denominators. Logs use leading integer bits rather than
converting arbitrarily large counts to floating point. There are no pseudocounts.
A present body with zero references has `U_B_decimal: "0"` and a null log. An
absent body has a null body count. A primitive root has no signature/body
decomposition under this API, so its primary score is unavailable.

Traversal and topological evaluation are iterative. Unknown dependencies,
missing primitive classifications, malformed histograms, reachable cycles, and
zero-reference nonprimitive declarations produce an explicit unavailable
status. A cycle's `blocked_nodes` can include its dependants; that field is not
claimed to be the exact strongly connected component. Unreachable graph nodes
do not affect the target's score. Graph failures do not change proof validity.

## Analysis API

`analyze(tasks, attempts, references, config)` expects tasks with `task_id`,
`family_id`, and `domain`, plus optional `origin` and `split`. Attempts require
`model_snapshot`, `task_id`, `condition`, `sample_index`, `validity_status`,
`metric_status`, and `metrics`. The three condition names are `standard`,
`mathematical_compression`, and `source_concision`. A successful attempt has
`validity_status: "success"`; measurable attempts use `metric_status: "available"`.
References are a mapping from task ID to a metric record or an attempt-shaped
record containing `metrics`. They must come from the frozen verified references.

Configuration accepts an `analysis` subobject or flat keys:

| Setting | Default | Meaning |
| --- | --- | --- |
| `samples_per_goal_condition` | 4 | Number of scheduled slots in each task and condition |
| `sample_index_base` | 0 | First scheduled sample index |
| `model_snapshots` | Models found in attempts | Explicit models allow accounting for a model with no records |
| `split` | All supplied tasks | Optional selection of one frozen split |
| `bootstrap_replicates` | 10000 | Number of family resamples; zero disables intervals |
| `bootstrap_seed` | 20261011 | Deterministic resampling seed |
| `confidence_level` | 0.95 | Two-sided percentile interval confidence |
| `audit_seed` | Bootstrap seed | Frozen outlier/random audit sample |

Every scheduled slot stays in the denominator. Missing records are recorded as
missing outcomes and count as unsuccessful, never silently omitted. Duplicate
schedule identities, unknown tasks, unknown conditions, and out-of-range samples
are rejected. Multiple runs should be analyzed separately or combined only after
resolving their immutable identities. Recorded incompatible toolchain, library,
environment, primitive-boundary, or source-token-policy values cannot be pooled.
Per-root primitive-manifest hashes explicitly scoped to the reachable closure
are allowed to differ; they are recorded separately from shared environment
and boundary-policy identities.

Correctness averages sample outcomes within task, then tasks equally. First
attempt and any-of-four rates are separate fields. Valid but unmeasurable proofs
remain successes. Metric coverage and missing reasons are reported independently.

Primary compression comparisons average log T0 over all measurable valid samples
within each task and arm, then average paired task differences equally. Each
contrast publishes its own paired population, domain/origin composition, solve
selection, and missing-metric counts. The three-arm common intersection is a
separate sensitivity analysis. Decomposition into mean log U minus mean log W
uses exactly the same samples and task weights. Source-body geometric ratios and
depth differences are descriptive diagnostics, with their own defined support.

Verified attainment is the all-attempt event `T0(output) >= 2**t * T0(reference)`
for thresholds 0, 1, and 2. Comparison uses exact integer cross multiplication,
including when logarithms would round nearly equal ratios to the same value.
Invalid and unmeasurable outputs do not attain. Missing reference metrics make
the attainment statistic unavailable for the scheduled population, rather than
pretending those references have zero scores. Saturation among measurable valid
attempts is also shown.

Bootstrap resamples entire families within each domain when families are nested
in domains. If a family crosses domains, the report identifies it and uses an
unstratified family bootstrap. All tasks, samples, and arms in a selected family
travel together. Families are sampled equally, but tasks retain equal point
weights; large families still contribute their full task count. Every replicate
recomputes paired support. Undefined replicates, the minimum replicated support,
singleton strata, and effects supported by fewer than two families are explicit.
Intervals are percentile intervals; no confirmatory p-values are produced.

`write_report(report, directory)` emits `analysis.json`, `correctness.csv`,
`paired_tasks.csv`, `resource_usage.csv`, and a standalone `report.html` with tables and inline SVG
plots. The scatter plot displays per-task mean log W and log U among measurable
valid samples. The attainment plot uses all scheduled attempts. A paired-effects
plot shows individual task changes and the family-bootstrap mean interval; a
correctness/compression profile explicitly separates its all-attempt horizontal
population from its conditional vertical population. Raw metric
distributions preserve zero counts and exact decimal/rational quantiles, with
positive-count log summaries. The audit sample identifies 10 positive and 10
negative outliers where available and 20 random paired tasks per contrast.

Resource summaries live at `models[model].conditions[condition].resources` and
include every scheduled attempt, including failures. Validation and extraction
elapsed seconds, recorded peak memory, generation workflow wall time, and input,
output, and reasoning tokens each have observed/missing/invalid counts, median,
p95, and maximum. Elapsed-time and token sums cover observed records only; peak
memory is not summed. Requested memory limits are never treated as measured
peaks. Zero tokens are valid recorded zeros; absent usage is unavailable.

The built-in provider's `generation.generation_elapsed_seconds` includes the
entire workflow, including fixed transport retries and backoff. It is not
model-only computation. Historical records without an explicit latency value
remain unavailable; the report does not derive latency from timestamps or
transport logs. Reasoning tokens may be included in provider output tokens, so
the columns are not added. No API cost is inferred without an explicit rate
schedule.

Unfinished slots, transport errors, provider failures, and returned-model
mismatches mark a run incomplete. All slots remain in the accounting denominator,
but the report prominently labels their proportions provisional and blocks
correctness/compression conclusion readiness. Provider failures and model
mismatches are not labeled incorrect mathematical proofs. Deterministic refusal
and truncated output remain actual scheduled-model failures under the protocol.

The report's `compression_conclusions_ready` checks signature consistency,
reference availability, metric coverage, paired support, and completion of all
scheduled outcomes. It is not a scientific approval or
a complete release gate; environment, validity, dataset, and cost/readiness
checks belong to the surrounding pipeline. A missing record remains visible even
when the metric summaries themselves are complete. No weighted aggregate quality
score, PageRank, universal expansion coverage f(s), or claim about internal model
reasoning is produced.

## Qualitative audit bundle

`write_audit_bundle(manifest, rows, result, directory)` writes an unblinded
`audit.json` with selection reasons and paired task context, a reviewer-facing
`review/index.html`, and a separate `private/labels.json` unblinding key. The
review directory hides model and condition labels, orders anonymous proof codes
deterministically, and contains every available arm/sample output for each
selected task, including failures. Missing scheduled records become explicit
gaps. Original response bytes, adapted proof, source-token evidence, weighted
dependency graph, and exact task context are separate linked files.

`review/annotations.csv` starts with blank mechanisms, notes, and suspected
harness-issue fields. Reviewers should edit a copy. A mechanism explains the
metric; it does not override the score or exclude a valid outlier. Blinding is
best effort: model and condition labels are hidden, but the submitted proof is
preserved verbatim and may contain clues. Full records and the condition mapping
are in the private part of the bundle. Identical rebuilds are idempotent;
differing evidence is not overwritten.

## Trusted imported-environment index

`index_verified_closures(records, environment_hash, boundary_policy)` requires
verified reference records, successfully exported graphs, and matching
environment and boundary-policy hashes. `write_index(index, path)` writes a
content-addressed immutable JSON file. The index merges imported declarations
only, excluding all declarations owned by `EvaluatorCandidate`. Source counts
are never invented for the imported nodes.
The explicit boundary policy may be a nonempty version string or a policy
object; either form must match the exact recorded SHA-256 policy digest.
For atomic boundaries where the extractor deliberately did not inspect the
body histogram, the cached body state is `unmeasured_atomic`; it does not claim
that the declaration lacks a body. Cache keys also include the extractor's
`histogram_status` and `body_present` evidence. This is index schema version 2.

Each base node's SHA-256 cache identity includes the environment, full boundary
policy digest, declaration name, module ownership, primitive flag, type and
value hashes, and both complete weighted reference histograms. This avoids
relying on Lean expression hashes alone. Conflicting definitions under the same
name are errors. One iterative dependency pass computes base U and D. Cycles,
missing dependencies, and zero-reference nonprimitive nodes retain unavailable
states, while unaffected components remain measurable. The primitive manifest
is explicitly the union of imported reachable reference closures, not a claim
to have indexed every declaration in Mathlib.

`merge_candidate(index, envelope)` accepts `nodes` with matching
`environment_hash` and `boundary_policy_hash` (or the exact `boundary_policy`).
It rejects bare nodes without provenance, index tampering, and modifications to
trusted imported nodes. It returns a complete graph for `score_graph` while
leaving the base index unchanged. The stored `base_cache` contains reusable
per-node U/D results; `score_graph` still independently checks the merged graph
and computes the final root score, which also provides a consistency check.

## Offline dataset readiness

`build_readiness_report(tasks, verified_records, config)` consumes public tasks
and independent reference evidence, never target-model outputs.
`write_readiness_report(report, directory)` emits inspectable immutable JSON
and an HTML diagnostic. Counts are broken down by split, domain, origin, and
their intersection. Each task exposes missing evidence, manual audit flags,
reference source length W_B, dependency depth D, and source-shape control labels.
Literal reflexivity and single tactic wrappers are described by their source
shape; neither is automatically labeled mathematically easy.

Evidence identity accepts, in order, a matching `verification_request_hash`,
matching `task_input_hash`, or an exact legacy `task_hash == digest(task)` when
the narrower fields are absent. The method is recorded. The verification hash
links Lean runtime inputs only; prompt-only premise presentation remains
separately frozen under the model input hash. An identity amendment can derive
that runtime hash only from a saved task snapshot matching the original record,
and explicitly records that no new proof execution took place. Runtime context,
imports, theorem target, binders, proposition, or forbidden-set changes require
new matching verification evidence.

Source compilation annotations cannot satisfy the independent kernel-replay,
trusted target, axiom, source-span, and dependency-graph evidence requirements.
Reference records with duplicate matching identities are unresolved rather than
chosen for a favorable metric. Missing, stale, invalid, and unmeasurable records
remain visible. Different engine/environment/boundary/token-policy cohorts are
shown separately; their numerical distributions are not silently pooled. Caller
supplied expected pins can select a cohort, with other evidence marked out of
scope for the requested profile.

By default, proposed source-length and depth cutpoints are the 25th and 75th
percentiles of complete measured pilot references. Ties are preserved, and
incomplete fit populations and empty cells remain explicit. These are reference
coverage bins, not mathematical difficulty labels. They do not silently update
task strata or establish broader difficulty coverage. Before main generation,
review the measured cross-table, curate adequate coverage in each domain, then
provide fixed `source_length_cutpoints` and `dependency_depth_cutpoints`, set
`strata_frozen`, and record the independent `difficulty_review_complete` decision.
All task evidence and manual audit gates must also pass. This dataset diagnostic
does not replace model-pin, cost, isolation, preregistration, or power checks.
