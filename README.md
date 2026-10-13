# Mathematical compression in Lean

A working evaluation harness for the attached plan: give the same Lean target to three prompts—ordinary correctness, mathematical compression, and source concision—and measure correctness independently from compression.

This is a **paper-aligned implementation**, not an exact reproduction of the authors' code. The experiment measures submitted proof representations under one library. A larger expansion/source ratio is not, by itself, better reasoning.

## Current status

The package includes the Lean checker/extractor, exact metric engine, immutable run scheduler, OpenAI Responses adapter, offline response import, environment hashing, statistical reports, and blinded proof-audit exports. The candidate dataset contains 50 pilot and 200 main goals, including the requested 150 library/50 fresh main split across five domains.

**The candidate collection is not a released, audited benchmark.** Task audit fields and the reference evidence distinguish compilation, independent verification, metric availability, explicit signatures, and unresolved family/answer-access review. The main experiment must remain unfrozen until those gates pass. No paid model generations have been made, and no model is selected.

Local reference checks with `configs/development.json` run the existing Lean/Mathlib installation through a macOS development sandbox. That profile blocks network access and restricts user/private file reads, but permits system IPC and does not enforce a CPU quota or memory ceiling. Paths containing control characters are rejected, because the profile cannot quote them. **It is for trusted reference fixtures only.** The direct real-Lean fixtures and the audit compile check listed under [Start here](#start-here) run `lean` without this sandbox. The CLI requires a container for live model generations and generated-proof evaluation. Production freezing additionally requires a content-addressed Docker image, environment hashes collected in that image, enforced resource limits, and completed dataset audits. The container recipe is included; use it with a running Docker daemon.

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

The real-Lean fixtures that run `lean` directly in `tests/test_lean_integration.py`, `tests/test_lean_metric_stress.py`, `tests/test_lean_engine_fixes.py`, `tests/test_heartbeats.py`, `tests/test_statement_auxiliaries.py` and `tests/test_round3.py`, the audit `target.lean` compile check in `tests/test_audit_minor.py`, and the sandboxed Lean checks in `tests/test_outcome_taxonomy.py`, `tests/test_docker_command.py`, `tests/test_baseline_portfolio.py`, `tests/test_baseline_tactics.py`, `tests/test_heartbeats.py`, `tests/test_review_followups.py`, `tests/test_statement_auxiliaries.py` and `tests/test_runner_remaining.py` read the pinned compiler and library paths from the environment manifest named by `LEAN_COMPRESSION_ENVIRONMENT` (default `configs/environment.local.json`; relative paths resolve from this directory). They skip when that manifest or its Lean binary is missing; set `REQUIRE_LEAN=1` to make that a failure. The sandboxed checks also skip off macOS. The opt-in container checks in `tests/test_docker_runtime.py` and `tests/test_runner_remaining.py` run Lean in an existing image instead and skip unless `LEAN_COMPRESSION_DOCKER_ENV` names the environment record of an image available to a running Docker daemon (`docs/runtime-validation.md`). `tests/test_environment_integrity.py` checks the committed environment records against the local installation without running Lean, and skips that check when the Lean binary that `configs/environment.local.json` names is missing. The other arithmetic, analysis, audit, dataset, and orchestration tests need no Lean.

The CLI reports invalid input or evidence as an error message without a traceback; set `LEAN_COMPRESSION_DEBUG=1` to print the traceback instead.

The current pin is Mathlib `274ed6d67d9b226c557813b9ce437574cccdce11`, whose declared toolchain is `leanprover/lean4:v4.34.0-rc1`. `configs/environment.local.json` records this machine's installed dependency locations; `configs/environment.hashed.json` additionally hashes source and compiled artifacts. To use a different checkout, regenerate the environment record rather than editing the hashes, and point the tests at it:

```sh
python3 -m lean_compression prepare-environment \
  --package-root /absolute/path/to/a/locked/lean/project \
  --output configs/environment.new.json
LEAN_COMPRESSION_ENVIRONMENT=configs/environment.new.json REQUIRE_LEAN=1 \
  python3 -m unittest discover -s tests -v
```

Commands that take `--environment` refuse a record whose `environment_hash` no longer matches its contents and, for host records, re-check the Lean binary, the toolchain shared libraries recorded by full-hash records, that the library directories exist, and the locked package revisions. `evaluate` re-verifies the run's embedded environment record before running Lean. `freeze`, `verify-references` and `baseline` require a record collected in Docker (`runtime_backend` `docker`) exactly when `validation.isolation` is `docker`, and then one whose `docker_image` is `validation.docker_image`; an absent `isolation` counts as `docker`, the runner's default. `freeze` also requires that image to be a content digest (`sha256:...` or `name@sha256:...`). Schema 2 records hash every library file Lean may load, including `.ir`/`.ir.sig`, except `.trace`/`.hash`/`.ilean`/`.a`. The committed schema 1 records stay valid; `environment.hashed.json` prints a note that its compiled hash omits that IR.

## Dataset readiness

`validate-dataset --references` takes the private references JSONL; it also lists, among the readiness errors, any task whose reference proof is copyable from its own prompt (answer access): its whitespace-normalized text occurs there with at least 15 characters, or 9-14 characters unless it is a generic shape (a literal reflexivity proof, one bare tactic, or a name the target binds), or a premise-context proof component (a declaration value, or a constructor component, field value, fun body, tactic, tactic term or rewrite rule inside one) copies near-verbatim a reference of at least 9 characters that is not a generic shape: at most 3 token edits, or one per 6 reference tokens if that is more (tokens are runs without whitespace, brackets, commas or semicolons, and a swap of two adjacent tokens is one edit), changing at most a third of its name components, rounded. `readiness` instead reads verify-references output directories; its `answer_access_screen` lists these tasks as `flagged_task_ids`, `short_exact_match_task_ids` and `near_verbatim_task_ids` (the closest match is in `near_verbatim_matches`), which block its gate, and generic 9-14-character matches as `generic_short_exact_match_task_ids`, which do not. This command rebuilds the dataset readiness report from the committed evidence without Lean or model calls:

```sh
python3 -m lean_compression readiness --tasks data/public/tasks.jsonl \
  --references data/private/library-verified-final --references data/private/fresh-verified-final \
  --references data/private/smoke-verified-final \
  --original-tasks data/private/library-before-premise-context.jsonl \
  --original-tasks data/private/smoke-before-audit-metadata.jsonl \
  --config configs/development.json --output work/dataset-readiness
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

Each reference has two separate fresh processes: verification, then extraction. The output retains kernel replay evidence, axiom audit, original source token spans, weighted dependencies, resource evidence, and exact integer measurements. The command resumes already written records; a changed checker, configuration or metric code requires a new output directory. Its `policy.json` records the engine, the metric code, and a digest of the settings that affect checking and measurement (`validation`, `extraction`, `environment`, `metric_profile`). Runs are matched on that digest, so later generation or analysis settings do not invalidate the checks. Checker failures in either process go to `incidents.jsonl` instead of a record, so a rerun retries those tasks. References are private files and are never included in generation prompts.

Build a reusable imported-declaration index from those verified closures:

```sh
python3 -m lean_compression index-closures \
  --references work/reference-checks --environment configs/environment.hashed.json \
  --config configs/development.json --output work/environment-index
```

The index covers observed closures, not every Mathlib declaration. Unknown dependencies remain errors; they are never guessed to be primitive. Candidate-owned declarations stay outside the shared imported-node cache.

## Parallel workers

`verify-references`, `baseline` and `evaluate` can share one output directory among several processes on one host. Start the same command several times in the background, then wait for all of them:

```sh
for worker in 1 2 3; do
  python3 -m lean_compression verify-references \
    --tasks data/public/smoke.jsonl \
    --references data/private/smoke_references.jsonl \
    --environment configs/environment.hashed.json \
    --config configs/development.json \
    --output work/reference-checks &
done
wait
```

Before checking a task or slot, a worker creates a claim beside its record (`<name>.claim` for `<name>.json`) and holds an exclusive lock (`flock`) on it until it is done. The claim records the claim protocol of the harness code (`claim_protocol`, currently 2) and names the worker's process ID, host, start time and command for people; only the lock makes it live. Other workers skip claimed and recorded tasks, so no two workers check a task at once and a recorded task is not checked again; a checker incident still leaves its task pending. Every worker of a directory must run the same harness code: the `policy.json` of a `verify-references` or `baseline` directory and of an evaluation version records `claim_protocol` and the code's `evaluator_source_hash`, and a worker of other code is refused before it claims anything, as is any worker on a directory or version that code without a claim protocol started. Finish such a directory with the code that started it, or check into a new directory or version. Each worker runs one Lean process at a time. Choose a worker count that keeps every check's CPU and memory allocation: a check that overruns its wall-clock limit is recorded as timed out, not retried. Each worker prints its own summary; `--limit`, `new_checks`/`new_evaluations` and `checker_incidents` count only that worker's checks.

Rerunning the same command, with any number of workers, resumes after a crash, kill or interrupt: written records are skipped, a claim is taken over once no process holds its lock, and a claim left beside a written record is removed once no process holds it. `evaluate` logs, once, the `attempt_evaluated` event of a record whose worker was killed before logging it, and the next worker to hold a record's claim deletes the temporary files (`.<name>.json.` and eight characters) a killed worker left for that record. The kernel drops a lock when the last process holding it exits; a killed worker that its parent has not reaped yet holds none. A worker's Lean check inherits the lock (in Docker mode the `docker run` client, otherwise the sandboxed `lean` process), so the claim of a killed worker stays live while its check runs, and reruns leave that task pending until the check exits. That check runs on without its wall-clock limit until Lean finishes or reaches its CPU-time limit, adding to the other workers' CPU and memory load. To stop it sooner, `docker rm -f` the worker's containers, whose names start with `lean-compression-<worker pid>-`, or in native mode kill its `lean` process, whose parent is then process 1. An interrupted worker (Ctrl-C or `kill -INT`) stops its own check before it exits: it kills the check and, in Docker mode, force-removes its container, as described in `docs/runtime-validation.md`, then leaves its claim to the lock, which is then free. The loop above starts its workers in the background: run from a script (a non-interactive shell) they ignore SIGINT, so `kill -INT` does nothing, and in an interactive shell Ctrl-C reaches only the foreground job. Stop background workers with `kill` (SIGTERM) and handle them as killed workers, as above. Never kill only a `docker run` client: its container would run on without the claim. `lsof` on a claim lists the processes that hold it; stop those instead of deleting a claim by hand. Run every worker for a directory on one host, with the directory on a local filesystem: workers on different hosts may not see each other's locks.

Two kinds of file keep a task or slot from every worker, whatever any lock, until an operator deletes them; each command's summary lists any in its directory as `kept_claims`. A container whose removal is unconfirmed may still run: the container of a checker incident whose `validation.failure_reason` or `extraction.failure_reason` is `container_cleanup_unconfirmed`, or of an interrupted attempt whose error note says so. Its worker then writes `<name>.retained` beside the claim, naming the container, the removal error, the kept scratch directory and the claim, and stops with an error if it cannot. A checker incident names that file in `claims_retained` of its validation or extraction `resources`, an interrupted attempt in its error note. Once the container is gone from the daemon that ran it (`docker rm -f <container>` exits 0 or reports no such container, or `docker ps -a --filter name=<container>` lists nothing), delete the scratch directory and then `<name>.retained`; the next run checks the task or slot again. A claim without this code's `claim_protocol`, or one that cannot be read, may belong to a worker of other code that holds no lock, so it is never taken over or removed: delete it once the process it names has exited.

## Automation baseline

`baseline` runs the frozen tactic portfolio once per task through the same checker and writes `trusted_tactic_baseline` records. `tactic_portfolio_tactics` lists its alternatives in order, and each is checked as its own proof `by <tactic>` until one succeeds. The alternatives share the `tactic_portfolio_total_wall_seconds` budget. Each check gets what the earlier checks' elapsed times, process start-up included, left of it, and none starts once it is spent. An alternative that cannot parse (such as `aesop` under imports without Aesop), is rejected, runs out of heartbeats or hits a resource limit leaves the rest of the budget to the next. One that reaches its wall-clock limit has spent the budget, so no later alternative runs; the frozen order therefore matters. `policy.json` records the budget as `portfolio_validation_wall_seconds` and the alternatives as `portfolio_tactics`. A record is the deciding check (the success, or else the last alternative attempted), and its `portfolio_alternatives` give each attempted alternative's tactic, validity status, elapsed seconds and wall-clock limit, with the evidence of its check: `failure_reason`, `error`, `process_returncode`, the last 4096 characters of its stderr (`stderr_tail`) and its `resources`. A checker incident in any alternative leaves the task pending; a rerun checks its whole portfolio again. Metric extraction of the proof that succeeded keeps `extraction.wall_seconds`, as for references and model proofs. A configuration with only the older `tactic_portfolio` string still has that string checked as one proof under the whole budget, so runs frozen with it keep a matching baseline, which `analyze` labels `legacy_single_proof`; there one tactic unknown under a task's imports fails every alternative. `freeze`, `baseline` and `analyze --baseline` apply one check to the portfolio, so a run is never frozen with a configuration no baseline could match: they refuse a configuration that names both keys (even with a null value), a malformed list, no portfolio, or no positive budget. Give `baseline` the task file, environment record, and configuration that froze the run you will analyze; for the pilot development run below:

```sh
python3 -m lean_compression baseline --tasks data/public/smoke.jsonl \
  --environment configs/environment.docker.json --config configs/pilot-ready.json \
  --output work/baseline-pilot
```

Then add `--baseline work/baseline-pilot` to `analyze`. The report adds portfolio solve rates overall and by domain, the tasks each alternative solved, per-task outcomes with every attempted alternative, and each condition's success and compression on portfolio-solved versus unsolved tasks (also in `automation_baseline.csv`). This is a descriptive anchor, not a preregistered decision quantity. A baseline from another environment, configuration, or checker engine, without that recorded budget, or with mismatched records, is refused. Tasks without a record stay visible and mark the section incomplete. Without `--baseline`, `analysis.json` records that none was supplied.

## Choose a model later

`configs/pilot.json` intentionally leaves `generation.model_snapshot` and `reasoning_setting` unresolved. Set a supported pinned model, explicitly select its reasoning/sampling settings, and set `snapshot_pinned` and `settings_resolved` only when those claims are supported. Some models do not accept `temperature`; set it to `null` to omit it deliberately. The adapter never retries a bad request with changed settings. OpenAI's output limit includes reasoning tokens, and reported reasoning usage is retained.

Configure `OPENAI_API_KEY` in your shell or secret manager. Do not put it in project files. The checker process never inherits API credentials. A Codex subscription is not treated as API access.

For a live pilot development run with a small task manifest, first create the container runtime below and save its image/settings as `configs/pilot-ready.json`. Attainment anchors must be checked in the run's environment and measurement settings, and host checks are not interchangeable with container ones. A development run frozen without `--reference-checks` binds no anchors; unlike production, a bound development anchor may be verified but unmeasurable. Check the references again inside the runtime, into their own directory, and bind them to the run when freezing. `freeze` refuses checks made in another environment or with other measurement settings, and it refuses an incomplete set: every task needs a verified record with its checked proof, so rerun `verify-references` until it reports no checker incidents before freezing. A rerun keeps existing records, so a reference proof that failed (or, in production, cannot be measured) must be corrected or its task left out, and the references checked into a new directory:

```sh
python3 -m lean_compression verify-references \
  --tasks data/public/smoke.jsonl \
  --references data/private/smoke_references.jsonl \
  --environment configs/environment.docker.json \
  --config configs/pilot-ready.json \
  --output work/reference-checks-pilot
python3 -m lean_compression freeze --development \
  --tasks data/public/smoke.jsonl --environment configs/environment.docker.json \
  --config configs/pilot-ready.json --split pilot --model YOUR_PINNED_MODEL \
  --reference-checks work/reference-checks-pilot --run runs/pilot-development
python3 -m lean_compression export-prompts \
  --run runs/pilot-development --output work/prompts.json
```

`freeze` records the SHA-256 of its `--engine` (default `lean/Evaluator.lean`, as for `evaluate`) as the manifest's `engine_hash`, and `evaluate` refuses any other engine except through the re-check described under "Verify, report, and reproduce"; with `--reference-checks`, `--engine` must be the engine that made them. A manifest frozen before this field existed keeps its earlier engine checks. The manifest's `evaluator_source_hash` identifies only the Python package (a digest of `src/lean_compression/*.py`), not the Lean engine. `freeze` also refuses a `generation.response_adapter` that `evaluate` could not apply.

Only this next command makes model API calls:

```sh
python3 -m lean_compression generate --run runs/pilot-development --limit 120
```

It starts a fresh request for each slot with no tools, prior responses, reference proofs, or validator feedback. Raw messages, responses, usage, provider IDs, timestamps, transport retries, and refusal/truncation statuses are retained. Model-version mismatches cannot be silently pooled. An ambiguous lost response retains its in-flight marker until `reconcile` (below) resolves it, avoiding an automatic second completion. The body of every non-error HTTP response is first read in full and appended to the run's `captures/<attempt_id>.jsonl` as base64, with its SHA-256 and transport attempt; its `transport.jsonl` event, and any generation recorded from it, name that capture. Only a `completed` or `incomplete` response from the pinned model whose own output or refusal text holds a value a stored record cannot (such as a lone surrogate) is recorded as `malformed_response`, a model-attempt failure whose `raw_response` is the body text with undecodable bytes escaped, kept with the response's ID, model, status, usage and `incomplete_details`; the command then stops so the provider can be checked, and rerunning `generate` continues with the next slot. A body that is not a JSON object, or a `completed` or `incomplete` response that cannot otherwise be read or stored as received (an unexpected shape, NaN or a lone surrogate outside the model's text, or such text from another model), records nothing: its `outcome_unknown` event names the capture and, as for a lost response, the in-flight marker is kept for reconciliation and the command stops. HTTP errors, connections that fail before the request is sent, and provider `failed` responses, whatever their output holds, are not model outcomes; nor is a response with another status that cannot be read or stored as received. Their `no_model_outcome` event names the capture of the body it was read from (`capture`, null when no response body was captured, as for an HTTP error) and holds the response itself when a record can (`provider_response`, null otherwise). Transient ones are retried under the frozen policy, honoring `Retry-After` (seconds or an HTTP date) up to `retry.max_retry_after_seconds` (60 in the shipped configs, and when a config leaves it out). After that the error stays in `transport.jsonl`, nothing is recorded for the slot, and the command stops; rerunning `generate` retries the same attempt ID. The exception is a provider rejection of one prompt itself (error code `invalid_prompt`, in an HTTP 400 or a `failed` response, whatever else that response holds). It is not a model refusal and does not stop the command: it is logged as `no_model_outcome` with `prompt_rejected: true` and counted in `prompt_rejections`, which count toward `--limit`, and nothing is recorded. The samples of a task and condition share one prompt, so the rejection is remembered per prompt hash from `transport.jsonl`: none of that prompt's slots is sent again, in this or any later `generate`, until a generation of one of them is recorded after the rejection, and each one passed over is counted in `skipped_rejected_prompt_slots`. They all stay pending, so the run stays incomplete, and `analyze` counts them as provider outages (`prompt_rejected_attempts`). `generate --retry-rejected` sends them again after the other pending slots, the prompt rejected longest ago first, so a retry that `--limit` or the stop below cuts short moves on to the others next time; a prompt rejected again in that command still gets only one request. Rejections of different prompts one after another are systemic, as when shared prompt text is flagged: after 3 with no generation between them (`retry.max_consecutive_prompt_rejections`, frozen with the config), including prompts `--retry-rejected` sent again, the command stops with an error and exit status 2, and their slots stay pending. Without `--retry-rejected`, remembered prompts are never sent, so a rerun always moves on to slots not yet requested.

```sh
python3 -m lean_compression reconcile --run runs/pilot-development
python3 -m lean_compression inspect-capture --run runs/pilot-development --attempt ATTEMPT_ID
```

A kept in-flight marker (`inflight/<attempt_id>.json`) stops every later `generate` at its slot without a request. `reconcile` resolves such markers from the run's own evidence and never makes a model call. Without `--apply` it changes nothing: for each marker, or the one `--attempt` names, it reports the call's `transport.jsonl` events, the decoded captures it considered and the action `--apply` would take. While a call runs, its command holds a lock on the marker, and a marker still locked by a running `generate` or `reconcile` is left alone; markers written before this lock existed have none, so do not reconcile while an older `generate` runs. A marker whose attempt already has a generation, or whose call already has the `no_model_outcome` event `generate` logs (logged after the marker was written), as when a command stopped just after recording or logging it, is removed; nothing is logged in `transport.jsonl` again. Otherwise the attempt's captured bodies are decoded, less those `no_model_outcome` events resolved: each resolves one captured line of the body it names, the earliest, so a later call's byte-identical body is still read. The first that is intact (a JSON line naming the attempt whose decoded bytes match its SHA-256), answers a request logged at its transport index (every request logged for the attempt must carry the frozen payload's hash), and reads as a model outcome (`completed`, `refusal`, `truncated_output` or `malformed_response`) of the pinned snapshot is recorded as the generation, as `generate` would have recorded it: `response_capture` names the capture and `reconciliation` the marker, transport attempt, request ID, capture time and request hash, while `generation_elapsed_seconds` stays unrecorded. Anything else keeps the marker: no captured body (a lost response or an interrupted command), an unreadable body, a provider failure, another model's response, or evidence that does not match. Only `--release-unreadable` with `--apply` releases it: the call is logged in `transport.jsonl` as `no_model_outcome`, as `generate` would log it, naming the newest of those captures, the last HTTP or connection error the call logged (`last_error`) and the reason, the marker is removed, and the next `generate` sends the same attempt ID again, unless the captured body or a logged HTTP 400 rejected the prompt as `invalid_prompt`, which is then remembered like any rejection. Each change is first appended to `amendments.jsonl` as an `in_flight_reconciliation` row with the marker and a `resolution`: `generation_recorded` with the capture, `released_without_model_outcome` with the reason and capture, `generation_already_recorded` or `outcome_already_logged`. `inspect-capture` is read-only: for each captured body, or only those of `--attempt`, it prints its integrity, transport index, request ID, size, the provider's response ID, model, status, usage and error code, how it reads, whether `reconcile` could record it, which `transport.jsonl` events name it and whether it is the recorded generation, never the model's text or a credential.

Other providers can supply JSONL records to `import-responses`; every record must include `attempt_id`, `prompt_hash`, `model_snapshot`, and the entire `raw_response` text as a string. Optional `generation_status` is `completed` (the default), `refusal`, `truncated_output` or `malformed_response`. Only Lean decides success, so any other value, including `success`, `null` or a provider stop reason, is rejected. Optional `usage` is an object or null; `generation_settings` and `provenance` are stored as given, and other fields are ignored. Every row is checked before anything is written, including that the stored fields are strict JSON (no NaN, Infinity or unpaired surrogate escapes) nested at most 64 levels deep, and different content for an attempt that already has a generation is an error. Preserve provider settings/usage/provenance as well. The exported prompts contain the required IDs and hashes.

## Verify, report, and reproduce

```sh
python3 -m lean_compression evaluate --run runs/pilot-development --version v1
python3 -m lean_compression analyze --run runs/pilot-development --version v1 \
  --references work/reference-checks-pilot --output reports/pilot-development
```

A checker or infrastructure failure in verification or extraction, such as an unavailable container runtime, is not a proof verdict. `evaluate` appends it to `evaluations/<version>/incidents.jsonl`, leaves the slot pending, and reports `checker_incidents`; rerun it to retry those slots. An extraction timeout or resource limit is final: the proof stays valid and its metrics unavailable. A Lean process stopped by a resource limit (memory, CPU time or file size) gets that outcome whatever result record it left, partial or complete, and keeps its return code; an unreadable record from any other exit is an incident. `analyze` counts the incidents still pending and the provider calls without a model outcome in `run_provenance`, and the report's incomplete notice names them. A rescored version copies records, not incidents, so its analysis also reports the incidents inherited from the version it was rescored from; to retry them, rerun `evaluate` on that version and rescore it into a new version.

The report includes all scheduled slots, correctness/coverage, paired B−A and B−C effects, their source/expansion decomposition, common-task sensitivity, reference attainment, family-cluster bootstrap intervals, plots, and a proof-audit bundle. Confidence intervals do not remove success-selection bias. An unfinished run is labeled incomplete and cannot support a conclusion.

`resource_usage.csv` reports observed timing, Lean heartbeats, token usage and memory peaks, including failed attempts. Missing measurements stay unavailable. It does not infer billing costs or add reasoning tokens to an output-token total that already includes them.

`analyze` never calls a model or invokes Lean. It writes into a new or empty directory and publishes the report only when it is complete; an existing empty directory is filled in place and keeps its permissions. `all_scheduled_attempts.json` names each evaluation file and its SHA-256 instead of copying its dependency graph, and only its owner can read it. If the run was frozen with reference checks, it accepts only those reference records; `run_provenance` names the reference policy and records it used. `rescore --from-version v1 --version v2` recomputes metrics from stored graphs into a new version and records an amendment; metrics the evaluator withheld, with a recorded `metric_unavailable_reason`, stay withheld. It refuses an existing version and writes a `policy.json` naming the source version, the engine that produced the graphs and the metric code. Attempt and reference metrics in one analysis must come from the same metric code: when every measured attempt was scored by the current code, `analyze` also rescores the reference anchors that other code scored from their stored graphs, including anchors that code left unavailable but not ones whose metrics were withheld, and lists them in `run_provenance.reference_metrics_recomputed`; any other mixture is refused. Raw generations remain unchanged. Frozen manifests and existing evidence refuse conflicting overwrites. `evaluate`, `analyze` and re-imports verify each stored generation's `attempt_id`, `prompt_hash` and `raw_response_hash`; an edited or misplaced generation file stops the command.

Rechecking with a changed Lean checker or extractor needs another new version; `evaluate` never resumes a rescored version. `evaluate` accepts only the engine frozen with the run, and another engine only this way, for a run frozen with reference checks: first re-check the same reference proofs with the changed engine into a new directory, then pass it: `evaluate --engine <changed> --version v3 --reference-checks <directory>`. The directory must be made by exactly that engine, in the run's environment and measurement settings, and hold a record of every frozen proof. A task without one has a pending checker incident, so `evaluate` refuses until a rerun of `verify-references` into that directory reports no checker incidents. The verified records become v3's anchors. A task whose frozen proof the changed engine rejects, or in production cannot measure, is bound unanchored instead, because a rerun cannot change that verdict. The `policy.json` of v3 names the anchors, lists those tasks in `unanchored_reference_tasks` and names the frozen engine its engine replaces (`frozen_engine_hash`). The amendment, with the same fields, is logged once in `amendments.jsonl` after v3's `policy.json` is published, however many workers start or resume v3; a worker refused because another bound v3 to other reference checks logs none. The `run_provenance` of `analyze --version v3` lists the unanchored tasks too. `analyze` counts them in `missing_reference_task_ids`, so attainment is unavailable and compression conclusions are not ready; nothing is substituted for a missing anchor. `analyze --version v3` accepts only that directory as `--references`; other versions keep the anchors frozen with the run. Every other engine mismatch is refused, and a run frozen without reference checks has no such remedy: freeze a new run for another engine. A manifest frozen before `engine_hash` existed keeps its earlier engine checks. A frozen run's reference proofs never change. To anchor such a task again, freeze a new run whose reference checks verify every task, with a corrected reference proof or without that task, and import the stored responses into it with `import-responses`; unchanged tasks keep their attempt IDs.

## Production resource profile

```sh
python3 runtime/prepare_image.py --output configs/environment.docker.json
```

This builds the pinned Lean/Mathlib runtime and records its immutable image ID. Copy that ID to `validation.docker_image` in a new configuration; set `isolation` to `docker`. Candidate execution disables networking, drops capabilities, mounts the engine read-only, makes the image filesystem read-only, limits processes, and enforces the configured CPU/memory budget. No host directory is writable from the container: its `/job` is a size-capped tmpfs holding the request read-only, and `run-lean` returns the result and memory evidence on stdout. An image built before that returns no result, so every attempt in it is a checker incident; rebuild it.

Use the environment record collected inside that image for production. Host and container build hashes are not interchangeable. The build hashes source before removing package Lean source files and Git history from the execution image; `prepare_image.py` checks that exclusion before writing the runtime manifest. Reverify references in the container and pass their directory through `freeze --reference-checks`. A production freeze accepts only a `verify-references` directory whose policy matches the environment and measurement settings, with a verified, measurable record from that engine, including its checked proof, for every task. Its `--engine` must be that engine; the manifest binds each task's record and the engine, and `evaluate` refuses any other engine unless given its re-checks of the frozen proofs, as described above. It also refuses a task whose checked reference proof is copyable from the task's own prompt by the answer-access rules of `validate-dataset` above. Complete task audits, pilot calibration, and preregistration before freezing the main evaluation: a production `main` freeze requires `--pilot-report`, the `analyze` output of a complete, non-development `pilot` run of the same model, `experiment` and `protocol_version` (its `run_provenance` records them, and its `models` must include that model; regenerate an older report), whose `analysis.json` SHA-256, in lowercase hex as `shasum -a 256` prints it, is `preregistration.pilot_report_hash`. A production freeze fails closed if the environment, reference evidence, or task prerequisites are unresolved.

[Runtime acceptance instructions](docs/runtime-validation.md) enable actual container checks when Docker is available. These were skipped on the current machine because the daemon was unavailable. Local reference checks and the Lean integration tests did run.

## Definitions and scope

- `W_S` / `W_B`: distinct original Lean parser leaf spans in explicit binders/proposition and submitted proof; excludes the theorem keyword, target name, and separator. Full declaration counts and UTF-8 byte spans are also stored.
- `U_S` / `U_B`: weighted recursive reference counts. Every occurrence contributes, including repetitions through shared expression nodes. Mathlib and evaluator declarations expand; other frozen package declarations and `Sort` are atomic.
- `T0 = (U_S + U_B) / (W_S + W_B)`; `I0 = W_B / W_S`; `D` is longest dependency depth. Counts use arbitrary-precision integers, ratios preserve exact numerators/denominators, and logarithms use leading bits.
- Cycles, unknown dependencies, zero-reference nonprimitive declarations, absent bodies, and extraction timeouts remain explicit missing-metric states. They do not turn verified proofs into failures.
- `boundary_policy`, `source_token_policy`, `unknown_dependency` and `dependency_cycle` label the one implemented policy (`expand_mathlib_and_evaluator_v1`, `original_syntax_v1`, `error`, `metrics_unavailable`); `freeze`, `evaluate`, `verify-references`, `baseline` and `index-closures` reject any other value.
- Tactic automation, aliases, and irrelevant dependency growth can change the scores. Inspect the numerator/denominator breakdown and sampled proofs.
- A real Lean stress test gives an alias chain a 3× higher `T0` with identical source length. Both proofs are valid; this is a tested example of score inflation without source savings.
- The optional new-lemma transfer, PageRank `I1`, and synthetic monoid studies are outside the core implementation. `f(s)` is not measured by this finite Lean benchmark.

## Sources

- [Supplied evaluation specification](docs/evaluation-plan.md).
- Aksenov, Bodnia, Freedman, Mulligan, [Compression is all you need: Modeling Mathematics](https://arxiv.org/abs/2603.20396), especially sections 3 and 5.
- [Pinned Mathlib source](https://github.com/leanprover-community/mathlib4/tree/274ed6d67d9b226c557813b9ce437574cccdce11).
- [Lean proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/); executable API behavior is tested against the older pinned toolchain.
- [OpenAI text generation](https://developers.openai.com/api/docs/guides/text) and [reasoning token behavior](https://developers.openai.com/api/docs/guides/reasoning).
