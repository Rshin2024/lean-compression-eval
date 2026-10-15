# Combined model comparison

From the repository root, run `python3 reporting/build_comparison.py` after the eight smoke reports are complete. Use `--suite PATH` for another completed eight-model suite. The selected OM100 cohort requires exactly OM100-001 through OM100-100 and four samples under every condition (9,600 total attempts). `--output PATH` writes an isolated QA copy without overwriting an existing page.

The generator checks complete, identical populations and reconciles scheduled attempts, validity, per-question measurement coverage and ranking values against the source analyses. Smoke denominators are 120 per model and 40 per condition; OM100 denominators are 1,200 and 400. It writes `index.html` and `comparison-data.json` inside the suite. Original reports, captured responses, evaluation records and frozen configurations are read-only inputs.

## Adjustable paper-metric ranking

The primary comparison uses the element-level definitions in [Compression is all you need, §5](https://arxiv.org/pdf/2603.20396#page=23), implemented with this evaluation's frozen source-token and dependency-reference counting rules. It offers T0 descending, I0 descending, and proof length W_B ascending. Higher I0 means a longer proof for a fixed statement; it is not proof concision.

The optional J0 blend is `beta * normalized_T0 + (1-beta) * normalized_I0`. For each shared question, the same usable samples supply the geometric means of both metrics; each mean is divided by the largest respective mean among the compared models. Normalized values are evaluated as `exp2(log_mean - log_max)`. This maximum-based normalization is an explicit exploratory implementation choice, not a choice prescribed by the paper. It is not PageRank I1. The paper's graph-walk alpha requires a separately specified graph population and computation and is unavailable here.

Controls select the metric, beta (0–1), prompt, model-coverage threshold, samples (all or a fixed sample number), minimum usable samples per question, proof-body token filter, and a question subset. Coverage is counted over the full source question set after sample and proof-length filters, before the question subset. A model/question qualifies only with enough successful, measurable outputs. All compared models use the intersection of qualifying selected questions. Cohorts with fewer than two models or no shared questions have no ranking. Fixed-sample selection resets the minimum to one and disables impossible larger sample requirements.

Order by average question rank (default) or aggregate metric value. The former averages midranks equally across questions. The latter geometrically averages per-question values equally for T0/I0/W_B, or arithmetically averages normalized J0. Exact rational products and integer powers decide T0/I0/W_B comparisons; display logs and numbers are approximate. J0 uses floating arithmetic with an absolute 1e-12 tie tolerance. These model-level aggregations and filters are descriptive, post hoc analysis choices. They change no frozen measurement, model call, pricing or verification result. Failure outcomes remain missing for compression, with all scheduled attempts retained in validity totals.

The chart displays explicit ordinal positions, mean ranks and underlying metric magnitudes. Its dot axis uses numeric ranks or log2 metric values (linear 0–1 for J0). An export records controls, source analysis hashes, exact ratio evidence, attempt IDs and per-question values. The previous T0 rank-points view and its diagnostics remain collapsed below as a reproducible baseline; their controls are independent of the primary paper-metric view.

Implementation: `paper_rankings.js` is a pure analysis module; `paper_panel.html`, `.css`, and `.js` define the controls and presentation. The builder embeds these local sources into the single-file report; no remote scripts or new served routes are required.

The self-contained page provides:

- All-model validity and all three prompt conditions, with counts explicitly labeled as proof acceptance.
- Primary per-question T0 rank aggregation: average each model’s within-question rank, with equal question weights and average ranks for ties. A heatmap shows every question’s rank or explicit exclusion, with underlying rounded T0 in tooltips and exact ranking method in exports and a rank-data CSV export.
- Output-to-output T0 and proof-body length magnitude charts remain available in an expandable view. No reference-proof value enters these comparisons.
- Actual T0, proof/statement length, expanded proof references, I0 and dependency depth, including measured-goal and attempt coverage.
- Original within-model prompt-effect ratios and 95% intervals.
- All model pairs on their shared measurable goals, as selectable descriptive T0 or proof-body length ratios with explicit goal counts (no newly claimed confidence intervals).
- Token usage, failure types, filtered per-attempt measurements and CSV exports.

Absolute T0 uses equal goal weighting: first mean log2 T0 over measurable attempts within each goal, then mean across included goals and exponentiate. Other descriptive medians use included measurable attempts. The common-goal option intersects eligible goals separately for each condition. Empty intersections remain missing. Pairwise cells use their own two-model intersections, which can differ from cell to cell. Each cell exponentiates the equal-goal mean of row-model minus column-model log metrics; T0 above 1 indicates more expanded references per source token, while proof-length ratios below 1 indicate shorter proof bodies. Missing measurements stay missing; validity is reported separately across all scheduled attempts.

For the original ten-question smoke cohort, the question-rank charts retain the five-question minimum coverage setting. For OM100, the presentation opens with an explicitly labeled 25-question minimum-coverage view: five of the eight selected models and 22 common questions under the compression prompt. This post hoc display choice does not change the evaluation population or score calculation. Named exclusions are shown above the chart. One-click presets also expose the 50-question coverage view, all measured models, and all selected models with no exclusion. An empty intersection produces no aggregate ranking: the page shows coverage and retains pairwise comparisons. The optional coverage selector and model checkboxes recompute the cohort, common questions and ranks immediately. This is an explicit display-only coverage setting, not an evaluation exclusion rule. Models below that setting remain named and retain their places in the pairwise comparisons and proof-validity results. Ranked T0 and proof-body length both use equal-goal geometric means (first averaging sample logs within each goal); the detailed measurement table continues to show proof-length medians. Changing the prompt, coverage setting or selected models may change the compared goal population.

Question ranks use the same fixed cohort on the same shared questions. Rank each model’s geometric mean T0 within each question, descending. Order and ties use products of exact rational numerators/denominators with integer cross-powers, accounting for different measurable sample counts; no floating root is taken. Exact ties share the average occupied ranks. Only a legacy question missing exact ratios for any included model uses a labeled mean-log2 fallback with absolute tolerance 1e-10; every measurable OM100 attempt must supply its exact ratio. Average the ranks arithmetically across shared questions, with lower average rank better. The sorted bars show rank points, `100 × (model_count − average_rank) / (model_count − 1)`: a linear rescaling with larger values better. It preserves ordering and ties, and shows the original average rank beside each bar. These points are neither percentage compression nor success rates, and are comparable only within the current cohort. The CSV retains raw ranks and adds per-question and mean rank points. Ranking reduces magnitude dominance but treats tiny and huge differences equally. Ranks are conditional on measurable valid outputs; failures stay in the separately reported validity denominator and receive no fabricated compression rank. Empty intersections and cohorts of fewer than two models have no rank comparison. The heatmap retains all questions (columns for the ten-question smoke suite, vertically scrollable rows for OM100), with nonshared questions explicitly excluded from every average. Changing models, coverage or prompt changes the comparison population. No significance claim or confidence interval is attached to these exploratory rank summaries.

Reference attainment is retained in a collapsed optional diagnostic and is absent from the headline cards. It does not determine any direct model comparison. Target attainment uses all scheduled attempts per model and condition (40 smoke / 400 OM100). A counted attempt must be valid, measurable and reach the chosen multiple of its goal’s reference T0. The bar separates qualifying proofs, other valid proofs and failed responses. It combines reliability and compression; it is not a pure compression-magnitude ranking.

CSV exports of individual attempts preserve exact large-integer U values and rational T0/I0 numerators and denominators. Displayed values and the comparison-table CSV are rounded/finite-number summaries. No raw proof text, private reference proof or API credential is embedded.

Serve the suite over loopback HTTP for browser preview; the page needs no network requests for its charts or filters. Model-report and source-analysis links remain local to the suite.

The cost view sums exact decimal USD strings from per-attempt cost reports and reconciles them to the model subtotal before rendering. Model, prompt and question filters apply to both the view and its CSV. Failures and truncated responses remain billable observations; unpriced generations stay unavailable, and the all-slots recorded-response estimate is shown only if every scheduled slot in the view has a priced response. This does not establish complete request billing: the all-request and provider-invoice totals always remain unavailable. Pricing hashes, dates and source evidence are linked. Token usage fields also expose observation coverage rather than treating missing counts as zero. Optional `--cloud-costs PATH` adds a separate shared compute ledger; disk/IP/network and invoice totals remain unknown unless independently recorded. Shared cloud costs are not allocated to individual models.

If all eight matching manifests are available under `--runs-root PATH` (default `runs`), the builder verifies run and prompt hashes and exports `questions.html` plus `questions-data.json`. They contain only the exact frozen `slots.messages`, collapsed across identical models/samples into question-by-condition combinations. Prompt differences across models/samples cause a failure rather than being hidden. The viewer does not read private curator catalogues, reference proofs, captured responses or credentials. It records the run hashes and prompt hashes for inspection.

## Amended OM100 verification

The OM100 final comparison uses `v2_threads1`: the original generation runs and
responses are unchanged, while every scheduled outcome and all 100 reference
proofs are checked under the separately frozen one-thread Lean runtime amendment.
Earlier v1 records remain historical evidence and are excluded from this final
comparison. The report builder rejects legacy OM100 reports, mixed verification
versions, mixed amendments/environments and phase records with different policy
bindings. The original ten-question smoke report remains supported unchanged.

Use the external phase-aware adapter after a model has all 1,200 explicit v2
terminal records, including records for truncated/failed generations:

```sh
python3 reporting/report_verification_v2.py \
  --run runs/om100-gpt-6.1-sol-high \
  --amendment ../../work/full-eval-100/runtime-amendment-v2.json \
  --output runs/om100-suite-high-20261013/report-gpt-6.1-sol
```

Repeat for the other seven models. The validated external reader checks the
original generation bytes and separate verification policy before statistics
are computed. The adapter creates temporary analysis projections in memory;
`all_scheduled_attempts.json` retains the actual two-phase schema with unchanged
`generation_identity` and nested `verification_result`. A terminal-looking
generation failure with no stored v2 record still blocks final reporting.
No source slot, generation, run manifest or prior result is rewritten.

`verification-phase.json`, `verification-policy.json` and the HTML describe the
original generation environment separately from the amended verification
environment. The question viewer remains tied to the exact original frozen
messages. API costs are calculated once from the original generation records;
the verification replay adds no model calls. `phase-audit-index.json` identifies
the selected proof-audit records and hashes of their full graph-bearing files;
the legacy single-phase graph-restoration exporter is deliberately not used.

The adapter requires the bound private reference index and runtime amendment
artifacts. Git source archives can record their hashes without including the
reference proofs or full verification records. Keep those operational artifacts
and complete source records for independent replay and audit.

Full-suite comparison after all eight phase-aware reports finish:

```sh
python3 reporting/build_comparison.py \
  --suite runs/om100-suite-high-20261013 \
  --runs-root runs \
  --cloud-costs ../../work/full-eval-100/runtime/azure/cloud-costs.json \
  --transport-cost-disclosure ../../work/full-eval-100/reporting-proposals/transport-cost-disclosure-20261014.json
```

Offline contract checks: `python3 -m unittest discover -s reporting -p 'test*.py'`. Synthetic QA fixtures must be built with `--classification synthetic_test_fixture`; this prints a prominent banner and suppresses the actual-input viewer. Such fixtures are development evidence, never evaluation results. Tests cover complete eight-model nested-record export, numeric precision, immutable generation identity, mixed-phase rejection, and incomplete truncated-response placeholders.

## Unknown transport charges

The optional `--transport-cost-disclosure PATH` supplies an operational incident
snapshot separately from the frozen model reports. The comparison labels token
estimates as costs of **recorded responses**. It shows listed disconnected requests
and HTTP errors with unavailable usage separately, filtered by model, question and
prompt condition. A later priced response for the same scheduled slot does not
resolve the earlier request's unknown charge. No retry is authorized by this input.
Omitting the file shows that incident-specific disclosure is absent; it never
implies zero extra charges or complete billing. Per-model `transport_coverage`
counts are retained when present, with a warning that historical unknown-outcome
events can include captured responses later recovered and priced.

The JSON schema is version 1, kind `unknown_transport_cost_disclosure`. Its
`as_of_utc` timestamp must have a timezone, `scope` must be
`known_incidents_not_exhaustive`, `invoice_reconciled` must be false, and
`all_request_total_usd` must be null. Each item in `incidents` identifies a model,
`attempt_id`, `task_id`, condition, and **one-based display `sample`**; convert from
the frozen slot with `sample_index - sample_index_base + 1`. It also specifies
`kind` (`disconnected_response` or `http_error`), nullable `http_status`,
`recorded_at_utc`, `usage_available: false`, and `unknown_charge_usd: null`.
Provenance fields are `source_run_hash`, `source_manifest_sha256`, and
`transport_events_sha256`. The `evidence` list contains only `kind` and SHA-256
pairs for a `disconnect_diagnostic` or `transport_log_snapshot`.

The builder validates incident identities against compared attempts, original run
hashes, and, for phase-aware reports, original manifest hashes. It checks count,
timestamp, and unknown-billing semantics, rejects duplicate incidents, and exports
only allowlisted fields. The exact supplied disclosure file's SHA-256 is retained
in comparison-data.json and displayed on the page. This is a hash-bound supplied
incident snapshot, not an independent reread of provider transport logs and not a
provider invoice; the producer's source-evidence audit remains necessary. It is
not an exhaustive billing ledger. Keep raw request/response bodies, credentials,
and private diagnostic payloads out of the disclosure and Git. The pinned phase
adapter, per-model costs, and completed report bundles are never rewritten.

## Local report viewer

After the complete comparison is built, serve its interactive pages locally:

```sh
python3 reporting/serve_comparison.py --suite runs/om100-suite-high-20261013
```

The default address is `http://127.0.0.1:62772/`. The server binds only to
loopback and requires a matching local Host header. Its explicit route list
contains the comparison, frozen-prompt viewer, per-model summary pages, metric
and cost exports, and phase-provenance indices. Raw `all_scheduled_attempts.json`
files, run directories, credentials and directory listings are not served.
Missing or linked artifacts prevent startup. The viewer makes no provider or
Lean calls and does not publish files to an external service.


## Compression and validity associations

The `Compression vs correctness` section reuses the primary paper ranking's model cohort, shared questions, prompt, sample choices and measurement filters. All four metrics are shown in a correlation overview on the same support. Its scatter plot can show one point per model or one point per model/question. The x-axis can use the existing preferred-order rank points or log2 metric magnitude (linear normalized values for J0). Magnitude W_B increases with proof length; rank-points W_B increases with concision. Pearson uses the displayed x transformation. Spearman uses exact ratio ordering and midrank ties for T0/I0/W_B, exact rank-sum ordering for model rank points, and the disclosed 1e-12 descending-anchor tie rule for J0 magnitude.

The default y-coordinate is `accepted / completed nonempty submissions` on those shared questions. Truncated and blank outputs are excluded; completed parse/elaboration failures and checker timeouts remain unaccepted in the denominator. The selectable original scheduled view uses `accepted / scheduled` and retains incomplete attempts. A length filter restricts x and eligibility only: it does not drop a completed failure or long valid proof from the applicable denominator. Unresolved statuses, missing schedule rows and duplicate slots fail closed. An independent pure module, `compression_validity.js`, consumes the frozen DTO and the exact paper-ranking result without modifying either.

A direct attempt-level compression/correctness correlation cannot be estimated: compression is observed only for accepted measurable proofs, whose validity is uniformly true. These new coefficients describe selected groups with at least one measurable success, not an unselected proof population or held-out predictions. Model/question observations repeat models and questions. No ordinary independence-based p-values or confidence intervals are reported. At least three plotted observations and variation in both axes are required; otherwise the coefficient is unavailable rather than zero. Fixed-sample selections and minimum-four-sample eligibility can force all observed validity fractions to one.

The question-level view also shows within-question Pearson r, computed after subtracting each question's across-model mean from x and y. It removes between-question mean differences only; it does not remove model effects, selection bias or within-question scale differences. The Spearman column always describes the original plotted coordinates, not these residuals.

Original scheduled-denominator view, compression prompt, gate25, min1, all samples: five models,22shared questions,88scheduled attempts each. Valid counts: GPT6.1Sol83, GPT6Sol64, Astra79, Opus48, Fable56. T0 rank-points correlation is Pearson -0.345561995235 and Spearman -0.7; using log2 T0 magnitude gives Pearson -0.558275861770 and Spearman -0.7. The question-level within-question-centered log2 T0 Pearson is -0.098767029948. These are descriptive post hoc associations, not evidence that compression causes lower correctness.

The JSON export records source analysis hashes, the active ranking/correlation controls, all four plotted point sets and coefficients, selected scheduled attempt IDs, metric-supplying attempt IDs, denominators and interpretation metadata. Inspectable plot labels and a keyboard-accessible details area expose the same values as the export. Source measurements, the 9,600 outcomes, API costs and frozen questions remain unchanged.


## Source length across accepted and unsuccessful outputs

`source_lengths.py` derives text lengths for every saved OM100 response without rerunning Lean or calling a model. It verifies the frozen manifest/configuration, slot identities, generation and response hashes, file bindings, adapter settings, and saved submitted-text hash. Completed generations use their saved `verification_result.adapted_response`; this must match the original frozen `single_outer_wrapper_v2` adapter applied to the saved response. Truncated text is adapted mechanically only for measurement and labeled `partial_saved_text`, never a new verification. No prose, comments or invalid syntax is stripped beyond the original adapter.

New per-attempt `sourceLength` fields contain Unicode character counts (Python code points, not UTF-16 units), non-whitespace character counts (`str.isspace()` with the Unicode version recorded), UTF-8 byte counts and splitlines counts. Empty or whitespace-only text has null source-length fields; raw returned-text counts can be zero. Only counts, classifications and hashes enter the comparison DTO. Original proof text remains private. These are post hoc text-length measurements, not the frozen Lean parser-leaf W_B or paper I0/T0 metrics. The original reports, outcomes, measured metrics, prices and questions are unchanged.

`source_length_analysis.js` analyzes every available selected output, independently of the success-qualified model-ranking cohort. The default selects all models, questions and prompts; excludes truncated text; retains all completed failure types; and correlates log2(non-whitespace characters) with Lean acceptance. Controls change prompt, question, unit, truncation handling, parse/format exclusions and linear/log2 scale. Missing text is excluded with explicit counts, never imputed as a zero-length proof. Coverage exclusions are disjoint in this order: missing text, truncation policy, format policy. Independent total-truncation counters include empty responses and are labeled separately.

The acceptance chart uses fixed doubling intervals [lower, upper), starting [1,64); the global dataset maximum for the selected unit fixes the extent. Empty outer bins are omitted visually but retained in exported bin records; empty bins have no rate. Outcome distributions report raw-length medians and linearly interpolated quartiles at (n-1)*p. Pearson against binary acceptance is point-biserial; Spearman uses exact integer-length ties. The within-group sensitivity subtracts each model × question × prompt group's means, discarding singleton groups. Constant-outcome groups remain in the residual calculation, contribute no outcome covariance, and are counted separately. No significance, independent-attempt or causal claim is attached.

Default observed coverage:9,600scheduled attempts,5,184nonempty adapted texts,4,416empty texts. Of the nonempty texts,551are truncated partials and4,633are completed. Completed output counts are2,445accepted and2,188not accepted. The default median non-whitespace source length is1,107characters for accepted outputs and1,500for unsuccessful outputs. Pearson(log2length, acceptance)=-0.183771734; Spearman=-0.251409798. After centering within model/question/prompt groups, Pearson=-0.139395511 across4,478observations in1,223groups (442mixed-outcome groups). Excluding977parse/format failures weakens the association further; these may contain prose or malformed source.

The JSON export includes active controls, analyzed points, denominators, all source length measurements for included attempts, policies and input hashes, but no raw responses. The whole `comparison-data.json` retains a length record for all9,600slots, including empty and excluded partial outputs. Supported current provider statuses are completed and truncated; a new refusal/malformed/unknown status fails closed until its measurement semantics are specified.


## Completed submissions versus scheduled attempts

The primary acceptance charts now default to completed, nonempty submissions. `CompressionValidity.summarizeOutcomes` classifies a completed submission only when the hash-bound source-length record has `responseStatus=completed`, `available=true`, `sourceKind=submitted_text`, and `truncated=false`. Missing provenance makes conditional acceptance unavailable; the code never silently substitutes the scheduled denominator. Zero completed submissions produce an unavailable acceptance rate, not 0%.

Three rates remain visible: accepted/completed, completed/scheduled, and accepted/scheduled. Their exact decomposition is `scheduled solve = completion × completed acceptance` whenever the denominators exist. Across this run those are 2,445/4,633 (52.77%),4,633/9,600 (48.26%), and2,445/9,600 (25.47%). All 4,967 exclusions are truncated outputs, including 4,416empty texts. The 38 completed checker timeouts remain unaccepted under the frozen checker budget; they are not mathematical disproofs.

The correlation control explicitly selects `denominator=completed` by default; `scheduled` reproduces the original operational view. The library API retains its scheduled default for historical callers. Every plotted point and export includes both denominators, completion and scheduled-solve rates, excluded-incomplete counts and completed-attempt identities. Spearman orders acceptance fractions by their selected denominators with exact integer cross-products. The x-values and shared-question cohorts are unchanged. Model-level y pools completed submissions, so questions with more completed outputs have more weight in y, while x retains equal question weights. Question-level comparison remains available separately.

For the default five-model/22-question T0 comparison, completed counts are 88,85,88,87,63 for GPT6.1Sol,GPT6Sol,Astra,Opus,Fable respectively. Accepted counts are 83,64,79,48,56. T0 rank-points Pearson is now -0.120670300223 and Spearman-0.4; log2 T0 Pearson is -0.476379039931. The scheduled option retains the earlier -0.345561995235 /-0.7 rank-point correlation. These small-cohort post hoc associations remain conditional on completion and measurable success, not causal or reliable-winner evidence.

The completed-submission view changes presentation denominators only. Original source reports, all-attempt success counts, API usage and costs, frozen responses, Lean outcomes, and compression measurements remain intact. Source-length analysis already excluded truncation by default and is unchanged. Completion is shown explicitly because conditioning on finishing can favor models that complete fewer or easier cases.
