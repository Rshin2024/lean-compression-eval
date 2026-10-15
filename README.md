# Mathematical compression in Lean

Evaluate whether language models can write correct Lean proofs that reuse mathematical structure efficiently. The measurements are based on [Compression is all you need: Modeling Mathematics](https://arxiv.org/abs/2603.20396).

Each model receives the same theorem under three prompts:

- **Standard:** produce a correct proof.
- **Mathematical compression:** favor mathematical reuse and abstraction.
- **Source concision:** minimize the proof's source length.

The harness checks proofs with Lean, measures their source and dependency graphs, and produces reports comparing correctness, compression, token usage, and API costs. It supports OpenAI, Anthropic, and imported responses from other providers.

## Getting started

Requires **Python 3.11+** and **Docker** for checking generated proofs. The Python package has no runtime dependencies.

```sh
git clone https://github.com/Rshin2024/lean-compression-eval.git
cd lean-compression-eval
./run-eval doctor
./run-eval --help
```

The launcher works without installing the package. Alternatively, `python3 -m pip install -e .` installs the `compression-eval` command.

Set the key for the provider you use in the same shell that runs the evaluation:

```sh
export OPENAI_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-api-key"
```

Anthropic user keys may also require `ANTHROPIC_WORKSPACE_ID`. Keys stay outside configuration files and are not passed to proof-checking containers.

## Running an evaluation

Choose a [model configuration](configs/models/) and set the model ID, reasoning effort, output budget, pricing, and checker limits. Build the runtime with `python3 runtime/prepare_image.py --output environment.json`, then set `validation.docker_image` to the printed image digest. The task file, Lean/Mathlib settings, and runtime must match; committed environment files contain installation-specific paths.

For an exploratory run, with your task file, reference proofs, environment record, and configuration:

```sh
# Check reference proofs and extract their measurements.
./run-eval verify-references \
  --tasks tasks.jsonl --references references.jsonl \
  --environment environment.json --config config.json \
  --output reference-checks

# Fix the prompts, settings, and sample schedule before generation.
./run-eval freeze --development \
  --tasks tasks.jsonl --environment environment.json --config config.json \
  --split pilot --model MODEL_ID --reference-checks reference-checks \
  --run runs/my-eval

# Generate responses, check proofs, and build the report.
./run-eval generate --run runs/my-eval
./run-eval evaluate --run runs/my-eval
./run-eval analyze --run runs/my-eval \
  --references reference-checks --output reports/my-eval
```

Only `generate` makes paid model calls. Runs resume from saved work; use a new run directory when changing inputs or settings. Inspect uncertain API outcomes with `./run-eval reconcile --run runs/my-eval` before retrying them.

Reference proofs are checker inputs, not model prompts. The [prepared 100-task dataset](data/prepared/openai_math_100_v1/README.md) includes the selected tasks and references; its full runtime image and reference graphs must be obtained or rebuilt separately.

## Metrics

`W_S` and `W_B` count Lean source tokens in the statement and proof. `U_S` and `U_B` count recursively expanded dependency references, including repeated uses.

| Metric | Definition | Interpretation |
| --- | --- | --- |
| T₀ | `(U_S + U_B) / (W_S + W_B)` | Expanded references per source token |
| I₀ | `W_B / W_S` | Proof length relative to statement length |
| Proof length | `W_B` | Smaller means shorter proof source |
| Dependency depth | Longest dependency path | Depth of the proof's dependencies |

Correctness is measured separately. Compression metrics require valid, measurable proofs; character and byte lengths can also be measured for unsuccessful outputs. Larger T₀ does not necessarily mean a shorter proof or better reasoning, and larger I₀ means a longer proof relative to its statement.

Model rankings compare outputs on shared questions, then aggregate their per-question ranks. Always read ranks alongside proof acceptance and question coverage. See [metric definitions](docs/analysis.md) and [reporting methods](reporting/README.md).

## Results

The OpenAI Math 100 experiments use selected Lean targets with reference-informed premises: **100 tasks × 8 models × 3 prompts × 4 samples = 9,600 attempts** each. Both use high reasoning effort; token budgets cover reasoning and answers.

| Output-token budget | Accepted proofs | Truncated responses | Details |
| --- | ---: | ---: | --- |
| 8,192 | 2,445 | 4,967 | [Results](docs/om100-results.md) |
| 128,000 | 3,831 | 123 | [Results and reproducibility](experiments/openai_math_100_provider_max_v1/README.md) |

Transport and processing tiers also differ between runs, so the table does not isolate the effect of the token budget.

128,000 tokens is still a provider ceiling. No question in that run has measurable outputs from all eight models under the compression prompt, so a ranking across all eight is unavailable. Smaller comparison groups have their own shared questions. Training-data overlap is unknown.

The [complete analysis package](analysis/om100-20261014/README.md) includes the
[26-page paper](analysis/om100-20261014/lean-compression-study.pdf), executed
notebook, statistical tables, reviewed inputs, benchmark cards, and the updated
compression explorer. Open the dashboard from this checkout with:

```sh
python3 -m http.server 62775 --bind 127.0.0.1 \
  --directory analysis/om100-20261014/analysis-report/dist
```

Visit <http://127.0.0.1:62775/?view=1#compression-explorer>, or download the
[self-contained offline report](analysis/om100-20261014/compression-explorer-offline.html).
The explorer shows exact per-output measurements, per-question ranks, and
rankings aggregated over the same shared questions for each selected model.

Full raw experiment responses, private transport logs, and proof graphs remain
outside Git. If you have the original experiment report bundle locally, open it with:

```sh
python3 reporting/serve_comparison.py \
  --suite runs/om100-max-suite-high-20261014
```

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m unittest discover -s reporting -p 'test*.py'
```

Lean and Docker integration checks require a configured runtime; unavailable prerequisites are reported as skips. No model API calls are needed for these tests.

## Documentation

- [Dataset and task preparation](docs/openai-math-100.md)
- [Lean checker and extraction](docs/lean-engine.md)
- [Container isolation and runtime checks](docs/runtime-validation.md)
- [Statistical analysis](docs/analysis.md)
- [Interactive comparisons and exports](reporting/README.md)
- [API cost tracking](docs/cost-tracking.md)
