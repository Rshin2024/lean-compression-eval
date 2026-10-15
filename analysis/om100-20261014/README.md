# Lean compression study: paper, analysis and benchmarks

Start with **`lean-compression-study.pdf`**, the 26-page technical paper. It
contains the complete study design, exact prompts, verification process,
statistical methods, results, costs, limitations and supplementary tables.
This is an exploratory draft for review, not a preregistered or peer-reviewed
publication. No human authorship or affiliation has been assigned.

## Read and share

- **Paper:** `lean-compression-study.pdf`. Editable LaTeX and 11 scientific
  figures are in `paper/`; run `tectonic paper.tex` from that directory.
- **Interactive analysis report:** `analysis-report/dist/index.html`. The
  compression explorer at the top now exposes raw output measurements,
  per-question ranks and aggregated model rankings. See the launch instructions
  below, or open `compression-explorer-offline.html` after downloading it.
- **Executed notebook:** `analysis.ipynb`; `analysis.html` is a self-contained
  saved preview requiring no Python to read.
- **Social benchmark 1:** `social/01-questions-solved.png` and editable SVG.
- **Social benchmark 2:** `social/02-compression-head-to-head.png` and editable SVG.
- **Two draft posts:** `social/draft-posts.md`. These are drafts; no social-media
  posts were published by this workflow.
- **Definitions:** `social/benchmark-definition.md` gives formulas, exact
  supports and uncertainty for the two cards.

## What the study finds

The main 128,000-token cohort contains 9,600 scheduled attempts and 3,831
accepted proofs. The separate 8,192-token cohort contains another 9,600
attempts and 2,445 accepted proofs. The earlier 960-attempt smoke test is
reported separately: its tasks and environment are different.

Under the standard prompt, Fable solves 96/100 questions in four attempts,
Astra 89, GPT-6.1 Sol 84, and Opus 81. This is observed four-sample coverage,
not single-attempt accuracy or a general mathematical ability score.

Among shared questions with successful proofs under the compression prompt,
Fable earns 60.9% of exact T0 comparison points against Astra, but 48.7%
against Opus. The Fable–Opus interval includes a tie. Every pair has its own
selected question population; no question supports a pure compression
comparison across all eight models.

Compression instructions often shorten accepted source without improving
end-to-end acceptance. Format failures are an important confound, and the
negative source-length/correctness association reverses after excluding parse
failures. Extreme T0 increases can arise from implicit library/typeclass
dependency expansion. T0 is not interchangeable with source concision or
reasoning quality.

## Open the compression explorer

From the repository root, serve the complete built report directory:

```sh
python3 -m http.server 62775 --bind 127.0.0.1 --directory analysis/om100-20261014/analysis-report/dist
```

Then open <http://127.0.0.1:62775/?view=1#compression-explorer>. Use a different
port if that one is occupied. The `dist` directory must include its JSON sidecar;
opening only `dist/index.html` through a file URL will not load the split data.
Alternatively, download and open the self-contained
[`compression-explorer-offline.html`](compression-explorer-offline.html).
Neither option requires API keys, a provider account, Lean, or cloud compute.

- **Model ranks:** rank points, average per-question rank, first-place counts,
  raw geometric-mean T₀ and proof length W_B. Adjust the prompt, metric and model
  roster. Each aggregate uses the same shared questions for every selected model.
- **Question ranks:** all eight models' measurements and sample coverage for any
  of the 100 questions. Select a model to inspect its four scheduled outputs.
- **Outputs:** all 9,600 final-run outcomes, including 3,831 measured proofs.
  Select an output to inspect its exact T₀ fraction and expanded/source sizes.
  Display filters do not rerank the underlying eight-model output comparison.

The default compression-prompt view selects the six models with measurements
on at least 25 questions; they share 23 questions. It gives Fable 75.65 rank
points, Opus 73.04 and Astra 68.70. These are descriptive, conditional standings:
points = 100 × (number of models − midrank) / (number of models − 1), averaged
equally over shared questions. They are not percentage compression or a claim
that nearby models are statistically distinguishable. Selecting all eight
models leaves zero shared questions in this prompt and explicitly displays
unavailable aggregate ranks. Failed proofs are never assigned compression zero.

The exact-arithmetic implementation, portable data builder and tests are in
[`reproducibility/compression-explorer`](reproducibility/compression-explorer/README.md).
The preview image is [`compression-explorer-preview.jpg`](compression-explorer-preview.jpg).

## Statistical scope

The new analysis is post hoc. It uses 10,000 resamples of 19 source-result
families, with a question-bootstrap sensitivity analysis. Yield and paired
metric comparisons weight questions equally; regression estimands use their
stated within-cell or design-matrix weighting. Family dependence, small cluster
counts, selected successful-proof populations, multiple comparisons, unknown
training exposure and changed cross-run generation policies are explicit in
the paper. There is no causal claim about the output ceiling alone.

The two headline cards keep reliability and conditional compression separate.
A newly designed joint contest is included only as a clearly labeled
supplemental analysis, with reliability and compression contributions shown.

## Data and reproduction

`data/` contains the 19,200 normalized substantive observations, the primary
9,600-row export, task metadata and smoke summaries. `tables/main/` and
`tables/cross-run/` contain the complete reviewed statistical tables. Exact
comparison snapshots and the supplied candidate metadata are in `inputs/`.
These are derived measurements and provenance, not full raw API responses.

See `reproducibility/README.md` for the executed notebook, pinned Python
requirements, original analysis scripts and portable statistical replay.
The primary replay reproduces all estimates and interval endpoints; tiny
floating-point summation differences in some SE/p-value fields are bounded
below 1e-12 and explicitly recorded. Cross-run replay tables are byte-identical.

Use the documented `reproducibility/reproduce.py`, `execute_notebook.py`, and
compression-explorer commands as the portable entry points. Some archived
figure/paper/social generator scripts retain their original workspace paths;
they are provenance snapshots, not portable launchers. The editable paper and
figure exports are included independently. The Data app's bundled `package.json`
comes from its original runtime template; its generic `npm test` command is not
the study test suite. Run the statistical and explorer tests documented under
`reproducibility/` instead. The prebuilt and offline dashboards need no build.

The full raw response/SSE, proof-graph, runtime-image and verification audits
remain in the original local evaluation archive. They are deliberately not
duplicated in this bundle. No new model calls, Lean checks or cloud jobs were
run for the statistical analysis. The original evaluation results are unchanged.

## Costs

Recorded-response API estimates are $1,848.44 for the initial substantive
cohort and $4,369.72 for the rerun. The initial VM figure is a historical as-of
subtotal of $56.82; the rerun's closed compute estimate is $59.33. Seven initial
and 30 rerun original requests have unknown charges, and non-compute cloud
items remain outside these estimates. These figures are not complete invoices.

`artifact-manifest.json` hashes this GitHub package. Statistical, manuscript,
notebook, figure and rendered-report verification receipts are retained under
`reproducibility/`. `provenance/initial-delivery-manifest.json` is the historical
pre-explorer delivery manifest, not the manifest for the current dashboard.
`compression-explorer-revision.json` preserves the later live-report revision
receipt, including original local lineage paths. The earlier ZIP and duplicate
build caches are omitted; this directory contains the current expanded package.
`provenance/export-scope.json` records those exclusions. Raw provider/SSE logs,
credentials, runtime images, local services and virtual environments stay outside
the repository.
