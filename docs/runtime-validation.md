# Docker runtime acceptance

The production Docker runtime has an optional acceptance test in `tests/test_docker_runtime.py`. It uses an **already available immutable image** and an existing environment manifest produced for that image. It does not build an image, install Docker, pull an image, or make model calls.

The container acceptance checks were **not executed in this development environment because a Docker daemon was unavailable**. The POSIX shell syntax check for `runtime/run-lean` runs independently. A skipped container test is not evidence that production isolation works.

## Enable the checks

From the package directory, point the opt-in variable to an existing Docker environment manifest:

```sh
LEAN_COMPRESSION_DOCKER_ENV=/absolute/path/to/environment.docker.json \
PYTHONPATH=src python3 -m unittest discover -s tests -p test_docker_runtime.py -v
```

The manifest must have `runtime_backend: "docker"`, an immutable `docker_image` containing a complete SHA-256 image ID or repository digest, and the runtime environment fields consumed by the runner. Its image must already be present in the selected Docker daemon; the daemon and image checks use the runner's Docker client environment. The test skips when the variable is unset, the manifest is absent, Docker is absent, the daemon is unavailable, or the referenced image is absent. An explicitly supplied malformed manifest fails instead of silently passing. No image is built or fetched to satisfy missing prerequisites.

## What is exercised

The test calls the actual `runner.execute_engine()` Docker path. A small trusted test wrapper, mounted from a world-readable temporary directory under a non-default file name, collects observations inside that same container and invokes a byte-for-byte copy of the production Lean evaluator. The request imports only `Init`, proves `True` with `True.intro`, permits no axioms, and requires successful independent kernel replay. This instruments the harness; it does not relax candidate proof restrictions or change production code.

The checks inspect behavior and kernel state rather than comparing Docker command arguments:

- Only the loopback network interface is present, no default IPv4 route exists, and a local UDP route lookup to a documentation-only address fails. The probe sends no datagram and does not contact an external service.
- The root mount is actually read-only. Attempts to create files in the compiled package and mounted evaluator directory fail, while the job directory remains writable.
- Active cgroup CPU and memory controller files report the requested **0.5 CPU** and **1,024 MiB** limits. Both cgroup v2 and conventional Docker cgroup v1 controller paths are supported. On v2, swap is disabled. PID limits, effective capabilities, `NoNewPrivs` and the in-container CPU-time limit are also inspected.
- Compiled `.olean` files exist, while no `.lean` sources or `.git` entries remain under `/opt/eval/package`, including dependency checkouts. The scan follows directory links with cycle detection. This exclusion scope is the benchmark package and its Mathlib/dependency sources; the trusted mounted evaluator and the installed Lean toolchain are intentionally outside it.

The evaluator run has a 120-second acceptance-test wall limit, allowing for the additional trusted probe wrapper. These checks read active cgroup limits; they do not deliberately trigger an out-of-memory kill or benchmark CPU throughput. Passing this test establishes the observed behavior for the selected image, Docker daemon and host at that run. Retain the manifest and immutable image identity with any production validation report.

The separate shell check invokes `sh -n runtime/run-lean`; it checks syntax only and makes no claim about Docker execution or resource enforcement.

## Mounts, permissions and the Docker client

Each attempt gets a private `0700` scratch directory on the host. Only its `job` subdirectory is mounted read-write at `/job`; host-side logs stay in the parent. The container keeps the image user, root with every capability dropped. No `--user` mapping is used because the container uid relates differently to the host user on each platform:

- **Rootful Linux Docker run by a non-root user.** Capability-less root, like remapped ids under userns-remap, gets only the "other" permission bits on host-owned files. The job directory is therefore `0777` and the request `0644`. `run-lean` sets `umask 022`, so root-owned results stay readable by the host user. Because the job directory has no sticky bit, that user can delete root-owned files in it, but not the contents of a directory created in the container. The trusted evaluator writes only files. Before it exits, `run-lean` stops any process Lean left in the container and makes everything under `/job` world-writable and non-sticky, so whatever candidate code created stays removable. Images built before this step lack it.
- **Rootless Docker or Podman.** Container root is the invoking user. A `--user` mapping would turn it into a subordinate uid without access; the permissive modes are harmless.
- **Docker Desktop.** File sharing maps ownership to the host user. Scratch directories come from the harness's temporary directory (`TMPDIR`), which must be a shared path; Docker Desktop shares `/var/folders` and `/tmp` by default.

The engine's directory is mounted read-only at `/engine`, and the container runs the file passed as `--engine`, whose hash the records carry. On rootful Linux that directory and file must be world-readable, as in a normal checkout. The harness reads `result.json`, `peak_memory_bytes` and `memory_stat` only as regular files and never follows links created in the container.

Every Docker client call in the runner (`docker run` and the forced removal) uses `runner.docker_cli_env()`, and preflights should use it too. It passes `PATH`, `HOME`, `XDG_RUNTIME_DIR`, `DOCKER_HOST`, `DOCKER_CONTEXT`, `DOCKER_CONFIG`, `DOCKER_CERT_PATH`, `DOCKER_TLS`, `DOCKER_TLS_VERIFY` and `DOCKER_API_VERSION`, plus `SSH_AUTH_SOCK` when `DOCKER_HOST` is an `ssh://` URL. API keys are not passed, and no host variable reaches the container.

After a timeout or a non-zero exit the runner force-removes the container. Only a zero exit from `docker rm -f`, or a "no such container" error (how older clients and Podman report a container that is already gone), confirms removal. If removal fails, exits with any other error, or does not finish within 10 seconds, the attempt is recorded as `infrastructure_error` with `failure_reason: container_cleanup_unconfirmed`, never as a model outcome. The container may still be running, so its scratch directory is kept instead of being deleted under it.

Otherwise the host deletes the scratch directory once the container is gone. Cleanup never raises and never changes the attempt's outcome. Whatever it cannot delete stays, for example directories left on rootful Linux by a container that was killed at the wall limit, or that ran an image without the `run-lean` step above. In both cases the record gives the kept path as `resources.scratch_retained` and the cause as `resources.scratch_retained_reason`: `container_cleanup_unconfirmed` or the deletion error. Delete it as root once the container is gone.

The Docker client runs in its own session, so Ctrl-C reaches only the harness. An interrupted or otherwise aborted attempt kills the client, force-removes the container under the same 10-second rule and only then deletes the scratch directory; if removal is unconfirmed, the directory is kept. The interruption then propagates, with a note naming any kept directory.

An in-container CPU-time limit of `ceil(wall limit × max(1, CPU quota)) + 1` seconds stops a CPU-bound container that outlives its removal; under the CPU quota it cannot bind before the wall limit. Docker policies therefore need a positive `cpu_quota`, because zero would lift the CPU limit.

`verify-references` and `baseline` run Lean in Docker unless `validation.isolation` names another backend. They then require an environment record collected in the image (`runtime_backend: docker`) whose `docker_image` equals `validation.docker_image`, and refuse any other record before writing to the output directory, as `freeze` does for container runs. The image must be a content digest: `sha256:` and 64 lowercase hex digits, optionally after `name@` as a repository digest. A tag such as `sha256:latest`, an empty or truncated ID such as `sha256:5d0da3dc9764` and a malformed reference such as `name@sha256:x` are not. `freeze` refuses a missing or other `validation.docker_image` before writing the run, `verify-references` and `baseline` before writing to their output directory, the runner before starting a container, and `runtime/prepare_image.py` refuses any other image, built or passed as `--image-id`, before running it.

`tests/test_docker_command.py` checks the exact command, mount permissions, client environment, timeout classification and cleanup handling without a daemon. Run this acceptance test on rootful Linux as a non-root user before production. Docker Desktop and rootless runtimes would not reveal a regression in the permission model.

## Memory evidence

After Lean exits, and after `run-lean` as container init has stopped anything Lean left running, it removes whatever the evaluated code may have left at `/job/peak_memory_bytes` and `/job/memory_stat` (`rm -f`, so a directory there stays) and copies the container's cgroup files there; a file it cannot read is skipped. The runner reads neither file after a timeout, which kills `run-lean` before its copy, or when it could not confirm the container's removal (`container_cleanup_unconfirmed`), since the container may then still be running: those records have `peak_memory_reason: not_read_after_timeout_or_unconfirmed_cleanup` and no `memory_stat_at_exit`. Only Docker records fill these fields:

- `peak_memory_mib` comes from `memory.peak` (cgroup v2) or `memory.max_usage_in_bytes` (v1). `peak_memory_scope` labels it as the cgroup high-water mark of all memory charged to the container, page cache included; it is not a process RSS. Page cache is charged to the cgroup whose process brought a page into memory, so the `.olean` files a Mathlib import maps count toward the peak when this container is the first to read them, and hardly at all when an earlier container or the host already cached them. The peak therefore depends on cache state and evaluation order, not only on the proof.
- `memory_stat_at_exit` holds four `memory.stat` values read after Lean exited, under their cgroup v2 names: `anon`, `file` and `file_mapped` in bytes (v1: `rss`, `cache` and `mapped_file`) and the cumulative major-fault count `pgmajfault`. `file` is the page cache, tmpfs included, that the container still has charged. With `pgmajfault`, which counts faults that had to read from storage, it shows whether a high peak reflects a cold cache. Lean's anonymous memory and file mappings are released when it exits, so `anon` and `file_mapped` cover only what is still charged then, mostly `run-lean` itself; they say nothing about Lean's working set. Neither cgroup version keeps a high-water mark of anonymous memory alone. One would need sampling during the run, which `run-lean` does not do.

Memory evidence never decides an outcome. A missing, unreadable or non-regular file, a file over 64 KiB, and a value other than 1 to 20 ASCII digits (surrounding whitespace aside) leave their values null; without a peak, the record keeps `peak_memory_reason: not_instrumented` and has no `peak_memory_scope`. The macOS development profile records no memory evidence.

Images built with an older `run-lean`, such as the local `lce-runtime:warm` and `lce-runtime:followup` images, neither remove nor write `/job/memory_stat`. Their `memory_stat_at_exit` holds whatever the evaluated code left there, else nulls, so it is evidence only for images built with this `run-lean`. An older `run-lean` overwrites `/job/peak_memory_bytes` from a readable cgroup peak file but never removes it, so without one a planted peak is recorded. The environment record does not identify the script. Inspect the image's copy, for example with `docker run --rm --network none --read-only <image> cat /opt/eval/run-lean`: this `run-lean` has the line `rm -f /job/peak_memory_bytes /job/memory_stat`.

`tests/test_runtime_gates.py` runs `run-lean` itself under the local `sh` and, where present, `dash` (Ubuntu's `/bin/sh`). It moves `/opt/eval`, `/sys/fs/cgroup` and `/job` into a temporary directory holding fake cgroup v1 and v2 files and a stand-in for Lean, then checks what the runner records from the copies, including oversized files and values, and that it ignores files planted before a timeout. It observes no real cgroup.

## Residual risk: host disk use through `/job`

The runner makes the image filesystem read-only (`--read-only`), mounts `/engine` read-only and gives `/tmp` a tmpfs held in memory and capped at 128 MiB. Its only writable mount on the host's disk is `/job`, the attempt's `job` directory under the harness's temporary directory (`TMPDIR`), and nothing caps its total size. `--ulimit fsize=268435456` stops each file at 256 MiB, but code that escaped the evaluator's restrictions could create any number of files, at disk speed, while its container runs. The check's container runs for up to `validation.wall_seconds` (`tactic_portfolio_total_wall_seconds` for `baseline`), and metric extraction runs in a second container for up to `extraction.wall_seconds`; all three are 60 seconds in the shipped configurations. Each container's files stay until its `execute_engine()` call returns and removes the scratch directory; the scratch directory of a container whose removal could not be confirmed (`container_cleanup_unconfirmed`), which may still be running, is kept. Meanwhile they could fill the volume holding `TMPDIR`, making other evaluations and any evidence writes on that volume fail. A harness killed mid-attempt leaves its scratch directory behind. On rootful Linux, a container killed at its wall limit, before `run-lean` opened `/job`, can leave directories the host user cannot delete; they stay on that volume, reported as `resources.scratch_retained`, until removed as root. The Docker client's `stdout.log` and `stderr.log` are host files too, but the client runs under the harness's 256 MiB per-file limit. The macOS development profile likewise leaves its scratch directory writable with only that per-file limit; it is for trusted inputs only. This was not reproduced: no Docker daemon was available and no write payload was built.

The mounts are unchanged because a replacement cannot be tested without a Docker daemon. The proposed design, to be validated with the acceptance test before use:

- Mount a size-capped tmpfs at `/job` (for example `--tmpfs /job:rw,noexec,nosuid,size=64m`). Files written there stay in memory, count against the container's memory limit and vanish with the container.
- Bind-mount the host's `request.json` read-only at `/job/request.json`.
- Pre-create `result.json` on the host, world-writable like today's job directory, and bind-mount only that file writable at `/job/result.json`. The evaluator rewrites it in place with `IO.FS.writeFile`, which works through a file mount, and the fsize limit caps it at 256 MiB.
- Give `peak_memory_bytes` and `memory_stat` the same treatment, or return them another way, since files left on the tmpfs disappear with the container. `run-lean`'s `rm -f` cannot unlink a mounted file, but its copy then overwrites the file in place.

Host disk use per attempt would then be bounded by those files and the two client logs.
