# Native evaluator benchmark

The native evaluator reproduced the complete result for one real smoke
reference, but the observed speed difference was small: **22.656 seconds native
versus 23.771 seconds with `lean --run`**, or 1.049× speedup and approximately
4.7% less elapsed time. This single comparison ran while other reference workers
could be active. It does not establish a meaningful corpus-wide improvement.
The production/development runner was left unchanged, and the 175.6 MB native
binary was not added to the deliverable package.

## Compared work

The selected task was `smoke_an_01`, the affine real inequality reference. Both
implementations received exactly the same saved JSON request in `extract` mode,
which performs validity checking and exports the full weighted dependency graph.
Both used the pinned toolchain, identical `LEAN_PATH`, `LEAN_SYSROOT`, locale,
working directory, no-network macOS sandbox, heartbeat setting, and 60-second
wall-time allocation. The benchmark did not change the Lean source or the task.

| Evidence | Value |
| --- | --- |
| Lean version | `leanprover/lean4:v4.34.0-rc1` |
| Lean engine (`Evaluator.lean`) SHA-256 | `83da0074473ce16de743f16eba380b4b78615e46718627eea2a210d39fc8d3f7` |
| Environment hash | `4ce9bdfa4cfb0d3dc443acdf251867697ab3342d9f6743677f8331bba1a3666f` |
| Request hash | `509cb62eeaef1acb33140ba744b38fdd0de7cc553a93b55f70411afa0ff0652d` |
| Native executable SHA-256 | `5d9a6ba30ce64cee84799b66ef358708869d490202b86ef4ea2b08ccbcd4c5e1` |
| Native executable size | 175,586,800 bytes |
| Exported graph nodes | 7,430 |
| Validity / metrics | Success / available, in both runs |
| Complete parsed result equality | Exact equality |
| Recomputed metric equality | Exact equality |

Equality includes the source-token spans, trusted type and telescope, checked
axioms, generated declarations, weighted graph node records, and diagnostics.
Both graphs also produced identical metric dictionaries when scored by the
Python metric engine. This is evidence for this positive reference case only;
it is not a replacement for the negative validity fixtures or broad performance
testing before changing the execution profile.

## Build and interface

The experiment copied the unchanged evaluator to the task scratch directory
`work/native-evaluator/Evaluator.lean`, then used the pinned toolchain's absolute
`lean` and `leanc` executables:

```text
lean -c work/native-evaluator/Evaluator.c work/native-evaluator/Evaluator.lean
leanc -O3 -rdynamic -o work/native-evaluator/evaluator-interpreter work/native-evaluator/Evaluator.c
```

The executable interface is:

```text
evaluator-interpreter REQUEST.json RESULT.json
```

`PATH` must begin with the pinned toolchain's `bin` directory, `LEAN_SYSROOT`
must select that same toolchain, and `LEAN_PATH` must be populated from the
frozen environment manifest. The benchmark kept the existing sandbox around
both command forms; compiling a native executable does not remove the need to
isolate tactic execution.

The C-generation step took 1.392 seconds and the working native link took
2.489 seconds in this run. An initial build without `-rdynamic` was unusable:
Mathlib tactic execution failed to resolve the native interpreter symbol for
`Array.emptyWithCapacity`. The pinned Lake source documents `-rdynamic` as the
non-Windows executable flag used by `supportInterpreter`. That initial failed
run is excluded from the performance comparison and preserved in scratch logs.

Local scratch evidence remains in `work/native-evaluator/`: `compile.json`,
`compile-interpreter.json`, `request.json`, `sandbox.sb`, `lean_run.json`,
`native-interpreter.json`, and `benchmark-interpreter.json`. These intermediates
are outside the user-facing output package. No additional model generations or
provider calls were made for this experiment.
