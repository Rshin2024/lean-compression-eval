# Compression explorer reproducibility

This folder reproduces the explorer's exact measurement tables and tests its ranking arithmetic. It requires only Python 3.10+ and Node.js with built-in `node:test` support (Node 20+ recommended). No npm or Python packages, provider credentials, Lean installation, cloud resources, or network calls are needed.

The browser helper `rankings.mjs` is byte-identical to the reviewed historical helper (SHA-256 `6440b8a85a979b3826a9d87a849017e6d747c8ab4b2e265ecf4cec99d3cea20e`). The portable builder changes input/output locations and binds the existing published report's eleven component IDs; its exact numerical projection and comparison formulas are preserved. Historical sources and frozen outcomes are not edited.

## Run from the published package root

From repository root, first enter `analysis/om100-20261014`:

```sh
node --test reproducibility/compression-explorer/test_rankings.mjs
python3 -B -m unittest discover -s reproducibility/compression-explorer -p 'test_queries.py' -v
python3 -B reproducibility/compression-explorer/prepare_queries.py --output /tmp/om100-explorer-rebuild
```

The output directory must not already exist; choose a fresh path for every build. Its parent must exist. The builder rejects output locations inside the package's `inputs`, `tables`, `analysis-report`, or `reproducibility` directories. It writes only the new directory, containing `queries-addon.json`, `rank-reconciliation.json`, and `validation.json`. It never updates the published app or frozen evidence.

The default package root is two directories above this folder. `--package-root PATH` supports a copy elsewhere, and `--input RELATIVE_PATH` can identify an input inside that package. Tests accept the optional `EXPLORER_PACKAGE_ROOT` environment variable for the same purpose. Missing published inputs fail the tests rather than silently skipping them.

Required existing package files are:

- `inputs/om100-max-suite-high-20261014/comparison-data.json`
- `tables/main/task_metadata.json`
- `tables/main/conditional_ranks.json`
- `tables/main/conditional_ranks_by_question.json`
- `tables/main/coverage.json`
- `tables/main/analysis_manifest.json`
- `analysis-report/src/data.json` (tests only)

Tests read the already published app's query rows; this folder contains no duplicate 8 MB addon. The only golden fixture is the compact independent default six-model/23-question oracle. Full coverage/rank expectations are read from the existing statistical tables, avoiding another full oracle copy.

## Validation and scope

The 23 JavaScript tests check exact ties and unequal sample counts, fixed shared-question populations, equal-question geometric aggregation, nulls, huge integers, immutable inputs, and individual-output rank stability after display filtering. They reconcile all 24 relevant T0/WB statistical-table cases (4,264 question/model midranks), all 138 default ranks, and all 9,600 output identities. The 12 Python tests check source formulas, missing values, schedule completeness, exact products, ranges, default ranks, complete cells, and safe output paths. The builder additionally reconciles all 36 T0/I0/W_B cases and 6,396 question/model midranks.

`package-validation.json` records the staging checks. Rebuilt query rows match the published app exactly. Timestamps, source paths and source-description envelopes can differ because this builder records portable input paths and a new build time; the generated addon is not promised to have the historical byte hash.

### Browser helper API

- `aggregateRanks(cells, {condition, models, metric})` keeps the selected roster fixed and uses only its shared measured questions. It returns explicit unavailable results for an empty intersection or fewer than two models. `metric` accepts `T0`, `WB`, or `W_B`; lower W_B ranks first.
- `rankQuestion(cells, {condition, task, models, metric})` retains all models, ranks only selected measured models, and flags questions outside the shared aggregate. Counts in this historical helper assume the complete reviewed cell table; the Python package tests enforce that complete Cartesian table. Do not pass a partial cell projection as a complete observation table.
- `rankOutputs(allAttempts)` ranks each complete eight-model/four-sample question/prompt population before display filtering. It rejects incomplete groups. Filter its returned array afterward; do not recalculate ranks from filtered rows.

Ranks use BigInt rational products and exact root comparisons. Floating values and scientific notation are display fields only. Rank points are relative placements, not percent compression, acceptance probabilities, or general reasoning-quality scores. No dynamic confidence intervals or new model evaluations are produced here.
