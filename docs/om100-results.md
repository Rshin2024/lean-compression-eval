# OpenAI Math 100: completed results

Completed October 14, 2026. All **9,600 frozen attempts** across the eight models were generated, checked under `v2_threads1`, and included in the reports. There are **2,445 valid, measurable proofs** and **4,967 truncated outputs**. All 100 reference proofs also passed the same amended verification runtime. No questions or recorded responses were dropped.

This is the user-selected 100-question exploratory cohort, not the separate preregistered 250-task study. The compact [machine-readable summary](om100-results.json) is committed here; full response, proof-graph and report artifacts remain in the local run directory.

## Results and costs

Each model has 1,200 attempts: 100 questions × three prompt conditions × four samples. Each condition has 400 attempts. Valid-proof counts are correctness measurements, not compression scores. Every valid proof in this run has an available compression measurement.

| Frozen model | Valid / 1,200 | Standard / 400 | Compression / 400 | Concision / 400 | Truncated | Recorded-response API estimate (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-6.1-sol` | 699 | 253 | 227 | 219 | 206 | 91.400972 |
| `gpt-6-sol` | 347 | 112 | 122 | 113 | 473 | 112.014403 |
| `gpt-6-luna` | 62 | 22 | 21 | 19 | 972 | 6.569003 |
| `gpt-6-astra` | 729 | 267 | 236 | 226 | 219 | 468.925492 |
| `claude-haiku-5-5` | 1 | 0 | 0 | 1 | 1166 | 7.974050 |
| `claude-sonnet-5-5` | 54 | 18 | 14 | 22 | 485 | 131.857966 |
| `claude-opus-5-5` | 331 | 108 | 84 | 139 | 550 | 273.224772 |
| `claude-fable-5-1` | 222 | 83 | 82 | 57 | 896 | 756.469480 |

The exact reported-token estimate is **USD 1848.43613853**, using the frozen October 13 rate schedules and including failed and truncated responses. The separate closed Azure compute estimate is **USD 56.824433**. It uses allocated wall-time proxies, including idle time. The task VM was deallocated and its resource group was confirmed deleted.

These are not invoices. Charges for six disconnected original Astra requests and one HTTP 520 incident are explicitly unknown and excluded; a saved replacement response does not resolve its original request’s charge. The incident list is not an exhaustive billing ledger. Cloud disk, public-IP, transfer, allocation-transition and billing adjustments remain unavailable where no billing evidence exists. Unknown charges are never treated as zero.

## Reading the compression comparison

The primary comparison is between model outputs. For each question and condition, it ranks each selected model’s geometric-mean T₀ across valid measurable attempts using exact rational arithmetic, gives ties their average occupied rank, then averages ranks equally across shared questions. Failed or missing measurements receive no invented compression value or rank.

**No question is measurable for all eight models in any of the three conditions.** Therefore an all-eight aggregate compression ranking is unavailable. Under the compression prompt, explicitly excluding models with zero question coverage leaves seven models with only three shared questions. Pairwise and other selected-cohort comparisons remain available, with their actual shared-question counts shown. Different pairs can use different questions and do not form one global ranking.

T₀ = (U_S + U_B) / (W_S + W_B): expanded dependency-reference counts per statement-and-proof source token under the fixed library boundary. W_B is Lean proof-source length, not API token usage; I₀ = W_B / W_S. Higher T₀ need not mean a shorter, more factored or better-reasoned proof. A retained audit example has a longer valid proof with a higher T₀ because repeated helper references expand again. Read ranks alongside length, validity and coverage. Reference-proof attainment is an optional diagnostic, not the primary model comparison.

### Clearer ranking view

The presentation now opens with an explicitly labeled five-model comparison on 22 shared questions under the compression prompt. The default requires measurements on at least 25 questions per model; Luna, Sonnet and Haiku are named as outside this rank view and remain in the other results. Presets also show three models on 47 questions, seven models on three questions, or all eight models with no aggregate ranking.

Bars show **rank points = 100 × (model count − average rank) / (model count − 1)**, alongside the original mean rank. Higher points mean better relative placement within the displayed group. This linear rescaling preserves the exact original ordering and ties; it is not percentage compression, and scores from different groups are not comparable. No proof outcome, T₀ measurement, question, prompt, model call or cost was changed by this presentation update.

## Protocol and limitations

- The exact 100 supplied candidates were preserved, with three prompt conditions and four independent samples per model/condition/question. The frozen-input viewer contains 300 question/condition message sets and maps them exactly to all 9,600 slots.
- All models requested `high` effort and the same 8,192-token reasoning-plus-output cap. The effort label and tokenizers do not imply identical provider compute. The 4,967 truncations make these results strongly dependent on that fixed budget; no cap was increased or failed proof repaired after seeing its outcome.
- Contexts contain common, reference-informed premise selection disclosed before calls. This is not a certified-unseen dataset or evidence of unaided discovery of the full original research problems.
- Uniform verification used Lean 4.34.1, the pinned OAI/Mathlib artifacts, two CPU quota units, 8 GiB memory, 64 PIDs, 180-second validation and 900-second extraction limits. All 100 references passed the amended runtime. Every one of the 9,600 saved outcomes has an explicit v2 record; eligible completed proof candidates proceeded to Lean validation and extraction using `lean -j1`. Truncated or otherwise ineligible outputs received terminal outcomes without launching Lean. The core Python package and Lean evaluator remained frozen.
- The retired v1 checker’s 1,474 historical results are preserved and excluded from v2 completion and comparisons. The [runtime amendment](../experiments/openai_math_100_v1/amendments/runtime_threads1_v2/README.md) separates generation identity from verification provenance.
- Six requests with unknown outcomes after connection resets were held, then retried once with explicit user authorization. Original evidence and unknown costs were retained; no saved model response was repeated or selected against. See the [bounded recovery and audit extension](../experiments/openai_math_100_v1/amendments/astra_disconnect_recovery_v1/README.md).
- Cross-model ranks and pairwise displays are descriptive and conditional on available valid proofs. Within-model prompt-effect intervals use the frozen theorem-family bootstrap procedure; they do not turn sparse cross-model coverage into an overall quality conclusion.

## Validation and saved artifacts

The final response/cost audit passed its complete-cohort gate: 9,600 unique provider response IDs, 9,600 priced recorded responses, and 19,200 generation/capture files rehashed at the end. All eight phase-aware reports validated their original identities, metric/graph evidence and costs. Independent comparison checks covered 2,400 exact rational measurement cells and 87 ranking cases.

All 9,987 v2 result and ancillary files (77,969,958,154 bytes) match between Azure and the secured local copy. Earlier v1 evidence, all reference results, both immutable runtime images, recovery records and operational logs are retained. The final cloud-cost refresh changed only build time and cloud-ledger metadata; comparison contents, frozen question messages and all 120 per-model report files remained unchanged.

The initial prompt-viewer byte comparison exposed only JSON metadata key insertion order. Both outputs and the failed assertion were retained. Exact parsed messages/metadata, canonical hashes, and byte comparison after reordering only those run-hash keys all passed; no prompt text changed.

The local viewer’s 109 allowed routes were fetched and checked against file hashes. Raw response/run paths and external Host headers were rejected. Desktop rendering, coverage selection, cost disclosures and question selection were checked in the in-app browser with no console errors. The original all-model view was restored.

The full local artifact directory is `runs/om100-suite-high-20261013/`: `index.html`, `comparison-data.json`, `questions.html`, `questions-data.json`, and the eight `report-<model>/` bundles. To view an available local bundle:

```sh
python3 reporting/serve_comparison.py --suite runs/om100-suite-high-20261013
```

The viewer binds only to `http://127.0.0.1:62772/`; it publishes nothing externally. Full records remain separate from the route allowlist. See [reporting instructions](../reporting/README.md) for the exact comparison build and exports.


## Adjustable paper metrics

The local comparison now opens with explicit T₀, I₀, exploratory J₀-blend, and proof-concision rankings. It retains the 25-question coverage default and average per-question T₀ ranking; the original rank-points view is available below. Model-level aggregation is a descriptive extension of the paper's element-level measures.

The controls expose beta, prompt, model coverage, minimum usable samples, a fixed sample or all samples, maximum proof-body tokens, and question selection. Users can switch between average question rank and equal-question aggregate metric values. The same compared models and shared questions are used for every displayed rank in a view. Higher I₀ rewards longer proofs for these fixed statements. J₀ uses an explicitly labeled per-question maximum normalization; PageRank I₁/alpha is not measured. Exports include the controls and exact evidence. See [reporting methods](../reporting/README.md#adjustable-paper-metric-ranking).

This presentation extension does not change the 9,600 historical outcomes, frozen prompts, metrics or costs, and initiates no new model calls.


## Compression and correctness

The local report includes an interactive compression/Lean-acceptance scatter plot, correlations for all four metrics, and model-level or model/question-level views. The default five-model/22-shared-question T0 rank comparison has Pearson r=-0.346 and Spearman rho=-0.700 against acceptance over88scheduled attempts per model. With log2 T0 magnitudes, model-level r=-0.558; after centering within questions, the question-level r=-0.099. The comparisons are conditional on measurable successful proofs, so they do not establish that individual-proof compression predicts correctness or causes a trade-off.

Failures and truncated outputs stay in validity denominators, including when proof-length filters are active. Constant-validity and insufficient-support selections show unavailable coefficients. See [correlation definitions and limitations](../reporting/README.md#compression-and-validity-associations). No measurements or evaluation outcomes changed.


## Length across accepted and unsuccessful submissions

A separate post hoc view now measures submitted-text length for both accepted and unsuccessful outputs. It uses Unicode characters, non-whitespace characters or UTF-8 bytes rather than relabeling the original Lean parser-token W_B. All9,600slots have explicit length metadata:4,633completed nonempty submissions,551nonempty truncated partials, and4,416empty responses. Partial text is excluded by default; empty text is never assigned a proof length of zero.

Among completed submissions, accepted outputs (n=2,445) have median1,107non-whitespace characters, versus1,500for unsuccessful outputs (n=2,188). The pooled association is modest: Pearson(log2length, acceptance)=-0.184 and Spearman=-0.251. Centering within model × question × prompt groups gives Pearson=-0.139. Parse/format exclusions and inclusion of partial outputs are available as sensitivity controls. These character measurements and associations do not change any original evaluation outcome or paper-compression metric.

See [measurement policy and analysis definitions](../reporting/README.md#source-length-across-accepted-and-unsuccessful-outputs).
