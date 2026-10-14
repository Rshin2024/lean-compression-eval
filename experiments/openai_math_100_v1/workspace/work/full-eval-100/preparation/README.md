# Source-scoped OM100 preparation

This directory prepares the user's selected 100 questions without running Lean or
calling a model. It does not make a question eligible for generation.

From the workspace root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 work/full-eval-100/preparation/extract_scoped_tasks.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s work/full-eval-100/preparation -p 'test_extract_scoped_tasks.py' -v
```

The script reads only the adopted, pinned selection, private reference declarations
and matching source files. It verifies full source and declaration hashes. It never
imports or executes the old collection script, curator prose, HTML, or Lean source.

## Outputs

- `draft_v1/private/scoped_tasks.jsonl`: all 100 complete compiler-context task drafts.
  **Use this file for trusted Lean signature materialization.** The exact original
  predecessor definitions and proofs are compiler inputs, not model prompts.
- `draft_v1/private/references.jsonl`: all 100 exact reference proof bodies, with hashes.
- `draft_v1/private/source_prefix/`: exact source prefixes preceding the selected target.
- `draft_v1/private/compiler_context/`: compiler prefixes with import/module commands
  removed, target documentation/attributes stripped, and upstream `autoImplicit=false`
  prepended. Target-local `omit ... in` and `open scoped ... in` wrappers remain.
- `draft_v1/public/questions.jsonl`: all 100 public draft interfaces. `context` is empty;
  `compiler_context_ref` identifies the private compiler input. This file is intentionally
  **not a runnable manifest**.
- `draft_v1/diagnostics.json`: one record per selected question, including failed
  extraction if any, omissions, remaining gates, and the fact that no Lean/model calls ran.

The IDs are `OM100-001` through `OM100-100`; target names are
`_root_.CompressionEval.OM100_001`, etc. All tasks retain `split=main`, their supplied
family and one of the four supplied selection groups. No questions are substituted.

## Scope and limits

The scanner masks nested comments, strings and quoted identifiers before recognizing
declaration delimiters. Target statement/proof separation is bound to the verified
source span. The original written binders are **not yet the fully materialized Lean
signature**: preceding section variables, instances, universes and include/omit commands
still require the trusted compiler/exporter.

Model-facing premises contain bounded, whole-block local interfaces. Theorem/lemma
proof bodies are removed. Computational definitions are included only when they contain
no tactic/proof blocks or structure-literal proof fields; other definitions expose their
headers. Interfaces containing proof terms in their types are omitted pending trusted
pretty-printing. Selection uses target-statement names and source position, never the
reference proof or curator hints. Local source parameters are retained as interface
annotations. Imported declaration interfaces still need export; 14 selected targets
have no preceding named declarations in their own source files.

Every audit flag remains false, and `model_generation_enabled` remains false. Required
next steps are trusted explicit-signature materialization, reference compilation and
independent replay, imported-interface/alias/answer-access auditing, the OAI-aware metric
boundary, matching runtime verification, and family/dependency review. Written proof
length is not a difficulty measurement. No successful build or absence of proof leakage
through imported semantic aliases is claimed by this lexical preparation step.
