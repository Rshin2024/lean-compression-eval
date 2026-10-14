# Prepared OpenAI Math 100 snapshot

This versioned snapshot contains the exact ready inputs reviewed for the eight-model, 9,600-attempt evaluation. All 100 standalone targets, official reference checks, premise audits and the root final review have passed. It is separate from the historical selection adoption under `data/question_sets/openai_math_100_v1`.

`private/tasks.jsonl` contains compiler contexts and model-visible premise interfaces; `private/references.jsonl` contains reference proofs. The directory name is an organizational boundary, not encryption. Do not feed entire task/reference files to a model. The harness constructs messages from the frozen prompt fields only. Source proof bodies are trusted checker inputs, not model prompts.

`root-review.json` is copied unchanged. `index.json` maps all100 IDs, 19 families, mathematical input hashes and independently reported reference-anchor hashes. `audits/` preserves the stage-specific metadata unchanged, so earlier audit files can legitimately retain generation-disabled or pending-later-review statements. The final root review binds these stages together. `manifest.json` binds the packaged files, eight frozen configurations and run identities.

Frozen environments/configurations are in `configs/om100/` relative to repository root. The eight local runs are `runs/om100-<model>-high/`; their manifests and later responses remain in ignored run storage. `audits/frozen-run-plan.json` is the proposed freeze result, not the live launch marker. Packaging never authorizes or starts model calls. The coordinator separately releases `work/full-eval-100/launch_ready.json` after checking the frozen runs.

The roughly3.1GB official reference-anchor graphs are deliberately omitted. Their independently recorded hashes and policy are retained, but this smaller package alone cannot replace the full anchor evidence when freezing another run. Keep the original anchors or independently regenerate them with the pinned source/runtime and re-audit the resulting evidence. The Docker image and provider responses are also not included.

Reference-informed premise retrieval and the exploratory/family limitations remain disclosed in the final review and main documentation. No claim of certified-unseen training data is made.
