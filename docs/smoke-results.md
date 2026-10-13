# Eight-model development smoke results

Completed 2026-10-13T08:10:04.658369+00:00. All 960 scheduled model responses were generated once and checked in the pinned Lean environment. No formal pilot or main study has been run.

The smoke comprises 10 goals, three prompt conditions and four independent samples per goal/condition. All models requested high effort with an 8,192-token reasoning-plus-answer ceiling and omitted temperature.

| Model | Valid / 120 | Standard / 40 | Compression / 40 | Concision / 40 | Metrics available |
|---|---:|---:|---:|---:|---:|
| claude-fable-5-1 | 96 | 35 | 30 | 31 | 93 |
| claude-haiku-5-5 | 3 | 1 | 1 | 1 | 3 |
| claude-opus-5-5 | 111 | 35 | 37 | 39 | 110 |
| claude-sonnet-5-5 | 39 | 26 | 7 | 6 | 39 |
| gpt-6-astra | 96 | 32 | 27 | 37 | 94 |
| gpt-6-luna | 81 | 29 | 23 | 29 | 80 |
| gpt-6-sol | 95 | 33 | 32 | 30 | 95 |
| gpt-6.1-sol | 88 | 30 | 26 | 32 | 88 |

There were 609 valid proofs and 602 with complete compression metrics. Seven extractor timeouts leave validity intact while metrics remain unavailable. Failures comprised 217 parsing failures, 103 elaboration failures, 29 verification timeouts, one truncated response and one verification resource limit.

Parsing failures were concentrated in Haiku (117) and Sonnet (77). The frozen identity response adapter retains wrappers and prose, so validity measures format compliance as well as mathematical proof correctness. These small-sample smoke rates should not be interpreted as a mathematical-capability ranking. No responses were repaired or replaced.

All 960 provider captures and all record identities reconcile. Each of the 609 valid proofs has successful kernel replay and a reference-matching type fingerprint. Eight historical setup incidents were resolved; no provider outcomes, checker incidents or in-flight model calls remain unresolved.

Reported usage totals: 286,480 input tokens, 275,404 output tokens and 214,487 reasoning tokens. Reasoning tokens are included in output usage where specified by the provider; do not add the two figures. All token fields have full coverage after recognizing Anthropic’s native thinking-token field.

The per-proof limits remained one CPU, 4 GiB memory, and separate 60-second validation/extraction stages. The dedicated local VM was increased to 12 CPUs and 28 GiB during the run at the user’s request. Completed records were preserved; unfinished checks resumed under identical proof limits. This allocation change matters when interpreting timing, not mathematical compression values.

The aggregate machine-readable results are in [live-smoke-summary.json](../examples/live-smoke-summary.json). Complete reports, CSVs, graphs and audit bundles are local under `runs/smoke-suite-high-20261012/`; `index.html` links all eight model reports. Raw responses and private audit material remain outside Git.

The reporting-only thinking-token normalization was applied after all proof verification finished. Existing v1 evaluations retain their original Python-package identity; use `analyze` to regenerate reports. Any future checker rerun uses a new evaluation version.

Dataset family, answer-access, difficulty/strata review, a formal pilot, and preregistration remain prerequisites for the main study.
