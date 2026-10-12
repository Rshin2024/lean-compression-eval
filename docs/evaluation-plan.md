Test the user’s proposed experiment directly: give an LLM a fixed mathematical problem in Lean, ask it to produce a maximally compressed correct proof, and measure the output using the paper’s compression quantities. Compare that instruction with an ordinary correctness prompt and a shortest-source prompt on exactly the same problems. Report correctness and compression separately.

The central question is: does asking for mathematical compression change the structure of verified proofs beyond what a request for fewer source tokens already achieves? This is an evaluation of submitted representations under a fixed library, not a measurement of the mathematically shortest possible proof or of the model’s internal reasoning.

Use a 50-goal pilot and a disjoint 200-goal main test, with four independent completions per goal, model and prompt condition. This gives 600 pilot outputs and 2,400 main-test outputs per model. Run three prompt conditions: A, ordinary correctness; B, mathematical compression; C, source concision. The core experiment requires no new-lemma discovery or interactive proving.

All sample counts, thresholds, limits and schedules below are proposed settings. Freeze final choices after the pilot and before generating main-test outputs. This document is a specification; it does not report an implemented harness or completed evaluation.

## Metrics and interpretation

For a declaration u with signature S and body B, retain these paper-derived quantities. W is source length and U is weighted recursive dependency expansion; these use different counting units.

| Field | Definition |
| - | - |
| W_S and W_B | Lean source token counts for signature and body |
| U_S and U_B | Expanded primitive-reference counts for signature and body |
| D | Longest dependency path to the fixed primitive boundary |
| T0 | (U_S + U_B) / (W_S + W_B) |
| I0 | W_B / W_S |
| I1 | Stationary probability of a compression-biased dependency walk |
| f(s) | Largest primitive-length ball entirely representable within s macro symbols |

The paper combines normalized T0 and I0 for the walk’s teleportation distribution. Its unwrapped count retains repeated references; it is not an AST size or a normalized proof term. [Paper, sections 2, 3 and 5](https://arxiv.org/pdf/2603.20396).

W records the submitted representation, not a search for the shortest equivalent proof. Use the label “paper-aligned implementation” until the tokenizer, primitive manifest and graph conventions have been matched against an author implementation. Do not claim exact numerical reproduction from the equations alone. Different proofs of the same theorem need not expand to the same mathematical or syntactic object. A higher U/W is therefore not automatically a better encoding of fixed information. The primary factual claim is a change in the measured ratio; the component measurements and proof audit determine its interpretation.

For the benchmark, store I0 and D as descriptive fields. At a fixed target, increasing I0 means increasing the submitted proof’s source length. Increasing T0 can mean either a smaller representation or a larger expanded dependency body. Neither change independently establishes better reasoning.

## Experimental questions and exact prompts

The primary contrast is B versus A: does the mathematical-compression instruction change verified output quality? The distinguishing contrast is B versus C: does it do more than an instruction to minimize source length? Estimate both within each model before comparing models. A small initial run can use one model; a broader study can repeat the frozen experiment on three or more pinned model snapshots.

Use this common instruction for every condition:

```text
You are completing a Lean theorem in the supplied fixed environment.
Return exactly one Lean proof term that fills <PROOF> in the target.
A tactic proof beginning with "by" is allowed.
Prove the exact target using its stated assumptions and the supplied imports.
Do not return Markdown fences, prose, a replacement declaration, imports,
top-level commands, new axioms, sorry, admit, or custom metaprogramming.
Ordinary local have and let expressions are allowed.
```

Append exactly one condition instruction:

| Arm | Instruction |
| - | - |
| A: standard | Produce a correct Lean proof of the target. |
| B: mathematical compression | Produce a correct Lean proof in the most mathematically compressed form you can. Use suitable existing definitions and lemmas, and factor repeated reasoning locally, to express the argument compactly. Avoid irrelevant reasoning or artificial expansion. |
| C: source concision | Produce a correct Lean proof using as few Lean source tokens as possible. |

Use one task template and serialize it identically across arms:

```text
Lean toolchain: {{LEAN_TOOLCHAIN}}

Approved imports:
{{IMPORTS}}

Relevant context:
{{FIXED_PREMISE_CONTEXT}}

Task:
{{CONDITION_INSTRUCTION}}

theorem {{EVALUATOR_TARGET_NAME}} {{EXPLICIT_BINDERS}} :
  {{TARGET_PROPOSITION}} := <PROOF>
```

Store the system and user messages verbatim, not merely a template name. Evaluate the entire response as the candidate body; do not extract whichever of several proposed proofs happens to work. If the pilot shows that a deterministic adapter for a single surrounding code fence is necessary, freeze that adapter for all arms before the main test and retain both raw and adapted output. No semantic rewriting, repair or proof selection happens in this adapter.

The wording of B deliberately describes compression without telling the model to maximize T0 numerically. Optimizing the raw ratio can reward irrelevant expansion. A separate adversarial score-optimization arm could study that failure mode later; it should not replace the user’s original experiment.

## Tasks and sampling

Make single-shot proof generation the primary experiment. A refactoring experiment is an optional follow-up with a different information budget.

| Track | Model input | Model output | Status |
| - | - | - | - |
| Proof generation | Fixed signature, imports and approved premise context | One Lean proof body | Core experiment |
| Proof refactoring | Same inputs plus a verified reference proof | One replacement proof body | Optional follow-up; analyze separately |

Within generation, every model sees all three prompt conditions on every task. The condition instruction is the only intended prompt difference. Do not show the model reference proofs, numerical compression scores, prior attempts or validator feedback. Local have and let expressions are allowed; new top-level declarations are outside the core submission format.

Build 200 test goals across five equally weighted domains: algebra and groups; number theory; analysis and inequalities; sets and logic; linear algebra and finite combinatorics. Target 40 goals per domain. Within each domain, sample across reference proof length and dependency depth rather than only theorem popularity. Include easy tasks as controls and report their results separately.

Use 150 reconstructed Mathlib goals and 50 freshly authored goals in the main test: 30 library goals and 10 fresh goals per domain. Reserve 50 pilot goals, 10 per domain, from disjoint theorem families. Fresh goals should change assumptions, combinations or constructions rather than merely rename public statements. The fresh subset is a limited check on dependence on familiar statements, not proof of absence from training data.

Assign family IDs before splitting, grouping closely related statements, variants and proofs built around the same construction. Use coarse reference-proof-length and dependency-depth strata to cover easy, medium and harder examples. Do not tune strata using target-model outcomes. Record task source, family, domain, difficulty stratum and any exclusion reason.

Use Mathlib source directly for the primary implementation. LeanDojo Benchmark 4 can supply candidate tasks and metadata, provided its environment is rebuilt on the chosen pin; its official repository describes the Lean 4 extraction workflow. [LeanDojo](https://github.com/lean-dojo/LeanDojo).

Freeze eligibility and replacements before evaluating the target models. Require a verified reference proof, auditable premises and extractable metric closure. Make exclusions and replacement reasons part of the dataset manifest. Never drop a difficult test goal after seeing model failures.

## Environment and answer access

Choose one immutable, buildable Mathlib snapshot and use its declared lean-toolchain and dependency lockfile. Do not guess a compatible Lean version independently. Record the commit, complete dependency lockfile, source and compiled-environment hashes, hardware allocation and evaluator version. Resolve and test these pins before any pilot generation; use the same per-task environment for every model, condition and reference proof. Matching the paper’s historical snapshot is an optional reproduction exercise, not a prerequisite for this prompt comparison.

The evaluator owns a standalone signature with explicit binders. The model supplies only a term accepted as the proof body. The evaluator inserts it into a fixed theorem template and parses the complete supplied body to end of input. It does not accept a replacement theorem statement, new imports, options or top-level commands in this mode.

For library goals, reconstruct the environment immediately before the theorem’s original declaration. Do not import the module that already contains the target. Ensure the original theorem, known aliases and declarations depending on it are absent from the allowed environment; audit the final transitive dependency closure against that forbidden set. Filter known duplicate target statements. Dependency and duplicate checks cannot establish absence of every logically equivalent theorem.

Keep reference proofs and forbidden source files out of model-visible inputs and execution paths. Precompute one approved premise context per task using a fixed retrieval or curation policy; send exactly the same context bytes to every condition and model. Freeze a context cap that all selected models can support. Log the full prompt and distinguish premises shown in the prompt from the larger approved environment available during Lean checking. The core model receives no live retrieval or execution tools.

Distinguish three claims: the target is absent from the runtime environment; the task family is separated from development tasks; the target was absent from model pretraining. The first two are enforceable here. Public theorem selection alone cannot establish the third.

## Validity gate

A proof succeeds only when it proves the evaluator’s original target under the permitted assumptions and within the verification budget. Record output-format failure, parse failure, elaboration failure, target mismatch, forbidden dependency, disallowed axiom and verification timeout separately. Metric extraction has its own status and timeout; it cannot retroactively turn a valid proof into an incorrect one.

Run generated code in an isolated process with a read-only trusted library and no network or access to evaluator secrets. Restrict executable metaprogramming forms in the proof interface. This isolation is necessary because tactic elaboration executes code.

Independently replay the resulting declaration against the trusted target and audit transitive axioms. The primary track permits the agreed foundational axioms, normally propext, Classical.choice and Quot.sound. Reject sorryAx, newly introduced axioms and native-evaluation assumptions outside that policy. Use an allowlist appropriate to the pinned version, not just a source search for the word “sorry.”

Lean’s current validation guidance describes kernel replay, axiom inspection and comparator-based challenge checking. Use comparator or an equivalent pinned checker where supported; do not assume current checker commands work on an older toolchain. [Lean proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/). Programmatic axiom collection is exposed by [Lean.collectAxioms](https://lean-lang.org/doc/api/Lean/Util/CollectAxioms.html).

Separate proof validity from metric availability. A valid proof whose closure cannot be measured remains a success for correctness and has null compression metrics with a recorded reason. Do not assign zero compression or silently omit it from the success count.

## Source token accounting

Implement one versioned source-token policy and apply it identically to model and reference proofs.

Use the parsed original syntax and source positions, before macro expansion. The parser and syntax APIs expose the structures needed to identify declaration components and source ranges. [Command parser](https://lean-lang.org/doc/api/Lean/Parser/Command.html); [Syntax API](https://lean-lang.org/doc/api/Lean/Syntax.html).

For the fixed template, count W_S over explicit binders and the proposition. Count W_B over the submitted proof term, including by and tactic arguments. Exclude the fixed theorem keyword, evaluator-chosen name and := separator from both. Also retain a full-declaration token count. Deduplicate overlapping token spans; ignore whitespace, comments and synthetic syntax without original source ranges. Define a source token as one original leaf atom or identifier/literal span under the pinned Lean parser policy; count each distinct original span once. Publish small lexical fixtures for qualified identifiers, numerals, strings and Unicode notation. Count local helper statements and their proofs inside W_B. API billing tokens are a separate cost measurement.

Do not split source on the string :=, use the LLM’s tokenizer, or count tokens in pretty-printed elaborated expressions. Keep the original source and token spans so every result can be audited. Hash the token policy.

In helper-library experiments, record each new declaration’s own source counts and the total submitted source cost. Count every submitted helper once, including unused ones, so a short root proof cannot hide expensive support code. Track generated internal declarations in the dependency graph, but do not invent separate source text or double-charge source already assigned to their owning declaration.

## Dependency extraction and arithmetic

Use a trusted Lean extractor to export each declaration’s elaborated type, optional value and separate reference histograms for signature and body. Include environment-added private and generated auxiliary declarations.

ConstantInfo.value? must be requested with allowOpaque := true to retrieve theorem and opaque bodies. [Declaration API](https://lean-lang.org/doc/api/Lean/Declaration.html). Do not use Expr.getUsedConstants or foldConsts for weighted counts: those APIs deduplicate constants. [FoldConsts API](https://lean-lang.org/doc/api/Lean/Util/FoldConsts.html).

For each expression occurrence, count const references and a distinguished Sort node. Traverse applications, binder types and bodies, let types/values/bodies, metadata and projection expressions. Variables and literals contribute no primitive-reference count in this profile. Keep literal byte sizes and whole-expression node counts as separate diagnostics. Reject unresolved metavariables at the validity gate.

Cache a subtree’s complete histogram, adding that histogram at every occurrence. A visited-pointer set that skips repeated appearances would corrupt multiplicity.

Let c_S(u,v) and c_B(u,v) denote the exported multiplicities. On an acyclic graph, compute in dependency order:

```text
For primitive p: U(p) = 1; D(p) = 0
U_S(u) = sum_v c_S(u,v) * U(v)
U_B(u) = sum_v c_B(u,v) * U(v)
U(u) = U_S(u) + U_B(u)
D(u) = 1 + max_v D(v), over referenced dependencies
```

Compute U with arbitrary-precision integers and export decimal strings. Store T0 as an exact numerator and denominator as well as a safely computed logarithm; use bit-length-based logarithms rather than first converting enormous integers to fixed-width floats. Never physically construct the expanded string. Cache by environment hash, boundary-policy hash and declaration type/value hashes, rather than declaration names alone.

For a concrete first implementation, recursively expand declarations owned by Mathlib modules, evaluator-owned mathematical context and candidate-generated declarations. Treat declarations owned by other frozen packages, such as Init, Lean, Std and Batteries, as atomic boundary nodes with U = 1 and D = 0. Treat the distinguished Sort reference as another atomic node. Determine ownership from environment/module metadata, not name prefixes. Publish the exact primitive-name manifest and its hash.

This is an operational boundary chosen for tractability. It changes absolute U, D and T0 values and must be labeled in every result. It is not claimed to reproduce the paper’s exact boundary until checked against an author implementation. An unindexed dependency is an extraction error, never an implicit primitive.

An expression with no counted references has expanded count zero and expression depth zero. An absent body is a different state from a present body with zero references. If an entire nonprimitive declaration has no counted references, flag it and leave its primary U/D unavailable pending a declared policy; do not silently promote it to a primitive. Preserve absent source, absent body, zero count and extraction failure as distinct states.

Select the first benchmark’s reference closures to avoid nontrivial strongly connected components. If a candidate reaches one, keep proof validity but mark the primary U/D metrics unavailable. A later condensation profile may sum inter-component weights and discard internal edges, recording component membership. Such finite component statistics must not be described as terminating literal expansion or silently mixed into the acyclic profile.

## Measurements for every attempt

Store raw measurements, ratios and the evidence needed to recompute them. Never retain only a composite score.

| Record group | Required contents |
| - | - |
| Identity | Task, family, domain, model snapshot, prompt hash, sample index and track |
| Environment | Lean and library pins, primitive hash, token-policy hash, evaluator version |
| Input and output | Trusted signature, approved context, original proof source and content hashes |
| Validity | Status, failure reason, proved-type fingerprint, checked axioms and forbidden-dependency result |
| Paper profile | W_S, W_B, U_S, U_B, D, T0, I0 and log2 values where defined |
| Supporting diagnostics | Elaborated proof-node count, distinct dependency count, helper count and tactic usage |
| Resources | Generation tokens, Lean heartbeats, elapsed verification time, memory and tool calls |
| Provenance | Reference-proof identity, dependency histogram path and verification-log path |

For zero U_B, preserve the exact zero and leave log2 U_B undefined; do not add an arbitrary pseudocount. Ratios with unavailable or zero denominators are null with a reason. For fixed signatures, W_S is constant across models; U_S should also remain constant under the frozen signature environment. A mismatch is a harness error to investigate. If engineering failures make metric coverage incomplete, repair and rerun extraction uniformly from stored verified outputs. Report unresolved coverage gaps before drawing compression conclusions; never infer that the measurable subset is representative.

Also record U_B separately from total U, because a complicated statement can otherwise dominate the score. For helper experiments, report both global depth and depth within newly generated declarations, stopping at the imported library. The latter is an additional diagnostic, not the paper’s D.

## Generation budget and baselines

Begin with 10 pilot goals, all three conditions and four samples: 120 smoke-test generations for one model. Once the pipeline works, run the full 50-goal pilot, resolve measurement and formatting issues, then freeze the protocol. Only then run the untouched 200-goal main test. The smoke goals are a subset of the pilot, not additional test data.

Initial per-attempt limits are 8,192 generated model tokens, 60 seconds of Lean verification with imports already available, 2,000,000 heartbeats and 4 GiB process memory. Allocate a separate 60-second metric-extraction budget. Use identical CPU allocation and process isolation across conditions. Establish the exact CPU quota, start/stop timing boundaries and dependency-cache state in the pilot. Record cold environment indexing separately from warm per-proof work.

Use temperature 0.7 where supported as a pilot starting point. Keep the model snapshot, reasoning setting, sampling parameters and output-token cap fixed within every comparison; log any hidden reasoning tokens that the provider reports. Tokenizer and reasoning-budget differences limit exact cross-model compute matching, so make within-model prompt comparisons primary. Seeds are useful for reproducibility where supported, but matching seed numbers across different prompts does not produce matched random trajectories.

Generate four independent outputs for each task and condition, giving 800 outputs per main-test arm and 2,400 per model. Each sample starts from the same task prompt in a fresh conversation. No feedback-driven repairs, best-of-four selection or cross-sample memory are allowed in the core experiment.

Randomize or interleave condition order across tasks and record timestamps. Pin provider model versions where possible; if an unpinned backend changes during the run, preserve version/time cohorts and do not silently pool them. Retry transient transport failures under a fixed retry policy using the same attempt ID. Refusals, malformed responses and truncated outputs are model-attempt failures; an unresolved infrastructure outage makes the scheduled run incomplete and must not disappear from accounting.

Use these comparison anchors and controls:

| Condition or baseline | Role | Main-test cost per model |
| - | - | - |
| A: ordinary correctness | Standard proof generation | 200 × 4 = 800 outputs |
| B: mathematical compression | Test the proposed instruction | 200 × 4 = 800 outputs |
| C: fewest Lean source tokens | Isolate ordinary concision | 200 × 4 = 800 outputs |
| Verified reference proof | Same-task measurement anchor, with no optimality claim | One checked proof per task |
| Fixed trusted tactic portfolio | Establish what existing automation solves | One fixed-budget portfolio run per task |

Freeze the tactic portfolio and its total verification budget in the pilot; it is an automation baseline, not a matched LLM-compute baseline. Broad trusted automation remains available in the practical core track. Record tactic usage and checking cost so a short automation wrapper is visible. A restricted-automation experiment is optional and must apply one declared policy to all models, conditions and reference proofs.

## Frozen run configuration

Keep one machine-readable configuration next to the manifests. This example is a proposed starting configuration; unresolved values must be supplied and validated before launching a run.

```yaml
experiment: lean_math_compression_v1
metric_profile: paper_aligned_v1
dataset:
  pilot_goals: 50
  main_goals: 200
  split_unit: theorem_family
conditions: [standard, mathematical_compression, source_concision]
samples_per_goal_condition: 4
generation:
  mode: single_shot
  output_format: lean_proof_term
  max_output_tokens: 8192
  temperature: 0.7
  model_snapshot: RESOLVE_BEFORE_RUN
  reasoning_setting: RESOLVE_BEFORE_RUN
environment:
  lean_toolchain: RESOLVE_BEFORE_RUN
  mathlib_commit: RESOLVE_BEFORE_RUN
  lockfile_hash: RESOLVE_BEFORE_RUN
  primitive_manifest_hash: RESOLVE_BEFORE_RUN
  source_token_policy: original_syntax_v1
validation:
  wall_seconds: 60
  max_heartbeats: 2000000
  memory_mib: 4096
  cpu_quota: RESOLVE_BEFORE_RUN
  permitted_axioms: [propext, Classical.choice, Quot.sound]
extraction:
  wall_seconds: 60
  unknown_dependency: error
  dependency_cycle: metrics_unavailable
analysis:
  bootstrap_replicates: 10000
  confidence_level: 0.95
  cluster_unit: theorem_family
  primary_contrasts: [compression_vs_standard, compression_vs_concision]
```

Also record dataset and prompt hashes, response-adapter version, retry policy, bootstrap seed, permitted tactic/metaprogramming policy and the exact success-classification code. Validate axiom names and resource controls against the pinned toolchain. Any change after the main test begins creates a new protocol version; preserve the original run rather than overwriting it.

## Comparisons and statistical reporting

Report per-attempt correctness over every scheduled model attempt: average the four binary outcomes within each task, then average tasks with equal weight. Also report first-attempt correctness and the fraction of tasks with at least one success in four attempts. The latter is a four-sample success rate, not the quality of a single generation. For every arm, separately report metric availability among valid outputs and reasons for missing metrics.

For the primary compression analysis, let m(i,p) be the mean log2 T0 over valid, measurable samples for task i in condition p. All four scheduled samples are eligible; never choose the maximum-scoring sample. If an arm has no valid measurable sample for that task, its m(i,p) is missing. For a contrast, take the mean of m(i,B) − m(i,A), or m(i,B) − m(i,C), across tasks measurable in both arms. Exponentiating that mean gives a geometric T0 ratio.

This estimates compression conditional on success and metric availability. Equal task weighting prevents easy tasks with four successes from dominating tasks with one. A first-valid-proof comparison may be included as a clearly labeled secondary deployment scenario, but it is not the primary statistic.

Publish the paired-task count, domain/source composition, exclusive solves, shared failures and missing-metric counts alongside each contrast. Different contrasts can have different paired subsets. Add a sensitivity analysis on the common A/B/C measurable-task intersection; do not imply that conditioning removes selection bias. Cross-model comparisons require the same care and remain secondary.

Fix one verified reference proof per task before generating any evaluated output. Score it in the identical environment with the same source and dependency policies. The reference supplies a reproducible anchor; it need not be minimal and must never be chosen to favor a condition. Report attainment saturation at each threshold: if almost every valid proof exceeds an unusually verbose reference, that threshold is not very informative.

Report the raw distributions of W_B, U_B, total U, I0 and D, with medians and interquartile ranges; use logarithms for positive, very large counts. Include same-task source-length ratios and depth differences. Keep zero U_B as zero with its logarithm undefined.

For the primary mean-log T0 contrast, use exactly the same samples and task weights to decompose the result:

```text
log2 T0 = log2(U_S + U_B) - log2(W_S + W_B)

paired mean change in log2 T0
= paired mean change in log2(U_S + U_B)
- paired mean change in log2(W_S + W_B)
```

This decomposition is exact for matched observations with identical averaging weights. It does not generally hold if three medians are subtracted independently. Show whether an apparent gain is explained by fewer source tokens, larger expanded dependencies, or both. At a fixed signature, I0 is essentially a rescaling of proof-source length; treat it as a descriptive field rather than an independent quality reward.

Add an all-attempt robustness check called verified compression attainment. For thresholds t = 0, 1 and 2, an attempt attains the threshold only if it is valid, measurable and has log2 T0(output) − log2 T0(reference) at least t. These correspond to at least 1×, 2× and 4× the reference ratio. Divide attainment counts by all scheduled model attempts and average tasks equally. Invalid and unmeasurable attempts do not meet the event; do not invent numerical T0 values for them. Show metric coverage alongside this curve. These thresholds are proposed diagnostics, frozen before the main test, not quantities defined by the paper.

Use 10,000 cluster-bootstrap replicates over theorem families for 95% confidence intervals, retaining all tasks, conditions and samples within a resampled family. Stratify by domain when families are nested within domains. Freeze family assignments and the bootstrap seed. Recompute each paired statistic within every replicate and report unstable or undefined replicates caused by sparse support.

Give intervals for success-rate differences, conditional mean-log compression differences and attainment-rate differences. The B-versus-A and B-versus-C contrasts are prespecified. If confirmatory p-values are reported for both, use a declared multiple-comparison adjustment such as Holm; otherwise emphasize effect sizes and intervals. Treat per-domain, fresh-goal and tactic subgroups as exploratory unless powered separately.

Use the pilot’s family-level variability and success rates to assess precision or simulate power before fixing the main sample size. Two hundred tasks is a practical starting size, not a guaranteed power calculation. If larger numbers are needed, decide that before inspecting main-test results. Report the number of independent theorem families as well as the number of tasks. The point estimates weight tasks equally; resampling families handles dependence and does not change the estimand to equal-family weighting.

Produce a correctness table, a paired per-task metric table, a wrapped-size versus expanded-size scatterplot, distributions by domain and a list of the largest metric changes with their Lean proofs. Regression slopes across heterogeneous tasks are secondary descriptive plots; they are not causal measures of how much information one extra generated token creates.

Do not create a weighted sum of pass rate, T0, I0 and D. The deliverable is a reproducible profile with separate statements about reliability, source concision and dependency expansion.

For a concrete optional decision rule, call B promising only if: its correctness is noninferior to both controls under a prespecified 5 percentage-point margin; its conditional T0 ratio versus C has a point estimate of at least 1.20 and a 95% interval excluding 1; and its B-versus-A effect is also positive with an interval excluding zero on the log scale. Use a one-sided 95% lower bound above −0.05 for each correctness comparison. Freeze this rule after the pilot if a binary decision is needed. The 5-point and 20% choices express practical preferences, not conclusions from the paper, and the planned sample size may leave the result inconclusive.

Even if that rule passes, inspect all-attempt attainment and the decomposition before interpreting the mechanism. Conditional compression comparisons can select different kinds of successful proofs despite similar aggregate correctness.

| Observed outcome | Supported interpretation |
| - | - |
| B shortens source, matching C’s behavior | The instruction mainly induces concision |
| B improves T0 beyond C, with relevant reuse and stable correctness | Evidence for stronger use of existing abstractions under this environment |
| B improves T0 through irrelevant dependency growth | Metric inflation; no evidence of better reasoning |
| B improves conditional compression but loses solves | A reliability/compression tradeoff |
| B relies on short automation calls | Practical source compression delegated to trusted automation |
| Effects are near zero with wide intervals | Inconclusive at this sample size |
| Effects are near zero with tight intervals | Little prompt effect within the tested task distribution |

None of these outcomes alone establishes discovery of new mathematical insight, minimum proof length, or a change in the model’s internal reasoning.

## Metric stress tests

Before using these measurements to judge an improvement, run controlled transformations and inspect the expected result.

| Fixture or transformation | Required observation |
| - | - |
| Whitespace and comments | Source-token counts remain unchanged |
| Ordinary local identifier renaming | Token counts remain unchanged under the declared lexical policy |
| Shared expression referenced twice | Reference histogram doubles; pointer sharing must not erase an occurrence |
| Hand-computable weighted diamond graph | Dynamic-programming counts match explicit expansion on the small fixture |
| Theorem and opaque declaration | Proof/value dependencies remain visible |
| Generated auxiliary declaration | Dependencies are included without invented source cost |
| Direct or indirect sorry and custom axiom | Validity gate rejects |
| Modified target or an imported target alias | Target/dependency gate rejects |
| Short automation wrapper | Small source cost, potentially large proof/dependency cost; report both |
| Redundant proof detour or huge irrelevant dependency | Exposes numerator inflation rather than receiving a “better reasoning” label |
| Alias chain | Depth may increase without useful representation savings |
| Refactoring into helpers | Root score changes are shown beside total submitted cost |
| Nontrivial dependency cycle | Primary metric becomes unavailable under the declared policy |
| Cached and uncached extraction | Identical histograms and counts |
| Zero counted body references | U_B remains exactly zero; its logarithm is null |
| Missing dependency or missing source ownership | Explicit diagnostic; no fabricated primitive or source count |
| Counts above floating-point range | Exact integer and rational exports remain correct |
| Fixed-signature hash across arms | W_S and U_S agree exactly |
| Truncated output and multiple proposed proofs | Frozen format policy applies without cherry-picking |
| Valid proof with extractor failure | Correctness remains success; metric status is unavailable |
| Interrupted/resumed run | Attempt IDs deduplicate records and preserve all scheduled outcomes |

Review the 10 largest positive and 10 largest negative paired T0 changes, plus 20 randomly sampled paired tasks, using a frozen sampling seed. Include all available condition outputs for those tasks. Where feasible, hide model and condition labels from reviewers. Label the apparent mechanism: ordinary brevity, useful existing-lemma reuse, local factoring, automation, irrelevant dependency growth, or suspected answer access. Keep the corresponding source and dependency evidence.

Manual review explains the measurements; it is not a hidden subjective score override. Keep valid outliers in the primary analysis. If a harness bug or a predeclared validity violation is discovered, apply the correction to every affected arm and rerun the relevant checks under a versioned amendment. Do not retrospectively remove inconvenient examples.

## Implementation architecture and stored evidence

Implement the measurement path as separate components so a scoring bug can be fixed without paying for new generations.

| Component | Input | Output and responsibility |
| - | - | - |
| Dataset builder | Source goals, family split and environment pin | Fixed target templates, allowed/forbidden environments and private verified references |
| Environment indexer | Pinned declarations and primitive policy | Module ownership, type/body reference histograms and cached base U/D |
| Generation runner | Task, condition, model and sample index | Immutable raw messages, provider metadata and stable attempt ID |
| Candidate validator | One output and trusted task template | Checked declaration or classified failure; target and axiom evidence |
| Lean extractor | Verified declaration and source | Original token spans, new/auxiliary declarations and weighted references |
| Metric engine | Base index plus candidate graph | Exact U counts, W counts, depth, rational ratios and availability flags |
| Analysis/report runner | Frozen manifest and all attempt records | Tables, uncertainty intervals, plots, audit sample and report |

Cache the trusted base environment. Evaluate candidates in fresh processes with no mutable environment carried from another output. Keep the base cache read-only during candidate execution. Record cache versions and invalidate entries when declaration, environment or policy hashes change.

Suggested interfaces, with language-specific details left to implementation:

```text
build_tasks(dataset_spec, environment_pin) -> task_manifest
index_environment(environment, metric_policy) -> base_index
generate(task, condition, model, sample_index) -> attempt_record
validate(task, attempt_record, validation_policy) -> validation_record
extract(task, validation_record, base_index) -> candidate_graph
score(candidate_graph, source_spans, metric_policy) -> metric_record
analyze(task_manifest, all_attempt_records, analysis_config) -> report
```

Store one task record with task ID, family ID, domain, split, origin, reference-length/depth stratum, target source, explicit binders, premise-context bytes, import/module manifest, forbidden declarations, private reference proof and relevant hashes. Store the raw declaration names in audit data even if display names are anonymized. Model-visible files must be separate from private reference data.

Store one attempt record for every scheduled slot, including failures. Required identity is experiment version, task ID, model snapshot, condition and sample index. Attach original/adapted response, generation settings, usage, timestamps, verification status, metric status and evidence paths. Use a separate transport-attempt log for retries so a network retry does not masquerade as a new model sample. Protect raw data from overwrite and maintain an append-only amendment log.

A synthetic arithmetic fixture makes the score schema concrete. It is a hand-built graph, not an observed Lean result: p and q are primitive; A references p twice and q once, so U(A) = 3 and D(A) = 1. B references A four times and p once, so U(B) = 13 and D(B) = 2. A target whose signature references A once and whose body references B twice has U_S = 3, U_B = 26 and D = 3. If its chosen source fixture has W_S = 5 and W_B = 8, T0 = 29/13 and I0 = 8/5.

```json
{
  "record_kind": "synthetic_metric_fixture",
  "metric_profile": "paper_aligned_v1",
  "validity_status": "not_applicable",
  "metric_status": "available",
  "W_S": 5,
  "W_B": 8,
  "U_S_decimal": "3",
  "U_B_decimal": "26",
  "D": 3,
  "T0": {"numerator_decimal": "29", "denominator_decimal": "13"},
  "I0": {"numerator_decimal": "8", "denominator_decimal": "5"}
}
```

Real records additionally carry log2 values, toolchain/policy hashes, dependency histograms, source-span ownership and validation logs. For generated auxiliary declarations without independent source text, store source counts as null with their owning declaration; zero would misleadingly suggest a measured empty representation.

## Report and reproducibility package

The first report should answer five questions in order: does the prompt preserve correctness; does it change measured T0; how much is explained by W versus U; does it differ from a shortest-source instruction; and which proof mechanisms explain the result?

| Output | Required contents |
| - | - |
| Protocol bundle | Config, task/family split, three prompts, model pins, context and metric-policy hashes |
| Raw evidence | All scheduled attempts, responses, validation logs, token spans and candidate dependency histograms |
| Correctness table | A/B/C success, first-attempt and four-sample solve rates, failures, resource use and metric coverage |
| Paired comparison table | B−A and B−C log effects, geometric ratios, intervals and exact paired denominators |
| Robustness table | All-attempt reference attainment, A/B/C common-task sensitivity and coverage gaps |
| Metric breakdown | W_B, U_B, total W/U, I0 and D; fresh/library and domain summaries |
| Plots | Success versus compression profile, source size versus expanded size, paired effects and attainment curves |
| Qualitative audit | Frozen random/outlier sample with proof source, dependency evidence and mechanism labels |
| Reproduction entry point | Commands to regenerate tables and figures from saved records without API calls |

Every figure must name its population: all scheduled attempts, valid attempts, measurable valid attempts or paired tasks. Include task/family counts and versions. Do not put proofs measured under different primitive boundaries or library pins on an unlabeled common numerical scale. Keep failed outputs and metric-unavailable records in the released accounting even when a plot cannot display them.

## Run size, time and cost planning

The proposed pilot requires 50 × 3 × 4 = 600 outputs per model, and the main run requires 200 × 3 × 4 = 2,400. Thus the planned total is 3,000 outputs per model, or 9,000 for three models. Reuse smoke-run outputs within the pilot only if prompts and settings remain unchanged; otherwise archive them separately and include their cost as development overhead.

Estimate money from observed pilot usage and actual provider rates at launch. For illustration only, an average of 2,000 billed input tokens and 1,000 billed output tokens would yield 6 million input and 3 million output tokens per model across pilot plus main. Multiply by the selected providers’ applicable rates, adding separately billed reasoning, retries and setup work. The nominal 8,192-token output cap across 3,000 calls allows up to 24,576,000 generated tokens; it is not an estimate of typical usage or of separately capped hidden computation.

At a 60-second verification cap, 3,000 attempts have an upper allocation of 50 worker-hours of verification per model. A separate 60-second extraction cap adds up to another 50 worker-hours if all attempts require it and hit the cap. These are cap-based allocations, not expected runtimes; indexing, environment builds and reference checks are additional. Measure median and p95 generation, checking and extraction times in the pilot. Choose concurrency that preserves the stated CPU and memory allocation, then estimate wall time from those measurements and provider quotas.

Use the pilot to decide whether the main run is affordable and precise enough. If not, change scope before main-test generation—such as evaluating one model first—while retaining all three conditions and consistent within-model budgets.

## Optional reusable lemma experiment

After completing the core prompt experiment, optionally add 20 task families, each with five development goals and five hidden transfer goals. This yields 100 development and 100 transfer goals. It asks whether a model contributes reusable abstractions, a different question from compactly solving a single supplied theorem. It is not required to answer the user’s initial proposal.

Let a model submit up to five new definitions or lemmas per family, with a total cap of 4,096 Lean source tokens including their signatures and proofs. Verify every submitted declaration and freeze this library before exposing hidden goals. Keep the underlying imports identical.

Measure downstream proving with and without the candidate library. Use both the same model and a fixed independent solver where possible. Neither solver sees reference proofs. Preserve equal inference and Lean budgets. Transfer tasks can inspect and use the verified helper library; they cannot alter it.

Record T0, I0 and D for every source-backed new declaration. Add distinct downstream task-family usage and an accounting measure:

```text
net saving
= total source cost of baseline proofs
- source cost of the new library
- total source cost of proofs using that library
```

Include the complete library cost, even for unused declarations. Compute savings only on the shared solved transfer goals, show that denominator explicitly, and separately report new solves and regressions. Report break-even usage and savings per additional task. A negative result is meaningful.

This added measure is an evaluation extension. It distinguishes reusing existing Mathlib infrastructure from contributing an abstraction whose cost is repaid across multiple problems.

## PageRank and expansion coverage

Run I1 only in the library experiment or on a fixed multi-theorem project with real reuse edges. A newly generated theorem with no incoming references receives stationary mass proportional to its teleportation weight; scoring isolated proof roots would therefore mostly recycle the local score.

For an optional operational PageRank profile, freeze these choices on pilot data: alpha = 0.85 for following references, beta = 0.5 for mixing T0 and I0, percentile transforms fitted to a fixed reference corpus, and epsilon = 1e-12 as a positive teleport floor. For source-backed eligible node u, set raw weight to beta × percentile(T0(u)) + (1 − beta) × percentile(I0(u)) + epsilon, then normalize over the graph. Nodes without source-backed scores receive only epsilon. These are explicit evaluation conventions, not claimed reproductions of the paper’s exact normalization. Never fabricate W values for generated internal nodes.

Use reference multiplicities to normalize each node’s outgoing edge probabilities. Redirect dangling-node mass to the same teleport distribution. Iterate until the L1 change is below 1e-10 or a declared iteration limit is reached; report nonconvergence. Run alpha sensitivity at 0.70 and 0.95.

Freeze graph construction, normalizers and the background node set. Record the number of generated nodes; raw mass can change when a library adds many aliases. Treat per-node and generated-library mass as diagnostics, accompanied by actual held-out reuse and total source cost. Do not compare bare PageRank values across unrelated corpora or different task counts.

The monoid expansion function f(s) cannot be directly estimated from an ordinary finite set of Lean proofs: observing successful examples cannot establish universal coverage of a primitive-length ball. Report f as not measured in the Lean benchmark.

If desired, build a separate finite synthetic monoid suite where all relevant elements can be enumerated and minimum macro lengths computed exactly. For Lean, an optional coverage-versus-source-budget curve can report the fraction of fixed tasks solved within a total submission budget. Call that empirical task coverage, not f(s).

## Build sequence and release criteria

| Stage | Deliverable | Acceptance criterion |
| - | - | - |
| Specification lock | Draft prompts, metric policy, task rules and run config | Every unresolved implementation choice has an owner; required pins resolved before execution |
| Extractor and validator | Lean exporter, isolated runner and metric engine | Hand-computable, validity and bookkeeping fixtures pass |
| Dataset | 50 pilot and 200 main goals with references | Family split frozen; answer access audited; all references verify and can be measured |
| Smoke run | 10 pilot goals × 3 arms × 4 samples | Every number traces to source and dependency evidence; format handling works |
| Full pilot | 50 goals × 3 arms × 4 samples per model | Runtime, metric coverage and interval precision assessed; final settings preregistered |
| Main evaluation | 200 goals × 3 arms × 4 samples per model | Scheduled outcomes accounted for; no outcome-driven changes to protocol |
| Analysis and audit | Paired contrasts, all-attempt checks, plots and proof examples | Denominators, selection effects and numerator inflation are explicit |
| Release | Versioned evidence bundle and report | Reanalysis reproduces every published statistic without new model calls |

A planning allowance is roughly 8–15 engineer-days for someone comfortable with Lean metaprogramming and Python, assuming a usable task source and existing model access. Extensive fresh-problem authorship, unfamiliar checker integration or difficult extraction conventions can extend that estimate. Treat it as a workload estimate, not a promised implementation time.

Assign explicit ownership for Lean extraction/validation, dataset and leakage review, model-run orchestration, and statistical analysis. One person can fill several roles; independent review of the metric fixtures and target-validation interface is useful before the expensive run.

Before the main run, sign off on the task and family manifest, frozen reference proofs, model snapshots, all three prompts, context bytes, source-token fixtures, primitive manifest, validity and axiom policy, budget configuration, failure/retry policy, statistical estimands and report template. Archive the preregistration and hashes.

The core release is complete when scores can be recomputed from stored source and weighted graphs, correctness checks are auditable, and compression changes can be distinguished from source brevity, expanded-dependency growth and success selection. Optional transfer and PageRank studies can follow without delaying this first answer.
