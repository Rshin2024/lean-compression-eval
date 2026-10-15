# Independent statistical and data review

**Share with caveats.** The stored numerical data pass the checks below. A universal pure-compression ranking and claims of reasoning improvement would exceed the evidence. This is a source/calculation audit, not a new proof check or a rendered review of the forthcoming paper. Existing results, scores and sources were not edited.

## Evidence and scope

The primary source is `outputs/lean-compression-eval/runs/om100-max-suite-high-20261014/comparison-data.json`, SHA-256 `4ddf80101317bb6158bd13a435eccae899684479dfd74b91c2047c81c550f264`. `data-audit.json` records hashes of the prepared question index, original evaluation plan, metric/analysis/reporting sources and all eight source analyses. The source-analysis hashes match the combined dataset. The index has 100 questions in 19 source families, with 25 questions in each of four domains; domains have 5, 5, 5 and 4 families. Family sizes range from 2 to 7 questions. These are curated public-library tasks, not a random sample of mathematics or demonstrated unseen training data.

Reproduce with Python's standard library, from the workspace root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 work/paper-analysis-20261014/review/check_data.py
PYTHONDONTWRITEBYTECODE=1 python3 work/paper-analysis-20261014/review/check_contest.py
PYTHONDONTWRITEBYTECODE=1 python3 work/paper-analysis-20261014/review/check_extremes.py
```

These scripts read saved measurements and submitted-text hashes only. They do not access raw response bodies, private reasoning, graph payloads, providers, Lean or cloud resources. They verify stored arithmetic; they do not independently reconstruct the extractor's graph counts, tokenize submitted proofs or establish absence of benchmark contamination.

## Data-quality findings

All 9,600 schedule identities and attempt IDs are unique. Every model/question/condition cell has exactly four samples (the comparison export numbers them 1–4). There are 3,831 accepted proofs, all with available metrics; no accepted proof is silently lost to metric extraction. The remaining statuses are 2,865 elaboration failures, 2,668 parse failures, 96 verification timeouts, 16 disallowed-axiom outcomes, one verification-resource-limit outcome and 123 provider truncations. Preserve all of them in scheduled denominators.

There are 9,477 completed, nonempty submitted texts. Of 123 truncated outputs, 99 have no non-whitespace saved text and 24 have some saved text. Empty and truncated counts therefore overlap: they are not additive failure populations. Accepted/scheduled is 3,831/9,600 = 39.90625%; accepted/completed is 3,831/9,477 ≈ 40.42418%. The first is the primary reliability denominator. Conditioning on completion removes provider-budget failures and changes the question.

Exact integer checks pass for all 3,831 accepted rows: T0 numerator equals U_S+U_B, denominator equals W_S+W_B, and I0 equals W_B/W_S. All 2,400 per-task/model/condition summaries match independent validity counts, means of logs and exact rational geometric products. Maximum independently recomputed log2 discrepancy is 1.14e-13. W_S and U_S are constant within every question across all saved successful proofs. Costs are estimated for all 9,600 recorded responses and sum exactly to $4,369.71831081; this excludes 30 unknown original-request charges and is not a full invoice.

One exact submitted-source hash is shared by different models among successful proofs: three standard-condition records for OM100-068 across GPT-6.1 Sol and GPT-6 Astra. Their metrics agree exactly. No same-question/source-hash group mixes success and failure. Hash equality demonstrates identical submitted bytes, not a shared reasoning process, plagiarism or training leakage. Different strings can still elaborate to equivalent proofs; this check does not deduplicate semantics.

## Benchmark recommendations

**Benchmark 1 — verified proof yield.** Use the standard prompt on the fixed 100 questions. Average the four success indicators within each question, then average questions equally. Show the scheduled single-attempt rate and four-sample question coverage side by side; all eight models remain included. This is understandable without a compression proxy and preserves truncations and all checker failures. Repeat the same table for other conditions in the paper.

| Model | Accepted / 400 standard attempts | Scheduled pass@1 estimate | Questions solved in four samples |
|---|---:|---:|---:|
| GPT-6.1 Sol | 277 | 69.25% | 84/100 |
| GPT-6 Sol | 162 | 40.50% | 67/100 |
| GPT-6 Luna | 46 | 11.50% | 30/100 |
| GPT-6 Astra | 301 | 75.25% | 89/100 |
| Claude Haiku 5.5 | 20 | 5.00% | 19/100 |
| Claude Sonnet 5.5 | 20 | 5.00% | 16/100 |
| Claude Opus 5.5 | 201 | 50.25% | 81/100 |
| Claude Fable 5.1 | 308 | 77.00% | 96/100 |

Here pass@1 estimates a randomly selected scheduled sample by using all four samples. It differs from the separately reported first-index success rate. For k≤4, the usual per-question finite-sample estimator is 1−choose(4−c,k)/choose(4,k), where c is the success count. At k=4 this is observed four-sample coverage; do not extrapolate to larger k or call it single-shot accuracy. Repeated-sampling interpretation depends on the sampling assumptions.

**Benchmark 2 — compression-instruction response.** The scientific primary analysis should follow the original plan: within each model, compare mathematical compression B with standard A and source concision C. Average log2 T0 over successful measurable samples within each question/arm, difference matched questions, and average questions equally. Report 2 to that mean difference as a geometric ratio, with the same-question W_B ratio, paired support and Δlog2 U−Δlog2 W decomposition. This answers whether the instruction changes measured representation beyond ordinary brevity; it does not rank all models on an unconditional notion of mathematical compression. Haiku's paired sets contain only 4 and 6 questions; Sonnet's contain 4 and 5, spanning only 3–6 families. Those estimates deserve explicit sparse-support labels.

**Optional shareable Benchmark 2 alternative — a post-hoc verified-proof preference contest.** If a full-eight comparison is essential, retain the parent's proposed fixed-roster contest as a separate, explicitly post-hoc descriptive score. For each question and pair of models compare all 4×4 outputs: valid beats invalid; among valid outputs higher exact T0 wins; equal T0 or two invalid outputs earn half a point. Average all 100 questions and seven opponents equally. Do not label it pure compression, a paper-defined metric, or a prespecified primary endpoint. The original evaluation plan explicitly recommended a profile rather than a combined quality score; this new preference rule must be disclosed as an added analysis.

For a question/opponent pair its exact decomposition is:

`score = 0.5 + 0.5(p_A − p_B) + p_A p_B (w_valid − 0.5)`.

The middle term is reliability advantage; the last is T0 preference among both-valid pairs. Keep this breakdown and both-valid/both-failed comparison masses visible. The eight scores average exactly 50 by construction. A universally failed question gives everyone 50, so 50 means neutral against this roster, not adequate proof quality. OM100-093 and OM100-096 are universally failed under the compression prompt. Removing or adding a model changes the estimand; retain all eight in the headline. `contest-audit.json` independently verifies all 44,800 undirected response-pair comparisons and exact point totals.

## Coverage and selection

There are only 1, 0 and 3 questions measurable for all eight models in standard, compression and concision respectively. Thus the all-eight conditional compression ranking is undefined. A 25-question model-coverage gate yields six models/23 shared questions for compression, but five models/50 shared questions for concision. These condition-specific populations cannot be compared as though identical. Raising the compression gate to 50 leaves five models/50 questions; raising it to 75 leaves four/62. Every such cohort is selected using observed success and should remain a sensitivity analysis, with excluded models and question IDs displayed.

Conditional comparisons select successful outputs in each arm even on the same question. Equal-question weighting prevents easy questions with four successes from receiving four times the weight of questions with one; it does not remove survivorship or outcome-selection bias. Report compression-only solves, control-only solves, shared failures and the common A/B/C intersection. Never give failed proofs a fabricated numerical T0 of zero. The contest's failure preference is a declared decision rule, not an imputed metric. No model should be removed after inspecting which cohort produces an appealing winner.

## Metric interpretation and observed sensitivity

T0=(U_S+U_B)/(W_S+W_B), with U counting weighted expanded primitive references and W counting Lean source-token spans. It is not compressed bytes, an AST size, a normalized proof-term size, bits of mathematical information or internal reasoning efficiency. Billing output tokens and Unicode/source-character lengths are separate units. Character lengths can describe failed submitted text, but cannot establish the length of a valid proof; parse failures may include prose.

No successful proof has zero U_B or W_B≤5; minimum W_B is 41. Every question has at least one successful proof somewhere in the eight-model/three-condition cohort. No question with multiple successful samples has universally constant T0 or total U. Thus the completed suite is not simply a collection of identical one-tactic measurements. This does not establish that every question discriminates usefully.

The fixed statement's expansion fraction U_S/(U_S+U_B) has median 1.10e-7 and maximum 0.25. Its source fraction W_S/(W_S+W_B) has median 0.238 and maximum 0.803. The statement does not dominate expansion here, but its denominator contribution can substantially attenuate differences in proof-source length. Compare W_B directly when claiming shorter proofs.

Expanded proof cost is not invariant across proofs of the same target. Across saved valid proofs, OM100-065 spans 52.74 log2 T0 units, OM100-100 spans 25.81, OM100-066 spans 15.03 and OM100-061 spans 14.16. A more controlled example in `within_cell_extremes.json` holds model, question and condition fixed: GPT-6 Sol, OM100-065, standard prompt spans 52.56 log2 T0 units across samples, with a 52.29-bit change in total expansion and a smaller W_B ratio of 0.822. This is an observed accounting difference, not evidence that a particular rewrite, factoring choice or deliberate irrelevant padding caused it. The saved proof bodies and graphs need separate inspection before any mechanism claim.

Among 7,123 both-valid cross-model sample pairs under compression, 6,320 prefer the same output by higher T0 and shorter W_B, 759 disagree, seven tie on both, and 37 tie only on W_B. This quantifies a real distinction between source concision and the expansion ratio. These are dependent descriptive pairs, not 7,123 independent trials. Keep outliers in primary results; use leave-one-question/family-out sensitivity and investigate mechanisms without deleting inconvenient observations.

## Inference and multiplicity

Use theorem-family cluster resampling, retaining every model, condition and sample within each family draw. Stratify by the four domains as frozen, use the same draw across models/contrasts, and recompute ratios, intersections and support in each replicate. Do not bootstrap samples or the 16 contest pairs as independent observations. Point estimates remain equal-question weighted, not equal-family weighted. With only 19 families, including 4–5 per domain, interval precision is limited; report undefined replicates and sparse family support. Intervals describe resampling sensitivity for this curated case set, not guaranteed generalization to unseen mathematics.

The frozen analysis pools task weights after whole-family resampling, so unequal family sizes also vary resampled domain weights. Retain that method for historical replication. A new fixed-domain-weight sensitivity may average the four domain-specific means with weight 1/4, but it is a separately declared estimand and must not silently replace the frozen bootstrap.

Prefer effect sizes and intervals. If making confirmatory significance claims, declare the comparison family before interpreting results: eight models × two planned prompt contrasts already gives 16 tests for one endpoint. Correctness, W_B, subgroup and between-model tests add further multiplicity; unadjusted 95% intervals are not simultaneous evidence for a selected winner. Use a stated adjustment such as Holm for a declared family of p-values or simultaneous bootstrap intervals, and label post-hoc/exploratory comparisons. Do not derive significance from overlap/non-overlap of separate marginal intervals. Near-zero effects with broad intervals are inconclusive, not equivalence; a noninferiority margin cannot be selected after seeing the data.

The provider-maximum run also differs operationally from the earlier 8,192-token run (streaming, requested priority handling and host allocation). Budget comparisons should be paired by question/condition and labeled observational; they do not isolate output ceiling causally. Mixed AMD and Intel hardware makes checker wall time an operational outcome rather than a controlled model-speed benchmark. Model training exposure is unknown; runtime target exclusion does not demonstrate unseen questions.

## Independent review of the new primary analysis

`check_primary.py` verifies the parent's `main/analyze_primary.py` outputs without importing that script. It checks all 152 rate means; 144 prompt-effect means and their question counts; 6,396 exact per-question ranks; the eight compression-prompt T0 contest scores and both decomposition terms; 12 source-length slopes; and all four split-sample coefficients and confidence intervals. The split regressions are rebuilt from an explicit dummy matrix, with a manually computed CR1 cluster sandwich and t interval. Family-bootstrap intervals and empty-replicate counts are independently reproduced for standard-prompt yield and all yield/T0 prompt effects, using family-level sums/counts instead of the parent's task-weight matrix. `primary-results-review.json` binds the precise source/result versions and reports the checks. Question-bootstrap intervals and other bootstrap endpoints are source-reviewed, not independently replayed.

The new analysis specification explicitly uses an unstratified 19-family bootstrap, unlike the original domain-stratified reports. Its intervals must be labeled as a new post-hoc uncertainty sensitivity; discrepancies from historical intervals are not a change to frozen measurements. The paper's original-plan B/A and B/C estimates remain distinct from the additional C/A contrast and supplemental contest.

Three review findings have been resolved or labeled in the parent's current source: trivial zero-null p-values were removed from rates/ranks/contest levels, Holm keeps the full planned comparison family when some p-values are uncomputable, and I0's higher direction is described as longer proof-to-statement source ratio. Only explicitly defined paired contrasts test zero. A contest-level test against zero would not test neutrality against the roster; do not reuse the superseded p-values.

The source-length result is sensitive to outcome filtering. Across completed nonempty submissions, the pooled within-cell slope is −0.028715 probability units per doubling of nonwhitespace source characters. Removing parse failures reverses it to +0.005543. This is a difference between selected populations, not proof that parse failure causally mediates an effect or that shortening mathematical proofs improves correctness. The main negative association should always be accompanied by this reversal. The 12 slopes are pooled within-cell covariance/variance projections, not equal-question averages; cells with more within-cell length variation contribute more leverage.

Split-sample regressions use only model/question/condition cells with an early successful proof, then predict the later two scheduled outcomes. Explicit question and model×condition controls do not remove that selection, give every question equal weight, or establish causality. T0's forward slope is about +0.00762 probability units per bit (95% family-cluster interval −0.01125 to +0.02650); the reverse slope is −0.00225 (−0.01212 to +0.00762). These are inconclusive associations in a selected population. Their intervals do not establish equivalence or absence of a relationship.

To reproduce the primary-output verification, run `work/paper-analysis-20261014/.venv/bin/python work/paper-analysis-20261014/review/check_primary.py` with NumPy and SciPy available. It does not import or execute the primary analysis. At fixed question, the I0 prompt contrast is algebraically identical to the W_B contrast because W_S is constant; they are not independent corroborating endpoints.
