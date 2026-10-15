# Methods and provenance audit

Prepared from the completed local evidence, source implementation, supplied evaluation plan, and primary literature on 15 October 2026 UTC. This is a read-only audit: no new provider calls, Lean executions, verification changes, or outcome exclusions were made. The new diagnostic files in this directory are explicitly post hoc. Citations and pinpointed local evidence are indexed in `source-citations.json`.

## Assessment

**Share with substantial methodological caveats.** The experiment supports reproducible comparisons of single-shot Lean proof acceptance, submitted-source length, and a declared weighted dependency-expansion ratio. It does not establish mathematical creativity, minimum proof size, general reasoning quality, certified novelty, or a causal effect of increasing the output budget. The most important metric warning is empirical: very large T₀ increases can be dominated by implicitly inserted typeclass instances rather than a proportionate improvement in the written argument.

## Three distinct cohorts, not one pooled experiment

| Cohort | Questions × models × prompts × samples | Scheduled | Accepted | Accepted with measurable compression | Output ceiling | Checker policy |
|---|---:|---:|---:|---:|---:|---|
| Engineering smoke test | 10 × 8 × 3 × 4 | 960 | 609 | 602 | 8,192 | Lean 4.34.0-rc1, earlier Mathlib/boundary; 60s validation/60s extraction, 4GiB, CPU1 |
| OM100 initial | 100 × 8 × 3 × 4 | 9,600 | 2,445 | 2,445 | 8,192 | Lean 4.34.1; v2_threads1; 180s validation/900s extraction, 8GiB, CPU2 |
| OM100 provider-maximum | 100 × 8 × 3 × 4 | 9,600 | 3,831 | 3,831 | 128,000 | Same OM100 mathematics, scorer, per-proof limits, and v2 runtime |

There are 20,160 scheduled outcomes overall, but pooling these rows into a single benchmark would mix task distributions, mathematical environments, and generation policies. The smoke set consists of ten easy constructed exercises, labeled provisional controls; its `fresh` metadata is not a training-contamination certificate. Seven accepted smoke proofs lack available primary compression measurements and must remain accepted with null metrics. The two OM100 cohorts use the same 100 prepared targets and identical prompt bytes but fresh output identities. The higher-budget run additionally changed streaming transport and OpenAI service tier, and was performed after the first run was inspected. It is a separate exploratory replication under a different operational budget, not a randomized budget intervention. [E01–E05]

The common frozen model identifiers are `gpt-6.1-sol`, `gpt-6-sol`, `gpt-6-luna`, `gpt-6-astra`, `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, and `claude-fable-5-1`. All requested high effort, omitted temperature, and used four fresh conversational samples per question/condition. Claude used adaptive thinking. A common effort label or token integer does not equalize inference compute, tokenization, or hidden reasoning. The manifests call the identifiers pinned snapshots; these records establish frozen requested identifiers, not independently hashed provider weights. [E04–E05]

## What was actually selected

OM100 preserves the user's exact 100 supplied candidate IDs, drawn from 92 source files in 19 upstream result families. There are 25 questions in each supplied group: algebra, analysis, combinatorics, and computer science/probability. These are selected component lemmas from research developments, not 100 full open research problems. They are not a random sample of all theorems, all OpenAI Math manuscripts, or all mathematical difficulty levels. Difficulty labels were provisional curator judgments, not an independent calibration of difficulty. Families contain shared constructions and preceding lemmas; 100 questions and four samples must not be described as 400 independent mathematical problems. [E06–E09]

The source is OpenAI's public `math` repository at commit `adc7f1241b42e322a6451854ab7e4b4c146bf78a`, with FineEnvs metadata revision `d7056fd31c19fd1a0aed0a0bc3e99026d412b2ca`. The upstream README describes research artifacts produced by an internal OpenAI model and a collection with varying verification status; it is not a clean human-authored, model-unseen benchmark. The repository's pinned license is Apache 2.0. FineEnvs serves as upstream provenance metadata here; this audit did not independently retrieve its inaccessible pinned web card or rerun its parent oracle. The actual selected references were instead checked in the local experiment. The candidates' claimed collection release date is6 October 2026, while earliest publication of each mathematical lemma and model exposure remain explicitly unknown. A recent repository release cannot establish absence from pretraining, earlier private development, post-training, or prior mathematical literature. [S2–S4, E06–E09]

The supplied original plan proposed a 50-goal pilot and a separate 200-goal test, five domains,150 reconstructed library goals plus 50 newly authored goals, family separation, and later protocol freezing. The executed OM100 cohort is explicitly `development=true` and `formal_250_question_preregistered_study=false`; all 100 selected library goals were evaluated without a pilot/test family split. Its manifests use `split=main` as a technical label, not evidence that this proposed main-study design or an external preregistration was completed. No independent blinded proof-quality review, formal power calculation, or public preregistration should be claimed. [E01,E04,E08]

## Task reconstruction and model-visible information

Preparation reconstructed each source environment immediately before its target, excluding the target's defining module and original declaration from allowed imports. Legitimate earlier definitions and proofs remain trusted compiler context. Explicit section binders and universes were materialized, and whole target types were kernel-compared before/after transformation. Original reference proof bytes were preserved. Two layout repairs, OM100-041 and094, parenthesized multiline `let` propositions and added two signature tokens; both had recorded kernel type equivalence. OM100-048 and078 required closing original variable scopes and reopening the same namespace/active opens to avoid duplicated section parameters. These are pre-generation task transformations, not repairs of model outputs. All 100 targets were retained. [E09–E11]

Model prompts contain the toolchain, approved imports, fixed premise interfaces, one condition instruction, and the explicit target with `<PROOF>`. They do not contain the target reference proof, curator proof summary, prior attempts, or validator feedback. Models had no live retrieval, execution, or proof-repair loop. Earlier theorem interfaces omit their proof bodies; appropriate definitions can be supplied. The final premise policy is **reference-informed**: target/context candidates and constants used by the reference inform retrieval, with the same final bytes for every model and condition. The exported context cap is 56,000 bytes, within a 64,000 byte configured prompt-context cap. This supplies a material scaffold and favors available relevant library interfaces; it must not be described as unaided discovery or retrieval blind to the reference. [E10–E12]

The original target's absence, prohibited dependencies, reference-byte separation, literal/fuzzy proof screens, and prior-environment interface membership were audited. These checks do not exclude every logically equivalent theorem or semantic shortcut in the permitted library. Public target identities and source material do not establish training exclusion. Upstream artifacts being generated by an OpenAI model create a plausible provenance asymmetry, but the data cannot identify its effect or demonstrate training leakage. [E09–E12]

The exact common system and three condition messages remain in `src/lean_compression/prompts.py` and frozen manifests. Standard requests a correct proof; mathematical compression requests compact use of definitions, lemmas and local factoring while avoiding irrelevant expansion; source concision requests the fewest Lean source tokens. The compression instruction never tells models to maximize a numerical ratio. OM100 uses a frozen mechanical adapter that removes only one complete outer code fence or `<PROOF>` wrapper. It never chooses among alternative answers or repairs Lean. Smoke used the identity adapter, another reason not to pool formatting outcomes. [E04–E05,E12]

## Relationship to the cited paper

Aksenov, Bodnia, Freedman and Mulligan's paper defines reductive compression T₀=(U_S+U_B)/(W_S+W_B), deductive compression I₀=W_B/W_S, and a PageRank-style I₁. Its dependency graph counts repeated constants and `Sort`, expands weighted dependencies, and uses parser-source length; its corpus treatment collapses strongly connected components. Section 5.1 proposes J₀=βT₀+(1−β)I₀ after normalization, without fixing that normalization. The present evaluator is a **paper-aligned operationalization**, not a numerical replication of the authors' full Mathlib study. Its library snapshot, boundary, source-span conventions and cycle policy are independently declared. [S1; E13–E15]

### Precisely what the implemented quantities count

- **W_S:** distinct original parser leaf spans in explicit binders and proposition. **W_B:** spans in the submitted proof body, including `by`, tactic arguments, local statements and local proofs. Fixed theorem keyword, synthetic name, `:=`, comments, whitespace and synthetic syntax are excluded. These are Lean parser spans, not API tokens, characters, elapsed reasoning steps or a shortest equivalent proof.
- **U_S and U_B:** weighted recursive counts from the root's elaborated type and proof expression. Constants and `Sort` are counted per occurrence; applications, binders, lets, metadata and projections are traversed. Variables/literals do not independently add a counted primitive reference. For each expanded referenced declaration, both its type and available body contribute. Thus repeated references repeatedly charge inherited dependency machinery; U is not unique dependency count, AST size, executable computation count or a physically materialized normalized proof.
- **Boundary:** recursively expand Mathlib modules, evaluator-owned mathematical declarations, generated candidate declarations, and the 457 exact listed OAI modules. Other frozen packages—such as Init, Lean, Std and Batteries—and `Sort` are atomic with U=1. Ownership comes from environment metadata. Unindexed dependencies are errors, not invented primitives. The boundary materially determines absolute U, depth and T₀.
- **Arithmetic:** arbitrary-precision integers and exact numerator/denominator fields; floating logarithms are derived display values. D is the longest path to the declared boundary. Missing source, absent body, present zero-reference body, extraction failure and dependency cycles are distinguished. The implemented primary profile rejects cyclic/zero-reference nonprimitive closures as unmeasurable; it does not silently apply the paper's SCC condensation.
- **Interpretation:** with fixed W_S, larger I₀ means a longer written proof. It is a descriptive proof-to-statement ratio, not a concision reward. Larger T₀ can result from reducing W, increasing U, or both. Different valid proofs of one statement can have radically different U; there is no fixed information payload whose compression ratio is being universally optimized. [E13–E15]

The dashboard's model aggregation and per-question ranking are later analysis choices, not quantities defined by the paper. Geometric means use valid measurable samples; midranks compare models on the selected shared-question intersection; question ranks receive equal weight. Rank points are a linear rescaling of rank, not percent compression. Its J₀ view normalizes each question's model-level T₀ and I₀ by the selected-cohort maximum before blending. This is explicitly exploratory and cohort-dependent. No I₁/PageRank or α experiment was computed: one would need a specified corpus-wide graph, all required node weights/source counts, normalization and sink treatment. [E16]

## New direct check: what drives the scores?

`metric-mechanism-audit.json` independently examines the final DTOs; it does not modify their scores. W_S and U_S are exactly invariant within each measured question in every cohort. The following diagnostics pool all conditions/models only to investigate the metric, with attempt weighting and no inferential claims:

| Diagnostic | OM100 8,192 | OM100 128,000 |
|---|---:|---:|
| Questions with at least2 measured outputs | 86 | 100 |
| Exact total-U invariance among those questions | 0 | 0 |
| Questions whose max/min U differs by at most1% | 35 | 39 |
| Questions whose max/min U differs by at most10% | 42 | 42 |
| Questions whose max/min U is at most2× | 69 | 70 |
| Median within-question log₂U range | 0.194 bits | 0.328 bits |
| Between-question share of observed log₂U sum of squares | 99.9888% | 99.9553% |
| T₀ order agrees with shorter source, non-tied within-question pairs | 90.38% | 90.25% |
| Median U_S/(U_S+U_B) | 4.91×10⁻⁷ | 1.10×10⁻⁷ |

The enormous absolute magnitudes overwhelmingly differ by question. On many questions, within-question ordering mostly tracks source concision, but important exceptions exist. This does **not** mean the explicit statement numerator dominates: no measured OM100 output has U_S greater than one quarter of total U. Its dependency structure can still recur through binder types and implicit instances in the elaborated body. The variance decomposition establishes a question-associated scale, not the causal contribution of imports; no alternative-boundary ablation was run. Float log differences can round tiny nonzero differences to zero, while invariance and1%/10%/2× checks use exact integers. [E17]

### Two retained outlier case studies

`outlier-audit.json` records four exact attempt IDs, source-file hashes, submitted proof files, full metric components and leading direct-body dependency contributions. Fresh Python dynamic programming on the saved graphs exactly reproduces every U_S and U_B; no Lean rerun was performed.

**OM100-065.** GPT6Sol standard sample 3 (`5138f5ed…`) has W_B=1669, W_S=114, log₂T₀=102.541697. GPT6Sol compression sample 4 (`6cb7221d…`) has W_B=1198 and log₂T₀=155.276744. The 52.735-bit increase decomposes into 52.293 bits of numerator growth plus 0.443 bits from smaller total source. In the high-score body, two occurrences each of `Real.normedField` and `Real.normedCommRing` account for approximately half of U_B each, together nearly all of it. The submitted proof includes a reciprocal-limit route:

```lean
have hinv : Tendsto
    (fun q : ℕ => (1 : ℝ) / (↑(wBlock q).card : ℝ))
    atTop (𝓝 0) :=
  Filter.Tendsto.div_atTop tendsto_const_nhds block_card_atTop
```

The low-score proof instead constructs a sufficiently large block and uses elementary ceiling/density inequalities. Both are accepted. The graph demonstrates expansion of deep normed-structure instances, not 52 bits of additional mathematical insight.

**OM100-100.** Luna compression sample 4 (`fc206e10…`) has W_B=1051, W_S=270, log₂T₀=99.900851. Fable source-concision sample 2 (`836b3b45…`) has W_B=313 and log₂T₀=125.711905. The 25.811-bit difference decomposes into 24.631 bits of numerator growth plus 1.180 bits from smaller total source. A single occurrence of `Real.instStarOrderedRing` supplies 99.999993206% of the high-score U_B. The visible proof uses aggregate-sum lemmas and finishes:

```lean
nlinarith [mul_lt_mul_of_pos_left hdegree hX,
  mul_lt_mul_of_pos_left hbad hD, mul_pos hX hD]
```

That instance name is not explicitly written in the submitted proof. Its insertion is visible in the recorded elaborated body. This audit cannot identify the exact surface expression that introduced it without re-elaboration; the excerpt is context, not a proved causal localization. These examples show sensitivity to elaborated implicit library infrastructure, with no arithmetic mismatch found. Neither example is evidence of intentional score gaming, and neither is removed from analysis. [E18]

## Verification, missingness and recovery

A candidate is parsed as a complete term, inserted into the fixed declaration, elaborated, compared to an independently elaborated trusted signature, audited for forbidden dependencies/axioms, and kernel-replayed. The allowed axioms are `propext`, `Classical.choice`, and `Quot.sound`. Admissions, changed targets, new axioms and prohibited executable metaprogramming are rejected. Broad trusted tactics remain available, so concise source can delegate work to automation. Docker disables network, uses a read-only filesystem, drops capabilities, and applies PID/memory/CPU limits. This is a bounded formal acceptance test; timeouts/resource-limit outcomes are not mathematical disproofs. [E14–E15]

The original OM100 verifier encountered worker thread/PID infrastructure problems. An explicit versioned wrapper amendment adds `-j1`, preserves mathematical sources, and separately binds verification environment to original generation identity. All 9,600 outputs and 100 references were uniformly processed under `v2_threads1`; the 1,474 retired v1 records remain historical and are not additional samples. The provider-maximum cohort uses the same final verification policy and reuses the 100 verified references by hash. A terminal record for an ineligible truncated answer does not mean Lean validated it: truncated outputs bypass proof checking. Infrastructure incidents were retained rather than silently labeled model errors. [E19–E20]

For OM100 8,192 there are 4,633 completed nonempty submissions and 4,967 truncations; acceptance is 2445/9600 scheduled versus 2445/4633 completed. For 128,000 there are 9,477 completed nonempty submissions and 123 truncations; acceptance is 3831/9600 scheduled versus 3831/9477 completed. Show completion, scheduled success, and conditional acceptance together. Completed-only acceptance and valid-only compression select on model outcomes and can favor different response/task populations. Failed/unmeasurable attempts must not receive numerical zero T₀. No all-eight common mathematical-compression question intersection exists in either OM100 cohort; default smaller-cohort rankings are explicitly conditional, not a universal eight-model order. [E02–E05,E17]

The separate submitted-text length extension counts Unicode code points, non-whitespace code points, UTF-8 bytes and lines across accepted and unsuccessful completed outputs, retaining prose/comments. These are not Lean-parser W_B. Nonempty truncated partials are separately labeled and excluded by default; empty source is unavailable rather than length-zero proof. This extension and the completed-only default were added post hoc; original records stay unchanged. [E21]


## Parse failures measure format adherence as well as Lean syntax

The full saved diagnostic audit covers Haiku, Sonnet and Opus in the 128,000-token cohort. Haiku has 1,051 parse failures (970 reported at line 1), Sonnet 1,088 (983 at line 1), and Opus 314 (287 at line 1). Every one is a classified Lean parser result with engine exit code 0. The `parseOriginal` whole-term parser runs before the restricted-metaprogramming/admission checks and before elaboration; these are not network failures or a Python preflight labeling mathematical answers incorrect.

The frozen adapter only removes a single wrapper if it encloses the entire response. A Markdown heading or prose preamble followed by `<PROOF>…</PROOF>` or a fenced Lean block remains the full candidate and can fail parsing. Among Haiku parse failures, 629 start with Markdown headings and 914 contain a proof tag; 940 Sonnet failures contain fences. These flags overlap and must not be added as mutually exclusive categories.

A deterministic stratified visible-answer sample selected six first-line failures and four later-location failures per model using a fixed SHA256 ordering. All 10 Haiku and 10 Sonnet samples visibly violate the one-term interface. Some Sonnet outputs contain literal `<invoke name="lean4_check">` or `<invoke name="lean4">` XML, but no such tool was executed. Seven of 10 Opus samples contain prose/replacement-declaration format violations; the other three show unknown-tactic or other Lean syntax errors. This deliberately stratified sample is not a population estimate of semantic solvability. Later-line failures can still be prose errors, so line 1 counts are not a complete format classification.

Opus's source-concision condition has 31/400 parse failures and 284/400 accepted proofs, versus 146/400 parse failures and 201/400 accepted for standard. The 83 additional accepted outputs accompany 115 fewer parse failures; acceptance conditional on escaping the parse-failure category is 201/254=79.13% versus 284/369=76.96%. That secondary denominator also includes later verification failures and is selected, not a causal decomposition. The defensible conclusion is that the observed end-to-end prompt effect combines format compliance and formal proving. Do not describe Haiku/Sonnet's near 90% parse-failure rates as 90% mathematical incompetence, or claim that stripping prose would make their enclosed proofs valid: no regrading was done. The strict gate is legitimate because the prompt explicitly required a proof term, but its contribution must be visible. [E12,E14,E25]

## Costs, reproducibility and statistical claims

The initial OM100 recorded-response estimate is $1,848.43613853 plus $56.824433 estimated compute; provider-maximum is $4,369.71831081 plus $59.32855721094778 estimated compute. These are usage-times-frozen-rate estimates, not invoices. Higher-run OpenAI requests use priority pricing based on delivered tier; the requested common output cap includes reasoning and answer tokens. Original request charges for six uncertain Astra outcomes and one HTTP520 incident in the first run, and 30 originally failed requests in the second, are unresolved. Their recoveries do not establish zero original cost. Saved responses were not repeated or chosen against; authorized uncertain-outcome replacements and captures retain explicit provenance. Storage/network/tax/credit omissions remain unknown. [E02–E03,E22–E23]

Distinguish the historical reports from the new paper analysis. **Historical frozen report code** uses equal-question paired contrasts, 10,000 whole-family bootstrap replicates, seed 20261011, and domain stratification where applicable. Those retained intervals are not the newly computed paper intervals. [E24]

**New paper analysis:** `analysis-plan.md` is explicitly post hoc, written after collection. `main/analyze_primary.py` and the cross-run analysis use seed 20261014, 10,000 global resamples of the 19 source-result family blocks, and question-weighted ratio estimators; these family resamples are not domain-stratified. A separate bootstrap over 100 question clusters is a sensitivity analysis. Exploratory paired-contrast tests use CR1 family-cluster standard errors, Student t with represented G−1 degrees of freedom, suppress p-values for fewer than five represented families, and apply stated Holm multiplicity correction. Main prompt-effect code groups Holm correction by outcome across its model/contrast tests; use actual exported family definitions rather than describing an unspecified universal correction. [E26]

The new scientific contrasts include compression versus standard, concision versus standard, and compression versus concision within each model. Correctness retains all scheduled outcomes; compression comparisons state their selected shared-question support. The joint contest score and social-post benchmarks are supplemental, roster-dependent, post hoc summaries that mix acceptance and compression. Sixteen response-pair comparisons for two four-sample cells are correlated and must not be treated as sixteen independent observations. There are 19 source families, not 9,600 independent mathematical units; even family resampling does not establish that those families are independent draws from all mathematics. Show the log decomposition with identical samples and weights: separately subtracting medians does not preserve that identity.

Supported claims: formal acceptance under the stated environment/limits; observed source concision; declared dependency-ratio differences; descriptive shared-question rankings; frozen-price observed-response cost. Unsupported claims: optimal compression, human mathematical taste, new theorem discovery, model-internal reasoning compression, certified unseen tasks, equal inference compute, a causal token-budget effect, universal model superiority, or a full provider/cloud bill. Reproducibility is strong at the recorded-artifact level, with manifests, prompts, responses, proof graphs, reference checks, hashes and a retained immutable image archive. A source-only rebuild is not promised to produce the same image digest because some base/package selections were not immutable. [E08,E19–E24]

## Exact condition instructions

The full common system message, verbatim condition strings, frozen prompt-policy hash, per-run manifest/config/measurement/boundary hashes and the new analysis-specification hash are in `prompt-specification.json`. The three condition strings are:

- **standard:** Produce a correct Lean proof of the target.

- **mathematical_compression:** Produce a correct Lean proof in the most mathematically compressed form you can. Use suitable existing definitions and lemmas, and factor repeated reasoning locally, to express the argument compactly. Avoid irrelevant reasoning or artificial expansion.

- **source_concision:** Produce a correct Lean proof using as few Lean source tokens as possible.

