# Eight-model cohort

The next selected dataset is the [OpenAI Math 100 collection](../../docs/openai-math-100.md).
These profiles retain the completed smoke run's Lean environment and legacy
dataset design. Their model IDs and high-effort generation preferences remain
the intended cohort, but the profiles must be adapted to the new validated
environment and protocol before running the selected 100 questions.

Selected from generally available text/code API releases on 2026-10-12. Each JSON file is a complete configuration based on `../pilot-ready.json`; pass it directly to `freeze --config` with the matching `--model` value.

| Provider | Model ID / configuration filename stem |
| --- | --- |
| OpenAI | `gpt-6.1-sol` |
| OpenAI | `gpt-6-sol` |
| OpenAI | `gpt-6-luna` |
| OpenAI | `gpt-6-astra` |
| Anthropic | `claude-haiku-5-5` |
| Anthropic | `claude-sonnet-5-5` |
| Anthropic | `claude-opus-5-5` |
| Anthropic | `claude-fable-5-1` |

All eight explicitly request **high** effort. OpenAI sends `reasoning.effort`; Anthropic sends `output_config.effort` with `thinking.type=adaptive`. Temperature is deliberately omitted. The existing 8,192-token reasoning-plus-answer cap, 180-second HTTP timeout, four samples per goal/condition, prompts, schedule seed, and Lean validation limits are shared. Provider tokenizers and effort policies differ: this matches the requested setting and cap, not actual compute. Inspect truncation rates in the smoke run before freezing the formal pilot.

These are the documented versioned API identifiers; do not append guessed date suffixes. The harness records and checks the model identifier returned by each provider. Availability to a particular account is established only by a successful request. Configuration readiness does not complete the dataset review or production gates.

Each profile now has `cost_tracking_required: true` and an embedded USD rate card
checked on October 13, 2026. New frozen runs retain that card and record token-cost
estimates per generation. OpenAI profiles request `service_tier: default` to match
standard pricing; the effective response tier is checked. Older frozen runs keep
their original settings. See [cost tracking](../../docs/cost-tracking.md).

## Credentials and a development smoke run

Use the **same terminal where the two keys were exported**. From the repository root, the launcher handles Python setup and chooses the existing `colima-lce-eval` Docker context unless you explicitly set `DOCKER_HOST` or `DOCKER_CONTEXT`. The `doctor` command displays presence booleans, never values:

```sh
./run-eval doctor
```

From any other directory, replace `./run-eval` with `/absolute/path/to/lean-compression-eval/run-eval`; relative file arguments still resolve from the repository.

For each model, use its matching config and a distinct run directory. For example, after checking the smoke references inside the pinned runtime as described in the main README:

```sh
./run-eval freeze --development \
  --tasks data/public/smoke.jsonl \
  --environment configs/environment.docker.json \
  --config configs/models/gpt-6.1-sol.json \
  --split pilot --model gpt-6.1-sol \
  --reference-checks work/reference-checks-pilot \
  --run runs/smoke-gpt-6.1-sol-high
```

Only `generate` makes paid model requests. The following sends at most one pending attempt, allowing account access and response handling to be checked first:

```sh
./run-eval generate --run runs/smoke-gpt-6.1-sol-high --limit 1
```

Repeat with the matching model, config, and run directory for each cohort member. Resume an existing frozen run with `generate`; do not freeze it again or edit its manifest. A full ten-goal smoke schedule is 120 generations per model, 960 across eight models. A development run does not qualify as the formal pilot required for the main experiment.

## Sources

- [OpenAI model releases](https://developers.openai.com/api/docs/changelog)
- [OpenAI high effort and sampling compatibility](https://developers.openai.com/api/docs/guides/deployment-checklist)
- [Anthropic model releases](https://platform.claude.com/docs/en/release-notes/overview)
- [Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic thinking and output limits](https://platform.claude.com/docs/en/build-with-claude/thinking)
- [Anthropic versioned identifiers](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
