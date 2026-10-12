# Validation and release scope

The delivered software implements the core three-condition experiment. It has not run an LLM comparison or a paid API pilot.

## Executed checks

- **90 tests passed.** These include exact arithmetic, family bootstrap and selection accounting, immutable/resumable bookkeeping, dataset validation, graph-cache reuse, readiness checks, and real Lean integration. The Docker acceptance class was skipped because a daemon was unavailable; its two local syntax checks passed.
- **250 current reference proofs were independently kernel checked and measured:** 50 pilot and 200 main, with 150 library and 50 fresh main goals. Each validation and extraction stage retained its separate 60-second limit and the configured heartbeat budget.
- The reference set has explicit signatures. Reconstructed library goals preserve predecessor context and import modifiers; signature materialization was compared against the reconstructed original using Lean's kernel equality checking.
- The complete pipeline was exercised with one reference proof replayed under the three prompt labels. All three attempts, tables, four plots, resource summaries, and audit files are retained. The committed audit bundles are `frozen_blinded_v1`: their codes can be recomputed from the run manifest, so they are not blinded review material. This is a software fixture, not model performance evidence. Its run, `examples/pipeline-demo`, is a development artifact from earlier harness builds: `evaluations/v1` predates the engine-change guard and mixes engines `275337fa` and `46217c8f` (the `mathematical_compression` record), so current `analyze` rejects it. `examples/pipeline-demo/amendments.jsonl` records v1 as a superseded mixed-engine version; `evaluations/v2` re-evaluated the same three generations with engine `3c093c02` alone, is the only analyzable version, and is the one reported and reproduced below.
- Cached imported-declaration evidence reproduced all ten smoke proof metrics exactly. The cache checks environment, boundary policy, declaration metadata, and complete weighted histograms.
- A real alias-chain fixture increases `T0` from 1 to 3 while both source counts remain 1. It demonstrates why a larger ratio must not be called better reasoning without examining its components.

See `test-results.txt`, the reference index in `data/private/reference-index.json`, and the current dataset/readiness reports for exact versions and records. Superseded reference records remain archived; they are not silently relabeled as executions of the current checker. In the archived index `data/private/reference-index-before-normalization.json`, `archived_evidence_path` locates each of the 57 superseded `3c093c02` records under `history-engine-3c093c02/`. `lean/versions/` retains checker sources `3c093c02`, `d15991b0` and `83da0074` only; engines `275337fa`, `46217c8f`, `44db1625`, `68575c77`, `a10ae4d0`, `a60a7ec4` and `b6f40fc9`, cited by archived development evidence and `examples/pipeline-demo/evaluations/v1`, have no retained source.

## Before a confirmatory model study

The dataset remains a candidate benchmark. The targeted review repaired known aliases and related constructions crossing splits, but a broader family, known-equivalent-statement, and difficulty/length/depth review remains open. Readiness flags deliberately preserve these gaps.

Select the model snapshot and supported generation settings, configure API access, build and acceptance-test the pinned container, and verify references inside that runtime. Host and container environment hashes are distinct. Run the pilot, assess metric coverage/runtime/precision, and freeze the protocol and strata before main generation.

The generated report's data and HTML structure were checked. Rendered browser inspection was unavailable because the in-app browser rejected local file URLs. No browser-security workaround was used.

## Reproduction without new model calls

From the project root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m lean_compression analyze \
  --run examples/pipeline-demo --version v2 \
  --references examples/demo-reference-check \
  --output work/reproduced-example-report
```

Use a new report directory on each reproduction. The stored generation and evaluation records are immutable. The full source/dependency evidence supports metric recomputation independently of any provider account.
