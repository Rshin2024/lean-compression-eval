# Docker runtime acceptance

The production Docker runtime has an optional acceptance test in `tests/test_docker_runtime.py`. It uses an **already available immutable image** and an existing environment manifest produced for that image. It does not build an image, install Docker, pull an image, or make model calls.

The container acceptance checks were **not executed in this development environment because a Docker daemon was unavailable**. The POSIX shell syntax check for `runtime/run-lean` runs independently. A skipped container test is not evidence that production isolation works.

## Enable the checks

From the package directory, point the opt-in variable to an existing Docker environment manifest:

```sh
LEAN_COMPRESSION_DOCKER_ENV=/absolute/path/to/environment.docker.json \
PYTHONPATH=src python3 -m unittest discover -s tests -p test_docker_runtime.py -v
```

The manifest must have `runtime_backend: "docker"`, an immutable `docker_image` containing a complete SHA-256 image ID or repository digest, and the runtime environment fields consumed by the runner. Its image must already be present in the selected Docker daemon. The test skips when the variable is unset, the manifest is absent, Docker is absent, the daemon is unavailable, or the referenced image is absent. An explicitly supplied malformed manifest fails instead of silently passing. No image is built or fetched to satisfy missing prerequisites.

## What is exercised

The test calls the actual `runner.execute_engine()` Docker path. A small trusted test wrapper, mounted in a temporary directory, collects observations inside that same container and invokes a byte-for-byte copy of the production Lean evaluator. The request imports only `Init`, proves `True` with `True.intro`, permits no axioms, and requires successful independent kernel replay. This instruments the harness; it does not relax candidate proof restrictions or change production code.

The checks inspect behavior and kernel state rather than comparing Docker command arguments:

- Only the loopback network interface is present, no default IPv4 route exists, and a local UDP route lookup to a documentation-only address fails. The probe sends no datagram and does not contact an external service.
- The root mount is actually read-only. Attempts to create files in the compiled package and mounted evaluator directory fail, while the job directory remains writable.
- Active cgroup CPU and memory controller files report the requested **0.5 CPU** and **1,024 MiB** limits. Both cgroup v2 and conventional Docker cgroup v1 controller paths are supported. On v2, swap is disabled. PID limits, effective capabilities and `NoNewPrivs` are also inspected.
- Compiled `.olean` files exist, while no `.lean` sources or `.git` entries remain under `/opt/eval/package`, including dependency checkouts. The scan follows directory links with cycle detection. This exclusion scope is the benchmark package and its Mathlib/dependency sources; the trusted mounted evaluator and the installed Lean toolchain are intentionally outside it.

The evaluator run has a 120-second acceptance-test wall limit, allowing for the additional trusted probe wrapper. These checks read active cgroup limits; they do not deliberately trigger an out-of-memory kill or benchmark CPU throughput. Passing this test establishes the observed behavior for the selected image, Docker daemon and host at that run. Retain the manifest and immutable image identity with any production validation report.

The separate shell check invokes `sh -n runtime/run-lean`; it checks syntax only and makes no claim about Docker execution or resource enforcement.
