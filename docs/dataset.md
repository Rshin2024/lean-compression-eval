# Dataset collection and readiness

The collection contains **250 candidate tasks**, with the proposed numerical quotas: 50 pilot tasks and 200 main tasks, 10 pilot and 40 main tasks in each of five domains. Each main-domain group contains 30 reconstructed library tasks and 10 authored tasks. The pilot contains 40 library tasks and 10 authored smoke tasks. Numerical coverage does not establish that this is a frozen, representative benchmark.

`data/public/tasks.jsonl` is the combined task manifest. The public component files are `smoke.jsonl` (10), `library_candidates.jsonl` (190), and `fresh_main.jsonl` (50). References are stored separately in `data/private/references.jsonl` and the corresponding component reference files. **Do not send private reference files or whole task objects to a model.** A prompt should use only the approved imports, fixed premise excerpt, explicit target and chosen condition. The trusted `context` is evaluator input, not automatically approved model-visible input.

## What has actually been checked

All 250 reference proofs have passed Lean checking using `leanprover/lean4:v4.34.0-rc1` and Mathlib commit `274ed6d67d9b226c557813b9ce437574cccdce11`. All 250 targets have explicit source signatures. The 190 reconstructed library references passed isolated engine validation, axiom/dependency auditing and kernel replay during signature materialization; evidence is in `data/private/library_signature_freeze/`. The 60 authored references first passed ordinary compilation; `smoke_reference_compile.json`, `amended_smoke_reference_compile.json` and `fresh_main_reference_compile.json` retain that evidence. Full metric-closure verification is accounted for separately from correctness.

The initial source-compilation triage is retained in `data/private/library_reference_compile.jsonl`, including failures from subsequently corrected prefix reconstruction. **Use the later per-task engine records and public audit fields for current status**, not the earlier triage pass count. The final library contexts assert that the original target is absent before the evaluator declaration. A successful check establishes absence of the listed original declaration in that reconstructed environment; it does not establish absence of every equivalent theorem, unlisted alias or pretrained answer.

No candidate is declared ready for confirmatory evaluation merely because it compiles. `readiness_errors()` requires affirmative fields for reference verification, explicit signature freezing, answer-access auditing, family review and measurable metric closure. Pending fields intentionally block this gate. Model generations have not been used to choose or repair these tasks.

## Reconstructed library tasks

Each record identifies its Mathlib source module, declaration, line, full source hash and reconstruction policy. The supplied imports are the imports from that source file. `import_specs` preserves their order and the original `is_meta`, `is_exported` and `import_all` flags; the engine verifies that their module order matches `imports`. The target module itself is not imported. The trusted context reconstructs source preceding the target, including previous declarations, local notation, public sections and open namespaces. Module headers and imports move to the evaluator's import mechanism; target attributes and target documentation are excluded. The target is renamed into the evaluator namespace. Source excerpts retain Mathlib copyright notices; its license is included as `data/public/LICENSE-Mathlib`.

This is a source-based reconstruction, not permission to import the completed target module. The `forbidden_declarations` initially contains the resolved original target. It must be extended when alias and dependency audits identify additional forbidden names. A transitive dependency audit should enforce this set for generated proofs. The original target module's later declarations are absent by construction.

The original source signatures could depend on surrounding section variables. These implicit section parameters have now been materialized from Lean's trusted telescope, preserving the original written binders and proposition. A separate trusted comparison process elaborates the original and materialized declarations, aligns their universe parameter lists, and calls `Kernel.isDefEqGuarded` on the types. The original reference proof is then replayed under the materialized signature without comparison helpers. The comparison source and results are private evidence. This avoids relying on pretty-printed type strings or hygiene-sensitive expression hashes as equality checks.

`premise_context` contains the complete predecessor source plus required universe declarations, excluding evaluator target-absence assertions. This gives models coherent local definitions, notation and scopes. It includes earlier library proofs but never the target's reference proof. The largest trusted library prefix is under 7 KB. The same fixed bytes are used for every condition. The earlier excerpt policy is preserved in the amendment log and `library-before-premise-context.jsonl`; this prompt-only revision did not change runtime imports, signatures, contexts or proof bodies.

## Authored tasks and novelty

The 60 authored statements combine assumptions or ask related consequences, with a short rationale in each provenance record. They are not generated by renaming a theorem or substituting a sequence of constants. They include algebraic consequences, divisibility and remainder combinations, real inequalities, set and logic constructions, linear maps and finite sums/products.

“Fresh” here means authored for this collection. It is **not** a claim that an equivalent statement is absent from Mathlib, public mathematics or model training data. Exact/equivalent-statement screening remains part of the answer-access audit. Many tasks are easy controls and use existing automation effectively.

## Sampling and family limitations

The library collection starts from early suitable declarations in 20 selected source modules. It is not a random sample of Mathlib and does not yet meet a defensible depth/length stratification. Source-length strata and authored-task difficulty labels are provisional; measured reference depth and length must inform the final strata before model outcomes are available.

Library families initially group source modules; authored families identify the construction used. Independent review led to replacement of two smoke problems and one main problem with construction overlap across pilot/main, replacement of two library aliases whose equivalent theorem was already available, and merging of related main-task families. The new conditional-monoid smoke problem is grouped with its related pilot library family. These changes precede model generation and are recorded with prior tasks/proofs in `data/private/dataset_amendments.jsonl`.

Module separation alone cannot establish complete separation of related constructions. Broader cross-module and authored/library review remains pending, so `family_reviewed` remains false. One merged main family crosses domains; domain-stratified family resampling is appropriate only when the final families are nested within domains. Report the eventual number of independent families, not only 250 task rows.

The pilot may guide runtime, extraction and precision decisions. Do not tune task replacements or family groupings using main-model outcomes. Archive every preprocessing correction and pre-evaluation exclusion with its reason.

## Python interface

`load_tasks(path)` reads JSONL or a JSON task array (also accepting an object with a `tasks` array). `validate_manifest(tasks, enforce_quotas=False)` returns a list of structural errors; it does not raise on ordinary validation failures or assert Lean correctness. With `enforce_quotas=True`, it checks the complete 50/200, domain and main-origin quotas.

`validate_references(tasks, references)` checks the one-to-one private reference mapping. `readiness_errors(tasks)` returns unresolved readiness gates. Hash checks cover trusted context, premise excerpt, binders and proposition. Recompute these hashes only after an intentional, recorded revision.

The structural validator detects duplicate task IDs, unsafe target/module names, malformed or reordered import descriptors, cross-split family IDs, accidental public reference fields, direct import of a target module, missing/incorrect quota counts, exact source signature duplicates and stale content hashes. It cannot determine logical equivalence, prove absence from pretraining or replace the Lean validity gate.

## Before the main run

1. Complete the final versioned metric-closure coverage checks and retain all correctness/availability outcomes.
2. Broaden the known-alias/equivalent-target and construction-family audits beyond the documented fixes.
3. Establish reference length/depth strata; the current easy-heavy collection is not a balanced difficulty sample.
4. Review precision and resource use in the pilot.
5. Freeze the manifest, references, policies, context bytes and hashes before generating main-test outputs.

The current files support engineering smoke tests and continued dataset auditing. A structural 250-task count is not a substitute for these gates.
