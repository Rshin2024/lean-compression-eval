# OpenAI Math 100: preparation and execution source

This directory preserves the source used to prepare the selected 100-question
experiment. It is a source archive, not generated results or a launch command.
The SHA-256 and byte length of every copied file are in
[`source-manifest.json`](source-manifest.json). Files under `workspace/` are exact
copies of the listed workspace sources; their imports, paths and tested code
have not been rewritten for publication. [`pins.json`](pins.json) identifies the
upstream sources, compiler, Mathlib, measured runtime and evaluator snapshot.

At this documentation snapshot, all **100/100 standalone tasks have passed
materialization, reference validation and compression extraction**. All 100 final
uniform-policy reference checks have also passed. The model-visible premise/prompt
audit has also passed for all 100 questions. Model generation has not started at
this source-release snapshot. The controller requires a separate
reviewed `work/full-eval-100/launch_ready.json` bound to the frozen inputs; this
archive does not include or create that marker.

## Source groups

| Source | Purpose |
| --- | --- |
| `preparation/extract_scoped_tasks.py` and tests | Extract source-scoped compiler inputs from the pinned adopted questions, without executing curator prose |
| `materialize.py` | Recover explicit signatures and check the selected reference against each standalone target |
| `repair_materialization.py`, `finish_signature_edges.py` | Preserve source section binders, universes, instances and scope while repairing extraction failures |
| `premises/probe_layout.py`, `repair_let_layout.py`, `finalize_let_layout.py` | Preserve the dependent `let`-binder layout in selected target signatures, with compiler checks |
| `diagnose_signature.py`, `calibrate_reference_090.py` | Preparation diagnostics and the extraction-time calibration that informed the uniform 900-second limit |
| `assemble_materialized.py` | Account for all 100 IDs and bind each selected preparation record to unchanged reference bodies |
| `check_official_references.py` | Run the independent final reference checks under the final shared policy |
| `audit_final_inputs.py` | Independently bind the source, mathematical inputs, reference proofs and official anchor identities |
| `premises/PremiseExporter.lean`, exporter/auditor and tests | Export prior-environment interfaces, suppress proof bodies, audit merged model prompts |
| `freeze_full.py` | Freeze one immutable run per model after required checks and reviewed audit evidence |
| `run_full_evaluation.py` and tests | Keep API calls local; synchronize saved generations to isolated cloud checking without repeating recorded calls |
| `runtime/` | Build and verify the minimal OAI/Mathlib runtime, then remove package Lean sources and Git data |

The explicit copy allowlist is in [`refresh_snapshot.py`](refresh_snapshot.py).
It excludes API credentials, the credential carrier, SSH keys/configuration,
Azure access metadata and status logs, provider responses, private task/reference
records, Docker images/tars and upstream library source copies. It refuses a
source change during copying, unexpected files, oversized files and recognizable
credential/private-key content. This is a narrow packaging check, not a claim
that general secret detection can prove arbitrary text safe.

## Replay layout

The scripts resolve the repository from their original workspace-relative
location. Restore the archive under a new workspace with this shape:

```text
replay-workspace/
  outputs/lean-compression-eval/       # checkout of this repository
  work/full-eval-100/                 # contents of this archive's workspace/work/full-eval-100
```

For example, from a new workspace that already contains the checkout:

```sh
mkdir -p work/full-eval-100
cp -R outputs/lean-compression-eval/experiments/openai_math_100_v1/workspace/work/full-eval-100/. work/full-eval-100/
```

The archived `runtime/context` is a **manifest and pin snapshot only**, not a
complete Docker build context. Fetch the upstream `openai/math` commit in
`pins.json`, then run `runtime/prepare_context.py --selection ... --upstream ...
--output ...` into a new directory. It verifies source hashes and derives the
457-module OAI import closure for the 100 selected questions. Do not point it at
the already existing archived context directory.

`Dockerfile.build` takes that generated context. `Dockerfile.final` additionally
requires a `final-context` containing this repository's `src/` and
`runtime/run-lean`. `finish-runtime.sh` and `verify-final.sh` preserve the actual
build-host layout `/opt/lce-om100` and stage completion files. Read them before
replay and prepare that layout on the replacement build host. `verify_builder.py`
and `verify_runtime.py` check library artifacts, native runtime support and
source exclusion. The observed image is content-addressed, but this archive does
not promise a bit-identical rebuild: Ubuntu/apt inputs were not frozen to exact
package snapshots. A rebuilt image needs its own measured environment manifest
and newly frozen runs; never relabel it with the recorded image hash.

The original adopted selection and private source/reference inputs are separate
inputs. Their immutable adoption record remains historical, even after trusted
preparation produces a new `materialized_final_v1` bundle. The preparation scripts
emit that bundle and stage-specific evidence; they do not modify the original
adoption status. The archived configuration files are the current prelaunch
configuration snapshot. Original intermediate attempts preserve their own
`config.used.json` and source hashes outside this archive.

Replay proceeds through extraction, materialization/necessary scope repairs,
assembly, final reference checks, premise export/audit, reviewed final input
approval, and immutable freezing. The repair scripts retain the named stage
paths and specific task IDs from this preparation; they are inspectable history,
not a universal dependency scheduler. Consult their `--help` where available and
the archived preparation/premise READMEs for inputs. Do not rerun diagnostic or
repair scripts indiscriminately over an existing evidence directory.

The controller also retains deployment-specific paths, the `lce-om100-azure` SSH
alias, and a local SSH wrapper location. These access files are deliberately not
included. Configure equivalent access for a replacement host, and keep API keys
in local process environment variables. No cloud/API credentials are needed to
inspect this archive or run offline source tests. The local Lean exporter test
currently names the original host's Lean 4.34.1 binary; a replay copy requires an
equivalent binary path or an explicit local test-path adjustment.

The archived controller includes a post-freeze launch correction: remote
`subprocess.Popen` starts the cloud checker in a new session with inherited file
descriptors closed and all standard streams redirected. Shell-backgrounded
`nohup` had left the SSH channel open until its timeout even though the checker
was healthy. The local coordinator resumed against that existing locked checker.
No model calls had occurred when this launch issue was identified. The correction
changes process detachment only; the evaluator, prepared tasks, model settings
and frozen run manifests remain unchanged.

## Policy and scope

The authorized schedule is 100 questions × eight models × three conditions ×
four samples = **9,600 attempts**, with `high` effort and an 8,192-token output
cap. A common effort label is not an equal-compute guarantee across providers.
The preparation policy uses two-CPU/8-GiB containers, 180 seconds for validation,
900 seconds for extraction, and an OAI-aware expansion boundary. The final
frozen run manifests, not this prose, are authoritative for execution.

Premise retrieval is explicitly **reference-informed**: constants in the target
and reference seed retrieval, but only interfaces present before the target are
exported, with proof bodies suppressed. Context is limited uniformly to 56,000
UTF-8 bytes, leaving room under the 64,000-byte user-message cap. Literal
proof-absence checks and prior-environment membership do not establish the
absence of every logically equivalent answer alias. This is a conditioned
exploratory comparison of proof outputs, not an unaided theorem-discovery test,
the formal 250-question study, or proof of non-contamination.

Comparison reporting lives in [`../../reporting`](../../reporting/README.md).
It aggregates exact geometric-mean T0 ranks within shared questions, reports
correctness and coverage separately, records API cost estimates with missing
costs explicit, and keeps shared cloud costs separate. Generated results and
private proof evidence remain outside this source archive.

The reviewed runnable inputs are separately packaged in
[`data/prepared/openai_math_100_v1`](../../data/prepared/openai_math_100_v1/README.md),
with the exact ready task/reference bytes, final review, index and audit metadata.
Frozen environment/config files are under `configs/om100/`. The local
`runs/om100-<model>-high/` directories remain authoritative for frozen execution;
the large reference-anchor graphs are retained outside the smaller repository
package. `package_prepared.py` creates this separate snapshot from an already
completed freeze, binds it by SHA-256 and adds a `prepared_snapshot` pointer to
the active configuration without changing the historical selection-adoption
fields. It does not read anchor graphs or release the live launch marker.

## Source checks

From the restored workspace, offline extraction and controller tests can run
without model calls:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s work/full-eval-100/preparation -p 'test_extract_scoped_tasks.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 work/full-eval-100/materialize.py --self-test
PYTHONDONTWRITEBYTECODE=1 python3 work/full-eval-100/test_controller.py
```

On the original preparation workspace, maintainers can compare every archived
file against its explicit source and the recorded manifest without copying:

```sh
python3 outputs/lean-compression-eval/experiments/openai_math_100_v1/refresh_snapshot.py --workspace . --verify-only
```

Refreshing without `--verify-only` is a deliberate source-archive operation; it
does not build Lean, run tests, launch generation or alter cloud resources.
