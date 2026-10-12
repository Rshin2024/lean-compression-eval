# Lean validation and extraction engine

`lean/Evaluator.lean` is a tested source driver for the exact toolchain declared by the environment manifest. Launch it through the isolated Python runner. A manual trusted-fixture invocation is:

```text
<manifest lean_binary> --run lean/Evaluator.lean request.json result.json
```

`PATH` must start with the pinned toolchain's `bin` directory and `LEAN_PATH` must contain the manifest's compiled-library directories. Lean's `findSysroot` consults the runtime path. Mixing the global default Lean with the pinned binary can produce incompatible `.olean` headers. No project or dependency is modified by the driver.

## Interface

The request object contains `imports` (module names), optional `import_specs` descriptors, `target_name`, `binders`, `proposition`, `proof`, `context` (trusted commands, default empty), `forbidden_declarations`, `permitted_axioms`, `max_heartbeats`, and `mode`. Modes `validate` and `extract` are checked in separate fresh processes by the Python runner. `extract` repeats validation before computing the graph. Optional `expand_modules` marks compiled evaluator context modules for expansion. Optional `max_graph_nodes` bounds graph extraction (default 100,000). `verify_histogram_cache` requests a development comparison against uncached counting and should remain false for benchmark runs.

`import_specs` entries use `module`, `is_meta`, `is_exported`, and `import_all`, corresponding to Lean.Import. Missing Boolean fields default to false, true, and false respectively. Descriptor module order must equal `imports` exactly; mismatches and malformed flags are environment errors. When descriptors are absent, the original string-list behavior is unchanged. `resolved_imports` records the flags actually loaded. The reconstructed Real.Pow fixture preserves its `public meta import Mathlib.Data.Nat.NthRoot.Defs` header; meta imports control transitive IR loading.

The proof is parsed as one complete Lean `term`; any remaining non-comment text is a parse failure. Its syntax is inspected for executable metaprogramming forms before elaboration. The entire fixed theorem is also parsed to end of input. The trusted target is separately elaborated from identical binders and proposition in the pre-proof environment. Its temporary signature axiom is never visible to the candidate.

The `context` is elaborated once and its command state, open namespaces and scoped variables are retained. An original source prefix can therefore reconstruct a Mathlib task. Context is evaluator-owned trusted input; never fill it with a model response. A compiled-prefix module can instead be imported and listed in `expand_modules`.

Before elaboration, actual kernel-environment declaration Names are scanned against `forbidden_declarations` labels without reparsing those labels. Every entry must be absent from the trusted runtime environment. A present original theorem or alias fails as `forbidden_environment`, even if the submitted proof would not use it. The final dependency closure is independently checked against that set.

## Validity evidence

The engine checks the final target against the separately elaborated trusted type, rejects unresolved metavariables and traverses all dependencies in **all packages** for axioms and forbidden names. This validity traversal does not stop at the compression primitive boundary. `getUsedConstants` is used only for dependency reachability and kernel-replay ordering, never for weighted metric counts.

The default axiom allowlist is `propext`, `Classical.choice`, `Quot.sound`; the trusted run configuration may narrow or explicitly change that list. `sorryAx` is always rejected. Original `sorry` and `admit` syntax is rejected even if an unused local proof would otherwise disappear. Indirect use through a theorem or opaque value is detected by the transitive audit.

All root-reachable declarations created during candidate elaboration are replayed in dependency order from the pre-proof environment through `Environment.addDeclCore` with `doCheck := true`. The root is replayed using the independently elaborated original type. Generated definitions, theorems and opaque declarations are supported. Generated inductive declarations or recursive cycles in candidate auxiliaries produce a replay failure instead of silently bypassing checking. The actual `as_aux_lemma` fixture creates an auxiliary theorem and passes replay.

Success output includes `validity_status: success`, `kernel_replay: true`, checked `axioms`, the resolved `root`, `candidate_declarations`, and a proved-type fingerprint. Failures retain distinct statuses for syntax, elaboration, target mismatch, forbidden environment/dependency, axioms, and replay. Timeouts and resource failures are classified by the process runner.

`trusted_type_source`, `universe_parameters`, and `trusted_telescope` expose the elaborated signature for dataset preparation. The telescope contains named binders, binder visibility, each type in its preceding local context, and the final proposition. Reparse and verify any reconstructed signature; pretty-printed text is an aid, not a round-trip guarantee. Scoped variables are not automatically charged to source `W_S`, so production dataset preparation must materialize all intended explicit binders before freezing tasks.

## Token accounting

`source_tokens` contains the exact assembled declaration, UTF-8 byte spans, `W_S`, `W_B`, `W_full`, proof offset, and policy name `lean_original_leaf_spans_v1`. Counts come from original leaf atoms and identifier/literal spans, before elaboration or macro expansion. Repeated identical source spans are counted once. Whitespace, comments, and synthetic syntax do not count. The fixed theorem keyword, evaluator target name, signature/body separator, and declaration colon are excluded from `W_S`/`W_B` but retained in `W_full`.

Lean normalizes CRLF while parsing. The extractor maps normalized positions back to the original input's UTF-8 byte offsets; the original source is preserved. Isolated carriage returns remain parser errors. The fixtures cover qualified names, strings, numerals, Unicode notation, comments, renaming, and CRLF.

## Weighted graph

Each graph node includes `name`, `owner_module`, `primitive`, separate `signature` and optional `body` occurrence histograms, type/body diagnostic hashes, histogram measurement status, expression node counts, literal bytes, and null source counts for declarations without independently submitted source. The root's source costs are recorded once in `source_tokens`; generated auxiliaries never receive invented source costs.

Ownership comes from the environment's module index. Mathlib-owned declarations, current evaluator declarations, and explicitly listed evaluator modules are expanded. Other frozen modules are atomic boundary nodes. The distinguished `@Sort` is atomic. The result is **paper-aligned**, not a numerical reproduction of an author implementation.

Every occurrence of `.const` and `.sort` is counted, through applications, binders, lets, metadata and projections. Variable and literal occurrences add no primitive references; literal sizes and complete expression nodes are separate diagnostics. Each subtree's complete histogram is cached and merged at every use; repeated shared expressions retain their multiplicity. `ConstantInfo.value? (allowOpaque := true)` retrieves theorem and opaque bodies. Absent body remains JSON null; an existing body with no references remains `{}`.

Atomic boundary nodes have `histogram_status: atomic_boundary_not_expanded`, empty edge placeholders, null node/literal diagnostics, and an explicit `body_present` flag. Their empty placeholders are **not measured zero histograms**. Expanded nodes have `histogram_status: measured`; a measured absent body is distinguished by `body_present: false`. This avoids traversing irrelevant boundary bodies without misrepresenting missing data.

Graph traversal preserves actual Lean `Name` values (including numeric and private components), using deduplicated constant references only to discover graph nodes. Names are not reconstructed from display strings. Distinct names that collide under the exported text label cause an explicit extraction error.

The graph is the reachable closure of the root; `candidate_declarations` separately inventories all newly generated declarations, including unused auxiliaries. An unused auxiliary does not affect root recursive expansion; its syntax remains charged to the whole submitted proof source. Missing constants and missing module ownership are extraction errors. Graph cycles, zero-reference nonprimitive nodes, exact integer arithmetic and ratio availability are handled by the Python metric engine.

Expression debug strings and pretty-printed binder names can contain hygiene identifiers and are not suitable for comparing separately elaborated signatures. Dataset freezing should independently kernel-check definitional equality of the old and materialized signatures, then revalidate the private reference.

The diagnostic `type_hash`, `value_hash`, and `proved_type_fingerprint` are Lean's noncryptographic structural expression hashes. They are not collision-resistant content addresses. The persistent graph cache does not key only on those values: it uses SHA-256 over the complete environment identity, boundary policy, declaration name, diagnostic hashes, and complete weighted histograms. Conflicting histograms under the same frozen node identity are rejected. This cache reuses exact metric inputs; it does not establish expression equality or proof validity. The Python runner separately records SHA-256 of the complete exported graph, source, engine and environment.

## Isolation and limits

The engine's executable-syntax denylist is defense in depth; it is not a general sandbox or a proof of absence of all executable elaborator behavior. Ordinary Lean tactics execute trusted code. The production runner must enforce no network, a sanitized process environment, a read-only library and no evaluator-secret access. Do not run arbitrary model responses directly through the manual invocation above.

The engine source uses an `unsafe` entry point only for Lean's required `enableInitializersExecution` when loading trusted library extensions. Submitted proof values must still pass the independent kernel replay and axiom gate. Module source and compiled libraries are part of the trusted computing base.

Metric extraction errors never alter an already stored validation success. If the separate extraction process times out or fails, the runner leaves compression unavailable while retaining correctness success. The frontend's structured error output does not itself distinguish every resource condition; outer process budgets remain authoritative.

## Executed fixtures

`tests/test_lean_integration.py` runs real Lean proofs against the manifest-selected binary. It covers ordinary and meta import descriptors; internal numeric and non-roundtrippable guillemet declaration names without text round-tripping; source spans; invariance under comments, whitespace and local renaming; UTF-8 and CRLF positions; doubled references; theorem and opaque values; generated auxiliaries; cached/uncached histograms; direct and indirect admissions; custom axioms; wrong propositions; replacement declarations and multiple responses; imported forbidden declarations; restricted metaprogramming; and valid proofs with graph extraction errors.

The generic unit suite skips these fixtures only when the pinned local runtime is unavailable. The delivered local environment executed them; they are not mocked theorem-checking results.

## Development engine snapshots

`lean/versions/` retains exact earlier engine source for historical reference records. Reference evidence records its own engine hash; do not relabel old records as executions of the latest source. Version `3c093c02` supplied the first full 60-reference measured cohort. Subsequent engine changes strengthened forbidden-label absence checks and preserved explicit import modifiers. All 57 still-current references measured with that earlier engine were then actually rerun with `83da0074`; the current 250-reference evidence is uniform at the final source hash. A production protocol should freeze one current engine before target-model generation.

## Completed local reference preflight

All 250 current dataset reference proofs passed validation and have available compression metrics: 190 reconstructed library goals, 50 main-test authored goals, and 10 authored pilot smoke goals. All current reference checks use engine `83da0074`. Earlier authored reference records and their `3c093c02` source remain in historical archives; replacement records were published only after actual repeated validation and extraction succeeded. The 57 normalized records have unchanged W_S, W_B, U_S, U_B, D, T0 and I0 compared with their historical measurements. Summaries in `data/private/library-verified-final`, `fresh-verified-final`, and `smoke-verified-final` identify the current evidence, while `data/private/engine-normalization-83da` records the repeat executions. Both validation and extraction retained their separate 60-second caps. All 17 real-Lean integration tests passed against the current engine.

These checks establish executable references and metric coverage for dataset preparation. They are not model generations, pilot prompt comparisons, or evidence that the compression prompt improves proofs. Model-facing premise presentation amendments are linked through exact runtime-input hashes and retained snapshots; original execution records are preserved.
