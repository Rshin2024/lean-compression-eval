# Selected evaluation questions: OpenAI Math 100

The user selected `openai_math_100_candidates.jsonl` and its accompanying HTML
catalogue on October 13, 2026. These **100 questions replace the earlier
250-candidate collection as the selected question set for the next experiment**.
The completed ten-question smoke run and its frozen records remain historical
results. No new model calls are part of adopting this selection.

The active selection is recorded in `configs/active-question-set.json`. Inspect
its immutable adoption record and file integrity with:

```sh
./run-eval question-set
./run-eval question-set --require-ready
```

The second command intentionally fails for the original **selection catalogue**.
Its `selected_pending_lean_preparation` status records the historical adoption
step and is not rewritten when a separate prepared bundle is created. Selecting
questions, preparing Lean tasks, freezing prompts and launching an evaluation
are distinct stages.

## Preparation and run status

All **100/100 selected questions now have standalone materialized targets with
successful reference validation and available compression measurements**. The
original reference proof bodies are unchanged. Source scope, implicit parameters
and dependent `let` binders were recovered and checked during preparation; no
questions were dropped or replaced. The separate `materialized_final_v1` bundle
records each preparation result and its evidence hashes.

All 100 final uniform-policy reference checks and model-visible premise audits
have passed. **The 9,600-attempt evaluation launched on October 13, 2026 (Pacific
time), after all eight manifests were frozen and the reviewed launch marker was
published.** Both providers authenticated and every configured model has returned
responses. Final results are pending; the immutable adoption status is not the
live run status.

The cohort requests `high` effort with an 8,192-token reasoning-plus-answer cap,
three conditions and four samples per question/model/condition. Truncated or
invalid responses remain recorded outcomes under this fixed budget. API calls
run in the local authenticated process; 48 verification workers run on the
64-vCPU, 512-GiB Azure VM. Validation has a 180-second wall limit and measurement
has a separate 900-second limit, each with a 2-CPU quota and 8-GiB memory ceiling.
The core code snapshot is commit `eab82c85` (Python package hash beginning
`958bc767`); the frozen manifests record the complete code and runtime hashes.

The main comparison uses exact rational geometric-mean T0 ordering within each
question, then averages tied ranks with equal question weight. A comparison
cohort uses only questions with a valid measurable output from every included
model. Changing the cohort recomputes that intersection. Solve rates, coverage
and missing measurements remain separate; no common questions means no aggregate
rank. Reference attainment is an optional diagnostic.

The repository snapshot of the reviewed inputs is
[`data/prepared/openai_math_100_v1`](../data/prepared/openai_math_100_v1/README.md):

- `private/tasks.jsonl` and `private/references.jsonl` are exact copies of the
  reviewed `work/full-eval-100/ready_v1` files. All 100 tasks have generation
  eligibility enabled; that is distinct from permission to launch a process.
- `root-review.json`, `index.json` and `audits/` bind the final inputs to the
  independent input, premise, family and official-reference audits. Earlier
  stage metadata remains unchanged and can retain historical pending flags.
- `manifest.json` records file hashes, eight frozen run identities and the
  corresponding configuration hashes. `configs/active-question-set.json` has
  a separate `prepared_snapshot` pointer; its original adoption pointer and
  manifest hash remain unchanged.
- [`configs/om100`](../configs/om100) contains the exact frozen environment,
  reference policy and eight model configurations. Local live run directories
  are `runs/om100-<model>-high/`, with their own immutable `manifest.json` files.

The approximately 3.1 GB reference-anchor graphs and the Docker image are not
duplicated into this repository package. The independent audit retains the
anchor hashes, and the complete original evidence remains in
`work/full-eval-100/reference-checks-v1`. Another freeze still needs that evidence
or independently regenerated, re-audited anchors. Packaging this snapshot does
not create `launch_ready.json`, submit model calls or alter the frozen runs.

The `private` folder contains source compiler contexts and reference answers;
it is an organizational boundary, not encryption. Send only the harness-built
frozen messages to a model, never the complete private JSONL files.

The exact preparation, repair, premise-export, orchestration and runtime source
is archived with SHA-256s in
[`experiments/openai_math_100_v1`](../experiments/openai_math_100_v1/README.md).
It contains code and pins, not generated responses, private reference records,
cloud access files or credentials. The archive documents deployment-specific
paths and the inputs needed to replay preparation.

## Identity and composition

All 100 supplied `OM100-001` through `OM100-100` IDs are retained as unique
question IDs. The attachment's repeated `task_id` identifies a parent problem;
it is preserved as `source_parent_task_id`, not used as a unique question ID.
The selected declarations span 92 source files and 19 result families.

| Supplied selection group | Questions |
| --- | ---: |
| Algebra | 25 |
| Analysis | 25 |
| Combinatorics | 25 |
| CS / probability | 25 |

These four supplied groups are preserved. The old five-domain, 50-pilot /
200-main quota rules do not describe this selection. No replacement questions
or arbitrary pilot/main split have been introduced.

## What is stored

`data/question_sets/openai_math_100_v1/selection.jsonl` contains the allowlisted
question identities, formal statements and provenance. It is a **selection
catalogue**, not a runnable `freeze --tasks` manifest. The versioned directory
also contains a manifest, per-question preparation status and a `private/`
directory with unchanged supplied files, the 100 reference declarations, and
source files checked against the pinned upstream revision. A file named
`private` is an organizational boundary; these files must not enter model
inputs or a generated-proof runtime.

The importer verifies unique candidate IDs, declaration identities, family and
group counts, source pins, reference hashes, line spans, and the agreement of
the HTML and JSONL. With `--verify-upstream`, it also checks the pinned full
source files and their selected declaration excerpts. Re-adopting unchanged
inputs is idempotent; altered evidence requires a new versioned directory.

The HTML contains complete proofs and hidden search text with solution hints.
The JSONL contains proof summaries and extraction suggestions. Neither file is
a model prompt. Reference declarations and curator hints are retained separately
from the allowlisted selection. Source and hash checks establish provenance,
not Lean correctness or independent mathematical novelty.

## Preparation policy

All selected questions remain accounted for, including any that fail
preparation. Before generation:

1. Recover imports, scope commands, universes and implicit section parameters
   from the exact pinned source. Do not execute prose `section_context` fields.
2. Reconstruct each target's preceding environment. Keep approved earlier
   helper statements available; remove the target, equivalent answer aliases,
   and downstream results that reveal it. The attachment's optional hard-track
   suggestions are curator notes, not instructions changing the task.
3. Freeze explicit Lean signatures, independently check the extracted reference
   proofs, and audit model-visible premises for answer leakage. Parent-task
   oracle acceptance does not verify these extracted lemmas.
4. Build and hash the matching isolated Lean/Mathlib environment. This corpus
   uses Lean 4.34.1 and Mathlib `d13f23b723b8a846827a245b89c10fc7d3f11612`,
   distinct from the completed smoke runtime.
5. Specify and validate expansion of the added `OAI` library as well as Mathlib
   and evaluator-owned declarations. Do not silently count this new library
   as an atomic dependency under the old metric boundary.
6. Review cross-question dependencies and family assignments. Some selected
   lemmas are prerequisites of other selected lemmas; any development/test
   separation must keep a family together. Uncertainty should account for
   the 19 families, while the displayed question averages keep equal question
   weights as requested.

The intended comparison remains output-to-output compression, with correctness
and missing measurements reported separately. Reference proofs support checking
and provenance; reference attainment is not the main model ranking. The existing
eight models and explicit `high` effort are the authorized cohort. The protocol
uses three conditions and four samples, scheduling 1,200 attempts per model and
9,600 across eight models. Each generation has an 8,192-token output cap; the
common `high` label does not guarantee equal reasoning compute across providers.
The final source-stripped runtime uses Lean 4.34.1, the pinned Mathlib revision,
and the 457-module required OAI closure. Validation is capped at two CPUs, 8 GiB
and 180 seconds per attempt; extraction has a uniform 900-second limit. The
observed runtime and environment hashes are recorded in the archive's `pins.json`.
Final frozen manifests remain the authority for execution; a planned schedule
or successful reference check alone does not start model calls.

Model-visible helper retrieval is **reference-informed**: target/reference
constants seed a bounded set of interfaces from the environment preceding the
target. The exporter suppresses proof bodies and excludes the target and its
post-target auxiliaries. All models and conditions receive the same frozen
interfaces, with a 56,000-byte context budget under a 64,000-byte user-message
cap. This is a comparison conditioned on those supplied helper interfaces.
Literal absence checks and prior-environment membership are useful audit evidence;
they do not prove that no logically equivalent answer alias is available.

The combined report defaults to all eight models on their shared measurable
questions. Within-question ordering and ties use exact rational geometric means
of T0; the question ranks are then averaged with equal question weights. If no
question is measurable for every selected model, the page shows coverage and
pairwise comparisons without inventing an aggregate rank. See the
[reporting definitions](../reporting/README.md).

The user also requested cost recording. The eight model profiles now require
dated pricing snapshots; new generation records and analysis exports include
per-attempt and per-question/model/condition API token-cost estimates. See
[cost tracking](cost-tracking.md). Missing usage and unaccounted transport charges
are disclosed rather than treated as zero.

## Reproduce selection adoption

```sh
./run-eval select-questions \
  --candidates /path/to/openai_math_100_candidates.jsonl \
  --catalogue /path/to/openai_math_100_candidates.html \
  --output data/question_sets/openai_math_100_v1 \
  --verify-upstream
```

The upstream revision and FineEnvs revision come from the checked input records.
Their fixed references are `adc7f1241b42e322a6451854ab7e4b4c146bf78a` and
`d7056fd31c19fd1a0aed0a0bc3e99026d412b2ca`, respectively. The immutable adopted
records describe their original unverified standalone-extraction state. Later
standalone checks are recorded in the separate prepared bundle; they do not
retroactively change those input records or certify absence from model training.
