# Combined model comparison

From the repository root, run `python3 reporting/build_comparison.py` after the eight smoke reports are complete. Use `--suite PATH` for another completed eight-model suite. The selected OM100 cohort requires exactly OM100-001 through OM100-100 and four samples under every condition (9,600 total attempts). `--output PATH` writes an isolated QA copy without overwriting an existing page.

The generator checks complete, identical populations and reconciles scheduled attempts, validity, per-question measurement coverage and ranking values against the source analyses. Smoke denominators are 120 per model and 40 per condition; OM100 denominators are 1,200 and 400. It writes `index.html` and `comparison-data.json` inside the suite. Original reports, captured responses, evaluation records and frozen configurations are read-only inputs.

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
