# Mathematical compression in Lean

A working evaluation harness for the attached plan: give the same Lean target to three prompts—ordinary correctness, mathematical compression, and source concision—and measure correctness independently from compression.

This is a **paper-aligned implementation**, not an exact reproduction of the authors' code. The experiment measures submitted proof representations under one library. A larger expansion/source ratio is not, by itself, better reasoning.

## Current status

The package includes the Lean checker/extractor, exact metric engine, immutable run scheduler, OpenAI Responses adapter, offline response import, environment hashing, statistical reports, and blinded proof-audit exports. The candidate dataset contains 50 pilot and 200 main goals, including the requested 150 library/50 fresh main split across five domains.

**The candidate collection is not a released, audited benchmark.** Task audit fields and the reference evidence distinguish compilation, independent verification, metric availability, explicit signatures, and unresolved family/answer-access review. The main experiment must remain unfrozen until those gates pass. No paid model generations have been made, and no model is selected.

Local tests use the existing Lean/Mathlib installation through a development sandbox. That profile blocks network access and restricts user/private file reads, but permits system IPC and does not enforce a CPU quota or memory ceiling. **It is for trusted reference fixtures only.** The CLI requires a container for live model generations and generated-proof evaluation. Production freezing additionally requires a content-addressed Docker image, environment hashes collected in that image, enforced resource limits, and completed dataset audits. The container recipe is included; use it with a running Docker daemon.

See `BUILD_STATUS.json` for checked counts and remaining gates, and [the dataset readiness report](examples/dataset-readiness/readiness.html) for the evidence behind them. [The example report](examples/report/report.html) is an explicitly labeled reference-replay demonstration, **not an LLM experiment**. Its data and generated HTML were checked; a rendered browser preview was unavailable because the in-app browser blocks local file URLs.

## Start here

Python 3.11+ is sufficient; the core has no third-party Python dependencies. Run commands from this directory:

```sh
export PYTHONPATH=src
python3 -m lean_compression doctor
python3 -m unittest discover -s tests -v
python3 -m lean_compression validate-dataset \
  --tasks data/public/tasks.jsonl --references data/private/references.jsonl --enforce-quotas
```

Lean integration tests require the pinned compiler and dependency paths. See [the Lean engine guide](docs/lean-engine.md) for their environment variables. Pure arithmetic, analysis, audit, dataset, and orchestration tests need no Lean.

The current pin is Mathlib `274ed6d67d9b226c557813b9ce437574cccdce11`, whose declared toolchain is `leanprover/lean4:v4.34.0-rc1`. `configs/environment.local.json` records this machine's installed dependency locations; `configs/environment.hashed.json` additionally hashes source and compiled artifacts. To use a different checkout, regenerate the environment record rather than editing the hashes:

```sh
python3 -m lean_compression prepare-environment \
  --package-root /absolute/path/to/a/locked/lean/project \
  --output configs/environment.new.json
```

## Check references locally

```sh
python3 -m lean_compression verify-references \
  --tasks data/public/smoke.jsonl \
  --references data/private/smoke_references.jsonl \
  --environment configs/environment.hashed.json \
  --config configs/development.json \
  --output work/reference-checks
```

Each reference has two separate fresh processes: verification, then extraction. The output retains kernel replay evidence, axiom audit, original source token spans, weighted dependencies, resource evidence, and exact integer measurements. The command resumes already written records; a changed checker/configuration requires a new output directory. References are private files and are never included in generation prompts.

Build a reusable imported-declaration index from those verified closures:

```sh
python3 -m lean_compression index-closures \
  --references work/reference-checks --environment configs/environment.hashed.json \
  --config configs/development.json --output work/environment-index
```

The index covers observed closures, not every Mathlib declaration. Unknown dependencies remain errors; they are never guessed to be primitive. Candidate-owned declarations stay outside the shared imported-node cache.

## Choose a model later

`configs/pilot.json` intentionally leaves `generation.model_snapshot` and `reasoning_setting` unresolved. Set a supported pinned model, explicitly select its reasoning/sampling settings, and set `snapshot_pinned` and `settings_resolved` only when those claims are supported. Some models do not accept `temperature`; set it to `null` to omit it deliberately. The adapter never retries a bad request with changed settings. OpenAI's output limit includes reasoning tokens, and reported reasoning usage is retained.

Configure `OPENAI_API_KEY` in your shell or secret manager. Do not put it in project files. The checker process never inherits API credentials. A Codex subscription is not treated as API access.

For a live pilot development run with a small task manifest, first create the container runtime below and save its image/settings as `configs/pilot-ready.json`:

```sh
python3 -m lean_compression freeze --development \
  --tasks data/public/smoke.jsonl --environment configs/environment.docker.json \
  --config configs/pilot-ready.json --split pilot --model YOUR_PINNED_MODEL \
  --run runs/pilot-development
python3 -m lean_compression export-prompts \
  --run runs/pilot-development --output work/prompts.json
```

Only this next command makes model API calls:

```sh
python3 -m lean_compression generate --run runs/pilot-development --limit 120
```

It starts a fresh request for each slot with no tools, prior responses, reference proofs, or validator feedback. Raw messages, responses, usage, provider IDs, timestamps, transport retries, and refusal/truncation statuses are retained. Model-version mismatches cannot be silently pooled. An ambiguous lost response retains its in-flight marker for reconciliation, avoiding an automatic second completion.

Other providers can supply JSONL records to `import-responses`; every record must include `attempt_id`, `prompt_hash`, `model_snapshot`, and the entire `raw_response`. Preserve provider settings/usage/provenance as well. The exported prompts contain the required IDs and hashes.

## Verify, report, and reproduce

```sh
python3 -m lean_compression evaluate --run runs/pilot-development --version v1
python3 -m lean_compression analyze --run runs/pilot-development --version v1 \
  --references work/reference-checks --output reports/pilot-development
```

The report includes all scheduled slots, correctness/coverage, paired B−A and B−C effects, their source/expansion decomposition, common-task sensitivity, reference attainment, family-cluster bootstrap intervals, plots, and a proof-audit bundle. Confidence intervals do not remove success-selection bias. An unfinished run is labeled incomplete and cannot support a conclusion.

`resource_usage.csv` reports observed timing, token usage and memory peaks, including failed attempts. Missing measurements stay unavailable. It does not infer billing costs or add reasoning tokens to an output-token total that already includes them.

`analyze` never calls a model or invokes Lean. `rescore --from-version v1 --version v2` recomputes metrics from stored graphs and records an amendment. Rechecking with a changed Lean extractor uses `evaluate --version v2`; raw generations remain unchanged. Frozen manifests and existing evidence refuse conflicting overwrites.

## Production resource profile

```sh
python3 runtime/prepare_image.py --output configs/environment.docker.json
```

This builds the pinned Lean/Mathlib runtime and records its immutable image ID. Copy that ID to `validation.docker_image` in a new configuration; set `isolation` to `docker`. Candidate execution disables networking, drops capabilities, mounts the engine read-only, makes the image filesystem read-only, limits processes, and enforces the configured CPU/memory budget.

Use the environment record collected inside that image for production. Host and container build hashes are not interchangeable. The build hashes source before removing package Lean source files and Git history from the execution image; `prepare_image.py` checks that exclusion before writing the runtime manifest. Reverify references in the container and pass their directory through `freeze --reference-checks`. Complete task audits, pilot calibration, and preregistration before freezing the main evaluation. A production freeze fails closed if the environment, reference evidence, or task prerequisites are unresolved.

[Runtime acceptance instructions](docs/runtime-validation.md) enable actual container checks when Docker is available. These were skipped on the current machine because the daemon was unavailable. Local reference checks and the Lean integration tests did run.

## Definitions and scope

- `W_S` / `W_B`: distinct original Lean parser leaf spans in explicit binders/proposition and submitted proof; excludes the theorem keyword, target name, and separator. Full declaration counts and UTF-8 byte spans are also stored.
- `U_S` / `U_B`: weighted recursive reference counts. Every occurrence contributes, including repetitions through shared expression nodes. Mathlib and evaluator declarations expand; other frozen package declarations and `Sort` are atomic.
- `T0 = (U_S + U_B) / (W_S + W_B)`; `I0 = W_B / W_S`; `D` is longest dependency depth. Counts use arbitrary-precision integers, ratios preserve exact numerators/denominators, and logarithms use leading bits.
- Cycles, unknown dependencies, zero-reference nonprimitive declarations, absent bodies, and extraction timeouts remain explicit missing-metric states. They do not turn verified proofs into failures.
- Tactic automation, aliases, and irrelevant dependency growth can change the scores. Inspect the numerator/denominator breakdown and sampled proofs.
- A real Lean stress test gives an alias chain a 3× higher `T0` with identical source length. Both proofs are valid; this is a tested example of score inflation without source savings.
- The optional new-lemma transfer, PageRank `I1`, and synthetic monoid studies are outside the core implementation. `f(s)` is not measured by this finite Lean benchmark.

## Sources

- [Supplied evaluation specification](docs/evaluation-plan.md).
- Aksenov, Bodnia, Freedman, Mulligan, [Compression is all you need: Modeling Mathematics](https://arxiv.org/abs/2603.20396), especially sections 3 and 5.
- [Pinned Mathlib source](https://github.com/leanprover-community/mathlib4/tree/274ed6d67d9b226c557813b9ce437574cccdce11).
- [Lean proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/); executable API behavior is tested against the older pinned toolchain.
- [OpenAI text generation](https://developers.openai.com/api/docs/guides/text) and [reasoning token behavior](https://developers.openai.com/api/docs/guides/reasoning).
