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
decomposition under this API, so its primary score is unavailable. Each result
also carries `scorer_hash`, the SHA-256 of `metrics.py`, which identifies the
scoring code.

Traversal and topological evaluation are iterative. Unknown dependencies,
missing primitive classifications, malformed histograms, reachable cycles, and
zero-reference nonprimitive declarations produce an explicit unavailable
status. Dependencies are visited in sorted order, so a graph with several
defects reports the same one in every process. A cycle's `blocked_nodes` can
include its dependants; that field is not claimed to be the exact strongly
connected component. Unreachable graph nodes do not affect the target's score.
Graph failures do not change proof validity.

## Analysis API

`analyze(tasks, attempts, references, config)` expects tasks with `task_id`,
`family_id`, and `domain`, plus optional `origin`, `split`, and `stratum`. Attempts require
`model_snapshot`, `task_id`, `condition`, `sample_index`, `validity_status`,
`metric_status`, and `metrics`. The three condition names are `standard`,
`mathematical_compression`, and `source_concision`. A successful attempt has
`validity_status: "success"`; measurable attempts use `metric_status: "available"`.
References are a mapping from task ID to a metric record or an attempt-shaped
record containing `metrics`. They must come from the frozen verified references.
Measured attempts and usable references must share one `scorer_hash`; records
scored before that field existed count as one `legacy` scorer. Any mixture is
refused, and the shared value is reported as `provenance.scorer_hash`.

The `analyze` command applies the reference rules before calling this function.
The reference policy must match the run's environment, engine and measurement
settings. Each reference record must be the one frozen with the run, checked per
task in current manifests and as one set in older ones. A record for a task that
had no anchor when the run was frozen is refused with that reason. An evaluation
version written by `evaluate --reference-checks` after a checker or extractor fix
is bound instead to the frozen proofs as its engine re-checked them: its
`policy.json` names their policy digest, each task's record digest and the frozen
engine that its engine replaces (`frozen_engine_hash`), and only those records
are accepted for it (or for a version rescored from it). A task whose frozen
proof that engine rejected, or in production could not measure, is listed in
`unanchored_reference_tasks` with its record's statuses and digest instead. It
has no anchor in that version: its record is checked against the digest and
then ignored, so the task stays in `missing_reference_task_ids` and attainment
follows the missing-reference rule below.
`run_provenance` records `reference_policy_hash`, `reference_anchor_binding`, the
`reference_anchor_digests` used, the version's `unanchored_reference_tasks`, and
`reference_metrics_recomputed`. The binding
is `frozen_per_task`, `amended_per_task` for such a re-checked version,
`frozen_set` for an older manifest, `not_frozen` for a development run frozen
without reference checks, or null when `--references` is not given. The last
field lists the anchors whose metrics were recomputed from their stored graphs.
That happens whenever every measured attempt was scored by the running metric
code and an anchor was not, whether the version came from `evaluate` or
`rescore`. As in `rescore`, an anchor whose graph was extracted is recomputed
even if the older code left its metrics unavailable, unless the evaluator
withheld them: a record with a `metric_unavailable_reason`, or with no metrics
at all, keeps them withheld. Older code withheld the metrics of a graph extracted
while the engine file changed, since it may come from another engine.
`run_provenance` also records the run's `split`, `experiment` and
`protocol_version`; a production `main` freeze checks them, `development: false`,
and every model's `run_complete` in the pilot report its preregistration cites,
which must be the report of a pilot of the same model (its `model_snapshot`,
listed in its `models`).

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
| `easy_control_strata` | `easy`, `provisional_easy` | Task `stratum` labels reported as easy controls |

`analyze` also reads the top-level `config["environment"]` object, the frozen
protocol's environment section: its `boundary_policy` is reported as
`provenance.configured_boundary_policy`, and its `lean_toolchain` and
`mathlib_commit` pins seed the provenance described below.

Every scheduled slot stays in the denominator. Missing records are recorded as
missing outcomes and count as unsuccessful, never silently omitted. Duplicate
schedule identities, unknown tasks, unknown conditions, and out-of-range samples
are rejected. Multiple runs should be analyzed separately or combined only after
resolving their immutable identities. Recorded incompatible toolchain, library,
environment, primitive-boundary, source-token-policy, scorer, or engine values cannot be pooled.
Per-root primitive-manifest hashes explicitly scoped to the reachable closure
are allowed to differ; they are recorded separately from shared environment
and boundary-policy identities.

`provenance.lean_toolchain` and `provenance.mathlib_commit` are seeded from the
frozen protocol's `environment` pins; a recorded attempt or reference value that
disagrees is rejected. Pooled attempts and references must also share one
recorded checker `engine_hash` and, where recorded, `evaluator_source_hash` (a digest
of the Python package's `*.py` files, not of the Lean engine).
Both are reported. The check also covers failures and unmeasurable verdicts,
which carry no source-token-policy hash. A null identity means that no pooled
record carries it.

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
Report files are UTF-8 whatever the locale.

The `analyze` command builds the whole report, including
`all_scheduled_attempts.json` and the audit bundle, in a hidden staging
directory created before the analysis starts, and publishes it only when every
file is complete, so a failure leaves no partial report at the destination. The
destination must be new or empty; an existing analysis or any other file is
never overwritten. A new destination is staged beside it and renamed into place.
An existing empty directory is never replaced, so it keeps its permissions and
any shell inside it sees the report: the report is staged inside it and its
entries are moved in, `analysis.json` last. A killed `analyze` can leave its
staging directory, `.<name>.analyze-<pid>-<time>.partial` with at most 40
characters of the destination's name, beside or inside the destination. The next
`analyze` for that destination on the same host removes it once that process has
exited, and never touches other files.

`all_scheduled_attempts.json` lists every scheduled slot with its status,
metrics, and resources. It pairs conditions with raw responses, so like the
audit bundle's unblinded files only its owner can read it. Dependency graphs
stay in the immutable evaluation files: each evaluated row omits
`extraction.nodes` and names its file in `evaluation_record` (`path` within the
run and `sha256`). Reference records likewise drop their graphs once their
digests are checked and any rescoring is done, and an older manifest's frozen
reference set is digested by reading each record again, so peak memory tracks
the largest single record rather than all graphs together.

Each row of the HTML compression table shows its theorem-family count and
defined/requested bootstrap replicates; the interval column header states the
confidence level. An effect supported by fewer than two families reads
`not estimable (<2 families)` instead of a zero-width interval, and the
paired-effects plot labels it not estimable and draws no interval for it.
Single-family bootstrap strata are listed below the table.

Easy controls are reported separately, using the frozen task `stratum` labels.
The `easy_control_strata` setting (default `easy` and `provisional_easy`) names
the easy-control labels; every other label, including a missing one (`unknown`),
is non-control. `distributions` adds a `stratum:<label>` group for each label and
the `stratum_class:easy_control` and `stratum_class:non_control` groups, each with
per-condition correctness and raw metric distributions; a class with no task is
omitted there. `models[model].stratum_breakdown` lists both classes, even when
empty, and each label, each with its composition, task IDs, `distribution_group`
(null for an empty class), and paired success-rate and mean log2 T0 contrasts.
These restrict the pooled per-task differences and replay the pooled family
draws from the same seed, so pooled estimates are unchanged. They are
exploratory subgroups, not separately powered. `write_report` adds
`correctness_by_stratum.csv` (nonempty groups), `contrasts_by_stratum.csv`
(every class and label, with interval bounds), and an HTML easy-control section.

Resource summaries live at `models[model].conditions[condition].resources` and
include every scheduled attempt, including failures. Validation and extraction
elapsed seconds, recorded peak memory, generation workflow wall time, and input,
output, and reasoning tokens each have observed/missing/invalid counts, median,
p95, and maximum. Elapsed-time and token sums cover observed records only; peak
memory is not summed. Requested memory limits are never treated as measured
peaks. Zero tokens are valid recorded zeros; absent usage is unavailable.
A recorded `usage` or token-details value that is not an object counts as an
invalid observation of the token fields it would supply. An absent or empty
`output_tokens_details` object defers to `completion_tokens_details`.

`validation_heartbeats` has the same statistics and an observed sum, taken from
each validation run's `resources.heartbeats_used`: the Lean heartbeats of
candidate elaboration, including the elaborator's own kernel checks, plus kernel
replay, in `maxHeartbeats` units (1000 raw heartbeats). They are not bounded by
or comparable with `validation.max_heartbeats`, in total or in either phase. The
engine reports them for successful checks only, so failed checks and records
from engines that predate the measurement count as missing, never as zero.

The built-in provider's `generation.generation_elapsed_seconds` includes the
entire workflow, including fixed transport retries and backoff. It is not
model-only computation. Historical records without an explicit latency value
remain unavailable; the report does not derive latency from timestamps or
transport logs. Reasoning tokens may be included in provider output tokens, so
the columns are not added. No API cost is inferred without an explicit rate
schedule.

Unfinished slots, checker or infrastructure errors (`infrastructure_error`,
`evaluator_error`, `environment_error`, `forbidden_environment`), transport
errors, provider failures, returned-model mismatches, and any unrecognized status
mark a run incomplete. All slots remain in the accounting denominator,
but the report prominently labels their proportions provisional and blocks
correctness/compression conclusion readiness. None of these are labeled incorrect
mathematical proofs. Only Lean verdicts attributable to the candidate, deterministic
refusals, malformed responses (`malformed_response`), and truncated outputs are
scheduled-model failures under the protocol.
Each condition's `failure_breakdown` separates these `model_failures` from
`unresolved_outcomes`; the correctness table and CSV show both.

A checker incident in verification or extraction, or a provider call without a
model outcome, leaves no record, so its slot counts as `not_evaluated` or
`not_generated`. The `analyze` command therefore summarizes the logs behind them
in `run_provenance`. `checker_incidents` covers
`evaluations/<version>/incidents.jsonl`: its events and attempts and, for
attempts still without a record, their latest statuses, phases and first-line
reasons. A rescored version copies records but no incidents, so
`inherited_checker_incidents` summarizes, in the same form, the log of the
evaluated version it was rescored from (following rescores back to it) for the
attempts still without a record in the rescored version; it is null for an
evaluated version. `provider_outages` counts `no_model_outcome` and `outcome_unknown`
events in `transport.jsonl`, the attempts still without a generation after
them, and in-flight markers. A prompt the provider rejected as `invalid_prompt`
is shared by every sample of its task and condition, and `generate` sends none
of them again without `--retry-rejected`, so all of its attempts still without a
generation count as unresolved, sent or not; `prompt_rejected_attempts` counts
them, and the notice names the remedy. Events end only at a newline, never at U+2028,
U+2029 or U+0085, and an append first ends a line a killed writer left
unterminated. Unreadable log lines are counted, not guessed. The HTML incomplete
notice repeats the unresolved counts, the inherited ones with their source
version, and any unreadable lines.

The report's `compression_conclusions_ready` checks signature consistency,
reference availability, metric coverage, paired support, and completion of all
scheduled outcomes. It is not a scientific approval or
a complete release gate; environment, validity, dataset, and cost/readiness
checks belong to the surrounding pipeline. A missing record remains visible even
when the metric summaries themselves are complete. No weighted aggregate quality
score, PageRank, universal expansion coverage f(s), or claim about internal model
reasoning is produced.

`analyze --baseline DIR` adds the spec's trusted tactic-portfolio anchor, written
by the `baseline` command, as `automation_baseline` in `analysis.json`, as
`automation_baseline.csv`, and as a `report.html` section. It fails closed unless
`DIR/policy.json` has kind `baseline`, the run's environment, configuration,
and evaluated engine hashes, and the frozen `tactic_portfolio_total_wall_seconds`
as its `portfolio_validation_wall_seconds`, and every record is a
`trusted_tactic_baseline` for the frozen portfolio, checked under that budget
(`validation.resources.wall_seconds_limit`), whose task identity matches its
frozen task. An evaluation version that records no checker engine (neither a
`policy.json` nor an evaluated record with `engine_hash`) is refused, and so is
malformed JSON in `DIR`, with the file named. Records for tasks outside the run
are counted, not used. `automation_baseline.policy` keeps only the checked
policy fields: `kind`, `engine_hash`, `environment_hash`, `config_hash`, and
`portfolio_validation_wall_seconds`. The HTML section states that validation
budget. A portfolio `success` is solved only when its Lean verdict
(`validation.validity_status`) is also `success`; checker rejections and
exhausted budgets are unsolved; checker, environment, and harness incidents, and
a success without that verdict, are unresolved. The `baseline` command itself
writes no record for a checker incident (it logs it in `incidents.jsonl`), so
that task counts as missing until a rerun into the same directory checks it.
Overall and per-domain solve rates use every run task as the denominator. Per
model, each condition's task-weighted success rate and mean task log2 T0, U, and
W, and each contrast's paired log2 T0 change, are split by portfolio-solved,
unsolved, and unknown tasks. Missing and unresolved tasks form the unknown group
and mark the section incomplete. The split is descriptive, has no intervals, and
is not a preregistered decision quantity. Without `--baseline`, the key records
`not_supplied` and no CSV is written.

## Qualitative audit bundle

`write_audit_bundle(manifest, rows, result, directory, run=None)` writes an unblinded
`audit.json` with selection reasons and paired task context, a reviewer-facing
`review/index.html`, and a separate `private/` directory with the `labels.json`
unblinding key and a random `code_key.json`. Task and proof codes are
HMAC-SHA256 values under that key, so the run hash, seed and attempt IDs cannot
reproduce them; a new directory gets a new key, codes and proof order. Bundles
committed under `examples/` predate the key (`frozen_blinded_v1`): their codes
can be recomputed from the run manifest, so they are not blinded review
material. Give reviewers `review/` only: `audit.json`, `private/` and the
enclosing report (for example `all_scheduled_attempts.json`) reveal models and
conditions. The review directory contains every available arm/sample output for
each selected task, including failures. Missing scheduled records become
explicit gaps. Original response bytes, adapted proof, source-token evidence,
weighted dependency graph, task context with import flags (`target.json`, which
carries `import_specs`), and a `target.lean` template are separate linked files.
A `malformed_response` has no proof; its text, the provider's entire reply, which
can name the model, is kept only in `private/`. The template repeats the prompt's
import lines; when the task has `import_specs`, a `module` header precedes them.

`review/annotations.csv` starts with blank mechanisms, notes, and suspected
harness-issue fields. Reviewers should edit a copy. A mechanism explains the
metric; it does not override the score or exclude a valid outlier. Blinding is
best effort: model and condition labels are hidden, but the submitted proof is
preserved verbatim and may contain clues. Full records and the condition mapping
are in the private part of the bundle. Given rows read without graphs, the
bundle reloads each exported attempt's graph from the evaluation file the row
names and refuses a file whose SHA-256 changed. Identical rebuilds are idempotent;
differing evidence is not overwritten.

## Trusted imported-environment index

`index_verified_closures(records, environment_hash, boundary_policy,
engine_hash=None)` requires verified reference records, successfully exported
graphs, matching environment and boundary-policy hashes, and one extractor
`engine_hash`; without an explicit value, all records must share one, so
engine versions are never mixed. `write_index(index, path)` writes a
content-addressed immutable JSON file. The index merges imported declarations
only, excluding all declarations owned by `EvaluatorCandidate`, including a
library task's context copies of Mathlib names. Source counts are never
invented for the imported nodes.
The explicit boundary policy may be a nonempty version string or a policy
object; either form must match the exact recorded SHA-256 policy digest.
The `index-closures` command is narrower: like `freeze`, `evaluate`,
`verify-references`, and `baseline`, it refuses configured policy labels the
pinned engine does not implement, so it accepts only the boundary policy
`expand_mathlib_and_evaluator_v1`.
For atomic boundaries where the extractor deliberately did not inspect the
body histogram, the cached body state is `unmeasured_atomic`; it does not claim
that the declaration lacks a body. Cache keys also include the extractor's
`histogram_status` and `body_present` evidence. This is index schema version 3;
older indexes are rejected.

Each base node's SHA-256 cache identity includes the environment, full boundary
policy digest, extractor engine, declaration name, primitive flag, type and
value hashes, and both complete weighted reference histograms. This avoids
relying on Lean expression hashes alone. Module ownership is not identity: Lean
attributes an on-demand auxiliary, such as an equation lemma, to whichever
imported module first realized it, so nodes list `observed_owner_modules`.
Conflicting definitions under the same name are errors. One iterative
dependency pass computes base U and D. Cycles, missing dependencies, and
zero-reference nonprimitive nodes retain unavailable states, while unaffected
components remain measurable. The primitive manifest is explicitly the union of
imported reachable reference closures, not a claim to have indexed every
declaration in Mathlib.

`merge_candidate(index, envelope)` accepts `nodes` with matching
`environment_hash`, `boundary_policy_hash` (or the exact `boundary_policy`) and
`engine_hash`, plus `imported_node_names`, the imported names of the
candidate's closure. The nodes must include every evaluator-owned declaration
of that closure. These stay private, so a task's context may redeclare a name
another task imports. Only the listed imported names are taken from the index;
an omitted declaration stays a missing dependency rather than borrowing another
task's node. It rejects bare nodes without provenance, other engines, index
tampering, and modifications to trusted imported nodes. It returns the
candidate's graph for `score_graph` while leaving the base index unchanged.
The stored `base_cache` contains reusable per-node U/D results; `score_graph`
still independently checks the merged graph and computes the final root score,
which also provides a consistency check.

## Offline dataset readiness

`build_readiness_report(tasks, verified_records, config)` consumes public tasks
and independent reference evidence, never target-model outputs.
`write_readiness_report(report, directory)` emits inspectable immutable JSON
and an HTML diagnostic. Counts are broken down by split, domain, origin, and
their intersection. Each task exposes missing evidence, manual audit flags,
reference source length W_B, dependency depth D, and source-shape control labels.
Literal reflexivity and single tactic wrappers are described by their source
shape; neither is automatically labeled mathematically easy.

`single_tactic_wrapper` labels any body `by <tactic>` with one identifier-like
tactic name, optionally ending in `?` or `!`, and records the tactic; `by rfl`
is literal reflexivity instead. Reports
record `readiness_settings`: `enforce_quotas`, the strata fit split, the
cutpoint source, supplied pins, and SHA-256 digests of the readiness section and
of the full configuration. Relaxing `enforce_quotas` leaves the `quotas_enforced`
gate pending, so such a report never claims `ready_for_dataset_freeze`.
`task_manifest_hash` is the SHA-256 of canonical JSON of the parsed task list
(`task_manifest_hash_method`); `task_file_sha256` is the input file's byte hash,
which for the committed manifest equals `manifest_sha256` in
`data/public/dataset_status.json`.
The `reference_proofs_absent_from_prompts` gate stays pending while any task's
whitespace-normalized reference proof of at least 15 characters
(`ANSWER_ACCESS_MIN_CHARS`) occurs exactly in a prompt rendered by
`prompts.messages()`, or while a task's proof text cannot be checked;
`answer_access_screen` lists both sets of tasks. Shorter exact matches of at
least 9 characters are listed in `short_exact_match_task_ids` without holding
the gate. The screen does not detect near-verbatim copies, such as a sibling
lemma's proof with one identifier changed, so the gate is a screen, not the
human answer-access audit.

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
