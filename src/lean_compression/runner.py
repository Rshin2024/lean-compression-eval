"""Fresh-process verification and separately budgeted metric extraction."""
from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from .integrity import is_content_digest, require_implemented_policy, verified_generation
from .io import (CLAIM_PROTOCOL, append_event, canonical, claim, claim_locks, digest, evaluator_hash, file_hash, kept_claims, now, read_json,
                 remove_abandoned_claim, retain_claims, task_input_hash, temporary_files, verification_request_hash, write_json)
from .metrics import score_graph
from .outcomes import EXTRACTOR_OUTCOMES, GENERATION_OUTCOMES, PROVIDER_STATUSES, outcome_class
from .prompts import adapt_response
from .store import evaluation_directory, generation_path, load_run, logged_evaluations, rechecked_anchors


def sandbox_profile(read_paths: list[str], scratch: str) -> str:
    """macOS development isolation; production uses container cgroups/read-only mounts.

    System frameworks may read OS caches outside /System. Deny user/private paths
    instead of pretending this development profile enforces a full filesystem allowlist.
    """
    import unicodedata
    def quote(path):
        # SBPL decodes JSON's \" and \\ escapes but not \uXXXX or \b: write UTF-8 and reject control characters.
        value = str(Path(path).resolve())
        if any(unicodedata.category(char) in ("Cc", "Cs") for char in value):
            raise ValueError(f"Sandbox profile cannot quote control or non-UTF-8 characters in path {value!r}")
        return json.dumps(value, ensure_ascii=False)
    protected = ["/Users", "/Volumes", "/private/tmp", "/private/var/folders"]
    rules = ["(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)"]
    rules += [f"(deny file-read-data (subpath {quote(p)}))" for p in protected]
    rules += [f"(allow file-read-data (subpath {quote(p)}))" for p in [*read_paths, scratch]]
    rules += [f"(allow file-write* (subpath {quote(scratch)}))", '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules)


# Every file Lean writes, and each log of the process the runner starts, stops at this size. In Docker it also caps
# the result run-lean returns; the memory evidence it returns is capped at 64 KiB.
FILE_SIZE_LIMIT, EVIDENCE_LIMIT = 256 * 1024 * 1024, 64 * 1024
# run-lean ends the container's stdout with these files, then this trailer of their sizes ("-": absent, "!": not a
# readable regular file).
RETURNED_FILES = ("result.json", "peak_memory_bytes", "memory_stat")
RETURNED_TRAILER = re.compile(rb"lean-compression-output v1 ([0-9]{1,10}|-|!) ([0-9]{1,10}|-|!) ([0-9]{1,10}|-|!)\n\Z")


def _limits(memory_mib: int, wall_seconds: float):
    import resource
    # CPU-seconds cap is a backstop, not a CPU quota. Docker enforces the latter.
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(wall_seconds) + 1, math.ceil(wall_seconds) + 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT, FILE_SIZE_LIMIT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory_mib * 1024 * 1024,) * 2)


def docker_cli_env() -> dict:
    """Environment for every docker CLI call: the client's endpoint and configuration only.

    Preflights must use it too, so they query the daemon that runs candidates. API keys are
    never passed, and `docker run` forwards none of these variables into the container.
    """
    names = ["HOME", "XDG_RUNTIME_DIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
             "DOCKER_CERT_PATH", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_API_VERSION"]
    if os.environ.get("DOCKER_HOST", "").startswith("ssh://"):
        names.append("SSH_AUTH_SOCK")
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            **{name: os.environ[name] for name in names if os.environ.get(name)}}


def _read_output(path: Path, limit: int | None = None) -> str | None:
    """Read a file the isolated process wrote, never following its links or waiting on a FIFO.

    A file of more than limit bytes, when one is given, is malformed and never read whole.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path.name} is not a regular file")
        if limit is not None:
            with open(fd, "rb", closefd=False) as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(f"{path.name} is larger than {limit} bytes")
            return data.decode("utf-8")
        with open(fd, encoding="utf-8", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _container_output(path: Path) -> tuple[int, dict | None]:
    """How many bytes of the container's stdout (the client's log at path) Lean printed, and the files run-lean returned.

    Only a trailer that ends the stream counts. run-lean writes it as container init after stopping everything Lean
    started, so nothing printed before can pose as it. Without one everything is Lean's, and no file was returned.
    A returned file is its bytes up to its cap, or "-" (absent), "!" (not a readable regular file) or ">" (over the cap,
    never read).
    """
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - 128))
        tail = stream.read()
        trailer = RETURNED_TRAILER.search(tail)
        if trailer is None:
            return size, None
        tokens = [token.decode() for token in trailer.groups()]
        start = size - len(tail) + trailer.start() - sum(int(token) for token in tokens if token.isdigit())
        if start < 0:
            return size, None
        stream.seek(start)
        files = {}
        for name, token in zip(RETURNED_FILES, tokens):
            if not token.isdigit():
                files[name] = token
            elif int(token) > (FILE_SIZE_LIMIT if name == "result.json" else EVIDENCE_LIMIT):
                stream.seek(int(token), os.SEEK_CUR)
                files[name] = ">"
            else:
                files[name] = stream.read(int(token))
    return start, files


def _returned(files: dict | None, name: str) -> str | None:
    """A file run-lean returned, read like _read_output reads one: None if absent, ValueError if unusable."""
    if files is None:
        raise ValueError("The container's stdout does not end with run-lean's returned files: run-lean did not finish, "
                         "or the image predates returning them")
    part = files[name]
    if part == "-":
        return None
    if part in ("!", ">"):
        raise ValueError(f"{name} is not a regular file or cannot be read" if part == "!" else f"{name} is larger than its limit")
    return part.decode("utf-8")


def _finite(text: str) -> float:
    """A number in Lean's result record; NaN, an infinity or one beyond a float's range, which no record can store, is
    unreadable."""
    if not math.isfinite(value := float(text)):
        raise ValueError(f"Lean's result record holds {text}, which is not a finite number")
    return value


def _malformed(result: dict) -> str | None:
    """The first field the harness reads from Lean's result record that is not shaped as the engine writes it, if any.

    Such a record is unreadable, never a verdict. The graph evidence these shapes hold is score_graph's to judge.
    """
    def strings(value):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    heartbeats, diagnostics = result.get("heartbeats"), result.get("diagnostics", {})
    units = heartbeats.get("max_heartbeats_units") if isinstance(heartbeats, dict) else None
    total = units.get("total") if isinstance(units, dict) else None
    shapes = {"validity_status": isinstance(result.get("validity_status"), str),
              # Thousands of heartbeats; bounded so that summed totals stay finite.
              "heartbeats": heartbeats is None or (isinstance(total, (int, float)) and not isinstance(total, bool)
                                                   and 0 <= total <= 2 ** 53),
              "diagnostics": isinstance(diagnostics, dict) and strings(diagnostics.get("syntax_kinds", [])),
              "candidate_declarations": strings(result.get("candidate_declarations", [])),
              "statement_auxiliaries": result.get("statement_auxiliaries") is None or strings(result["statement_auxiliaries"])}
    if result.get("metric_status") == "available":
        tokens, nodes = result.get("source_tokens"), result.get("nodes")
        shapes.update(source_tokens=isinstance(tokens, dict) and "W_S" in tokens and "W_B" in tokens,
                      root=isinstance(result.get("root"), str),
                      nodes=isinstance(nodes, list) and all(isinstance(node, dict) and isinstance(node.get("name"), str)
                                                            for node in nodes))
    return next((f"Lean's result record has a malformed {field} field" for field, shaped in shapes.items() if not shaped), None)


def _remove_scratch(scratch: Path) -> str | None:
    """Delete an attempt's scratch directory without raising; return the error if any of it stays."""
    try:
        # Reopen directories left without owner write or search bits, as the development sandbox's Lean can leave
        # them. A container writes nothing here: its /job is a tmpfs.
        for root, dirs, _ in os.walk(scratch):
            for name in dirs:
                path = os.path.join(root, name)
                try:
                    if not os.path.islink(path):
                        os.chmod(path, 0o700)
                except OSError:
                    pass
        shutil.rmtree(scratch)
        return None
    except Exception as exc:  # also RecursionError: Python 3.11 walks and deletes a deep tree recursively
        try:
            shutil.rmtree(scratch, ignore_errors=True)
        except Exception:
            pass
        return str(exc) if os.path.lexists(scratch) else None


def _remove_container(name: str) -> str | None:
    """Force-remove a container within 10 seconds; the error unless its removal is confirmed."""
    try:
        removal = subprocess.run(["docker", "rm", "-f", name], env=docker_cli_env(), capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    # Success, or "no such container" from clients that report a removed container as an error.
    if removal.returncode and b"no such container" not in removal.stderr.lower():
        return f"docker rm -f exited {removal.returncode}: {removal.stderr.decode(errors='replace').strip()}"
    return None


def _retain(container: str, error: str | None, scratch: Path) -> list[Path]:
    """Keep this worker's claims for a container whose removal is unconfirmed: it may outlive the worker, so no worker
    checks their task or slot again until an operator confirms it is gone (io.retain_claims)."""
    return retain_claims({"reason": "container_removal_unconfirmed", "container": container, "error": error,
                          "scratch_retained": str(scratch)})


def execute_engine(request: dict, environment: dict, engine: Path, policy: dict,
                   *, mode: str, wall_seconds: float) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="lean-compression-")).resolve()
    containers = []
    try:
        result = _run_engine(scratch, request, environment, engine, policy, mode=mode, wall_seconds=wall_seconds,
                             containers=containers)
    except BaseException as error:
        # Interrupted (Ctrl-C) or aborted: the container is removed before its bind source, which stays unless
        # the removal is confirmed, as do this worker's claims. The error propagates and names whatever is kept.
        unconfirmed = containers and _remove_container(containers[0])
        kept = f"removal of container {containers[0]} unconfirmed: {unconfirmed}" if unconfirmed else _remove_scratch(scratch)
        if kept:
            error.add_note(f"Kept scratch directory {scratch}: {kept}")
        if unconfirmed:
            try:
                for path in _retain(containers[0], unconfirmed, scratch):
                    error.add_note(f"Kept {path}: delete it once container {containers[0]} is confirmed gone")
            except OSError as exc:
                error.add_note(f"Could not keep this worker's claims for container {containers[0]}: {exc}")
        raise
    # A container whose removal is unconfirmed keeps its bind source and this worker's claims; keeping the claims
    # fails the attempt if it cannot be done. Cleanup never raises or replaces the attempt's outcome; whatever
    # stays is reported for the operator.
    if result["resources"].get("scratch_retained_reason") == "container_cleanup_unconfirmed":
        if kept := _retain(containers[0], result.get("error"), scratch):
            result["resources"]["claims_retained"] = [str(path) for path in kept]
    retained = result["resources"].get("scratch_retained_reason") or _remove_scratch(scratch)
    if retained:
        result["resources"].update(scratch_retained=str(scratch), scratch_retained_reason=retained)
    return result


def _run_engine(scratch: Path, request: dict, environment: dict, engine: Path, policy: dict,
                *, mode: str, wall_seconds: float, containers: list) -> dict:
    started = time.monotonic()
    # The 0700 scratch parent keeps the job private on the host. Docker mounts only the request, read-only; container
    # uids differ from the host user (capability-less root on rootful Linux, remapped ids under userns-remap), so it
    # is world-readable.
    job = scratch / "job"
    job.mkdir()
    input_file, output_file = job / "request.json", job / "result.json"
    write_json(input_file, {**request, "mode": mode})
    input_file.chmod(0o644)
    isolation = policy.get("isolation", "docker")
    clean_env = {"PATH": str(Path(environment["lean_binary"]).parent) + ":/usr/bin:/bin", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                 "TMPDIR": str(scratch), "LEAN_PATH": os.pathsep.join(environment["lean_search_paths"])}
    source_engine = engine.resolve()
    extra_read = [str(source_engine.parent), environment["toolchain_root"], *environment["lean_search_paths"]]
    resource_evidence = {"wall_seconds_limit": wall_seconds, "memory_mib_requested": policy.get("memory_mib", 4096),
                         "isolation": isolation, "cpu_quota": None, "memory_enforced": False,
                         "network": "disabled", "credentials_inherited": False,
                         "peak_memory_mib": None, "peak_memory_reason": "not_instrumented",
                         "heartbeats_limit": request.get("max_heartbeats"), "heartbeats_used": None,
                         "heartbeats_unit": "maxHeartbeats: 1000 raw IO.getNumHeartbeats",
                         "heartbeats_scope": "candidate elaboration, including the elaborator's own kernel checks, plus kernel "
                                             "replay; not bounded by or comparable with heartbeats_limit, in total or in either phase",
                         "heartbeats_used_reason": "not_reported_by_engine", "model_tool_calls": 0}
    container_name = None
    if isolation == "macos_sandbox":
        if platform.system() != "Darwin":
            raise ValueError("macos_sandbox requires macOS")
        profile = scratch / "sandbox.sb"
        profile.write_text(sandbox_profile(extra_read, str(scratch)), encoding="utf-8")
        cmd = ["/usr/bin/sandbox-exec", "-f", str(profile), environment["lean_binary"],
               "--run", str(source_engine), str(input_file), str(output_file)]
        resource_evidence["profile_hash"] = file_hash(profile)
        resource_evidence["profile_scope"] = "development; system reads permitted; no enforced CPU quota or memory cap"
    elif isolation == "docker":
        image = policy.get("docker_image")
        if not is_content_digest(image):
            raise ValueError("Docker policy needs an immutable docker_image content digest")
        cpu_quota = policy.get("cpu_quota", 1)
        if not isinstance(cpu_quota, (int, float)) or cpu_quota <= 0:
            raise ValueError("Docker policy needs a positive cpu_quota; 0 would lift the CPU limit")
        container_name = f"lean-compression-{os.getpid()}-{time.time_ns()}"
        # CPU-time backstop for a timed-out container that outlives `docker rm -f`; under the
        # CPU quota it cannot bind before the wall limit.
        cpu_seconds = math.ceil(wall_seconds * max(1, cpu_quota)) + 1
        # No host directory is writable. /job is a tmpfs, larger than the file-size limit so that an oversized result
        # stops Lean at that limit before /job fills, and run-lean returns the result and the evidence on stdout.
        cmd = ["docker", "run", "--rm", "--name", container_name, "--network", "none",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--pids-limit", "64", "--memory", f"{policy.get('memory_mib', 4096)}m",
               "--memory-swap", f"{policy.get('memory_mib', 4096)}m",
               "--ulimit", f"fsize={FILE_SIZE_LIMIT}:{FILE_SIZE_LIMIT}", "--ulimit", f"cpu={cpu_seconds}:{cpu_seconds + 1}",
               "--log-driver", "none", "--cpus", str(cpu_quota),
               "--tmpfs", "/job:rw,noexec,nosuid,size=320m",
               "--mount", f"type=bind,source={input_file},target=/job/request.json,readonly",
               "--mount", f"type=bind,source={source_engine.parent},target=/engine,readonly",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", image,
               "/opt/eval/run-lean", f"/engine/{source_engine.name}", "/job/request.json", "/job/result.json"]
        clean_env = docker_cli_env()
        resource_evidence.update(memory_enforced=True, cpu_quota=cpu_quota, cpu_seconds_limit=cpu_seconds, image=image)
        # From here on an interrupted attempt removes this container before its scratch directory.
        containers.append(container_name)
    else:
        raise ValueError("No unsandboxed candidate mode is supported")
    try:
        stdout_file, stderr_file = scratch / "stdout.log", scratch / "stderr.log"
        timed_out = False
        with stdout_file.open("wb") as stdout_stream, stderr_file.open("wb") as stderr_stream:
            # The checker inherits its worker's claim locks: the claim stays live while the check runs,
            # even if the worker is killed.
            proc = subprocess.Popen(cmd, cwd=scratch, env=clean_env, stdout=stdout_stream,
                                    stderr=stderr_stream, start_new_session=True, pass_fds=claim_locks.get(),
                                    preexec_fn=(lambda: _limits(policy.get("memory_mib", 4096), wall_seconds)))
            try:
                proc.wait(timeout=wall_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            except BaseException:
                # Its own session never sees Ctrl-C, so stop it here; execute_engine then removes its container.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
                raise
        cleanup_error = None
        if container_name and (timed_out or proc.returncode != 0):
            cleanup_error = _remove_container(container_name)
            if cleanup_error:
                # The container may still be running; its bind source is kept rather than deleted under it.
                resource_evidence["scratch_retained_reason"] = "container_cleanup_unconfirmed"
        # What Lean printed precedes the files run-lean returns; a container that may still run returns none.
        printed, returned = None, None
        if isolation == "docker" and not (timed_out or cleanup_error):
            printed, returned = _container_output(stdout_file)
        def bounded_log(path, size=None):
            size = path.stat().st_size if size is None else size
            with path.open("rb") as stream:
                value = stream.read(min(size, 4 * 1024 * 1024)).decode("utf-8", errors="replace")
            return value, size, size > 4 * 1024 * 1024
        # A Docker client whose log reaches the file-size limit exits with an error, not a signal: a resource limit too.
        client_capped = isolation == "docker" and proc.returncode != 0 and max(
            stdout_file.stat().st_size, stderr_file.stat().st_size) >= FILE_SIZE_LIMIT
        stdout, stdout_size, stdout_truncated = bounded_log(stdout_file, printed)
        stderr, stderr_size, stderr_truncated = bounded_log(stderr_file)
        resource_evidence.update(stdout_bytes=stdout_size, stderr_bytes=stderr_size,
                                 stdout_log_truncated=stdout_truncated, stderr_log_truncated=stderr_truncated)
        if isolation == "docker" and (timed_out or cleanup_error):
            # run-lean was killed before returning its copies, or the container may still be running: whatever
            # ends the stream may have been printed by the evaluated code, so nothing returned is read.
            resource_evidence["peak_memory_reason"] = "not_read_after_timeout_or_unconfirmed_cleanup"
        elif isolation == "docker":
            # run-lean copies these cgroup files once Lean exits and returns them. Memory evidence never decides
            # an outcome: an absent, unreadable, oversized or malformed value stays null.
            def evidence(name):
                try:
                    return _returned(returned, name) or ""
                except ValueError:
                    return ""
            def count(text):
                # A 64-bit counter has at most 20 digits, which also keeps the MiB division below finite.
                digits = text.strip()
                return int(digits) if text.isascii() and digits.isdigit() and len(digits) <= 20 else None
            if (peak := count(evidence("peak_memory_bytes"))) is not None:
                resource_evidence.update(peak_memory_mib=peak / (1024 * 1024), peak_memory_reason=None,
                                         peak_memory_scope="cgroup high-water mark of all memory charged to the container, page cache included")
            memory_stat = dict(line.split(" ", 1) for line in evidence("memory_stat").splitlines() if " " in line)
            # Bytes (pgmajfault: faults) under cgroup v2 names; v1 calls the first three rss, cache and mapped_file.
            resource_evidence["memory_stat_at_exit"] = {key: count(memory_stat.get(key, memory_stat.get(v1, ""))) for key, v1 in (
                ("anon", "rss"), ("file", "cache"), ("file_mapped", "mapped_file"), ("pgmajfault", "pgmajfault"))}
        if cleanup_error:
            # A daemon that cannot remove the container cannot vouch for this attempt or for what it still writes.
            return {"validity_status": "infrastructure_error", "metric_status": "unavailable",
                    "failure_reason": "container_cleanup_unconfirmed", "error": cleanup_error,
                    "stdout": stdout, "stderr": stderr,
                    "resources": {**resource_evidence, "elapsed_seconds": time.monotonic() - started}}
        if timed_out:
            return {"validity_status": "verification_timeout" if mode == "validate" else "extractor_timeout",
                    "metric_status": "unavailable", "stdout": stdout, "stderr": stderr,
                    "resources": {**resource_evidence, "elapsed_seconds": time.monotonic() - started}}
        # In Docker, run-lean reports a signalled Lean as 128+N: SIGKILL (OOM), SIGXCPU, SIGXFSZ.
        resource_exit = client_capped or proc.returncode in (137, 152, 153, -signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ)
        try:
            output = _returned(returned, "result.json") if isolation == "docker" else _read_output(output_file)
            result = json.loads(output, parse_float=_finite, parse_constant=_finite) if output is not None else None
            canonical(result)  # a value no record can store, such as a lone surrogate, is unreadable like NaN
            problem = "Lean did not produce its result record" if output is None else (
                _malformed(result) if isinstance(result, dict) else "Lean's result record is not a JSON object")
        except (OSError, ValueError, RecursionError) as exc:
            problem = str(exc)
        if resource_exit:
            # Stopped by a resource limit: whatever result record it left, partial or whole, is not a verdict.
            result = {"validity_status": "verification_resource_limit" if mode == "validate" else "extractor_resource_limit",
                      "metric_status": "unavailable",
                      "error": problem or "Lean was stopped by a resource limit after writing its result record, which is not used"}
        elif problem:
            result = {"validity_status": "infrastructure_error", "metric_status": "unavailable", "error": problem}
        # Only successful checks report heartbeats; older engines report none.
        if result.get("heartbeats"):
            resource_evidence.update(heartbeats_used=result["heartbeats"]["max_heartbeats_units"]["total"],
                                     heartbeats_used_reason=None)
        return {**result, "process_returncode": proc.returncode, "stdout": stdout, "stderr": stderr,
                "resources": {**resource_evidence, "elapsed_seconds": time.monotonic() - started}}
    except (OSError, ValueError) as exc:
        return {"validity_status": "infrastructure_error", "metric_status": "unavailable",
                "error": str(exc), "resources": resource_evidence}


def evaluate_proof(task: dict, proof: str, environment: dict, engine: Path, config: dict) -> dict:
    validation = config["validation"]
    initial_engine_hash = file_hash(engine)
    request = {key: task.get(key, default) for key, default in
               (("imports", []), ("target_name", "evaluation_target"), ("binders", ""),
                ("proposition", ""), ("context", ""), ("forbidden_declarations", []))}
    request.update(proof=proof, max_heartbeats=validation["max_heartbeats"],
                   permitted_axioms=validation["permitted_axioms"])
    if task.get("import_specs"):
        request["import_specs"] = task["import_specs"]
    checked = execute_engine(request, environment, engine, validation, mode="validate",
                             wall_seconds=validation["wall_seconds"])
    base = {"validity_status": checked.get("validity_status", "infrastructure_error"),
            "metric_status": "unavailable", "metrics": None, "validation": checked,
            "engine_hash": initial_engine_hash, "environment_hash": environment["environment_hash"],
            "boundary_policy": config["environment"]["boundary_policy"],
            "boundary_policy_hash": digest(config["environment"]["boundary_policy"]),
            "task_hash": digest(task), "task_input_hash": task_input_hash(task),
            "verification_request_hash": verification_request_hash(task), "source_hash": digest(proof), "evaluated_at": now()}
    diagnostic = checked.get("diagnostics", {})
    # Statement auxiliaries, such as the statement's matcher, come from the fixed signature, not the proof.
    # Engines that do not report them, or report null, leave them in the count.
    auxiliaries = checked.get("statement_auxiliaries")
    helpers = [name for name in checked.get("candidate_declarations", ()) if name not in (auxiliaries or ())]
    base["supporting_diagnostics"] = {
        "proof_nodes": diagnostic.get("proof_nodes"),
        "helper_count": max(0, len(helpers) - 1) if "candidate_declarations" in checked else None,
        "helper_count_scope": "generated auxiliary declarations" + (
            ", including any statement auxiliaries" if auxiliaries is None else " except statement auxiliaries")
            + "; local have/let source remains included in W_B",
        "tactic_syntax_kinds": [k for k in diagnostic.get("syntax_kinds", []) if "tactic" in k.lower()],
    }
    if file_hash(engine) != initial_engine_hash:
        return {**base, "validity_status": "infrastructure_error", "failure_reason": "engine_changed_during_verification"}
    if base["validity_status"] != "success":
        return base
    extracted = execute_engine(request, environment, engine, validation, mode="extract",
                               wall_seconds=config["extraction"]["wall_seconds"])
    base["extraction"] = extracted
    if file_hash(engine) != initial_engine_hash:
        return {**base, "validity_status": "infrastructure_error", "failure_reason": "engine_changed_during_extraction",
                "failure_phase": "extraction"}
    status = extracted.get("validity_status", "infrastructure_error")
    if status not in EXTRACTOR_OUTCOMES and outcome_class(status) == "incomplete":
        # The checker could not measure a valid proof: an incident to retry, like one in validation, never a final record.
        return {**base, "validity_status": status, "failure_phase": "extraction"}
    if extracted.get("metric_status") != "available":
        base["metric_unavailable_reason"] = extracted.get("metric_error", extracted.get("failure_reason", extracted.get("error", extracted.get("validity_status", "extraction_failed"))))
        return base
    tokens = extracted["source_tokens"]
    graph = extracted["nodes"]
    metrics = score_graph(graph, extracted["root"], tokens["W_S"], tokens["W_B"])
    base.update(metric_status=metrics["metric_status"], metrics=metrics,
                graph_hash=digest(graph), source_token_policy_hash=digest({"version": "original_syntax_v1", "engine_hash": base["engine_hash"]}))
    nodes = list(graph.values()) if isinstance(graph, dict) else graph
    primitives = sorted(n["name"] for n in nodes if n.get("primitive"))
    base["primitive_manifest"] = primitives
    base["primitive_manifest_hash"] = digest(primitives)
    base["primitive_manifest_scope"] = "reachable closure; boundary policy and environment define shared corpus boundary"
    return base


def evaluate_run(run: Path, engine: Path, *, version: str = "v1", limit: int | None = None,
                 reference_checks: Path | None = None) -> dict:
    directory = evaluation_directory(run, version)
    manifest = load_run(run)
    require_implemented_policy(manifest["config"])
    if (manifest["config"]["validation"].get("isolation") != "docker"
            and manifest["model_snapshot"] != "reference_fixture"):
        raise ValueError("Model-generated proofs require Docker isolation; native mode is limited to explicitly trusted reference fixtures")
    tasks = {t["task_id"]: t for t in manifest["tasks"]}
    # Workers of other code, or another claim protocol (io.CLAIM_PROTOCOL), are refused below.
    evaluation_policy = {"engine_hash": file_hash(engine), "evaluator_source_hash": evaluator_hash(), "config_hash": manifest["config_hash"],
                         "run_hash": manifest["run_hash"], "version": version, "claim_protocol": CLAIM_PROTOCOL}
    if reference_checks is not None:
        # A checker or extractor fix, the only way to evaluate with an engine other than the frozen one: this
        # version's anchors are the frozen proofs re-checked by its engine, the tasks whose frozen proof that
        # engine rejects or cannot measure are bound unanchored, and its policy names the engine replaced.
        evaluation_policy.update(rechecked_anchors(manifest, reference_checks, evaluation_policy["engine_hash"]),
                                 frozen_engine_hash=manifest["reference_engine_hash"])
    elif manifest.get("reference_engine_hash") not in (None, evaluation_policy["engine_hash"]):
        raise ValueError("Engine differs from the one that verified this run's frozen references; re-check the frozen "
                         "reference proofs with it into a new directory and pass that as --reference-checks")
    elif manifest.get("engine_hash") not in (None, evaluation_policy["engine_hash"]):
        raise ValueError("Engine differs from the one frozen with this run (manifest engine_hash); evaluate with that engine")
    # Resume only a version this engine, code and configuration wrote; rescored versions are never re-evaluated.
    existing = read_json(directory / "policy.json") if (directory / "policy.json").exists() else None
    # A policy.json here was published by a concurrent worker after the check above; write_json compares it.
    if existing is None and any(path.name != "policy.json" for path in directory.glob("*.json")):
        raise ValueError(f"Evaluation version {version} holds records without a policy; evaluate into a new version")
    def refused(existing):
        existing = existing if isinstance(existing, dict) else {}
        binding = ("reference_policy_hash", "reference_anchors", "unanchored_reference_tasks")
        unbound = lambda policy: {key: value for key, value in policy.items() if key not in binding}
        origin = ("rescore" if existing.get("kind") == "rescore" else "other reference checks"
                  if unbound(existing) == unbound(evaluation_policy) else "another engine, code or configuration")
        return ValueError(f"Evaluation version {version} was written by {origin}; evaluate into a new version")
    if existing is not None and existing != evaluation_policy:
        raise refused(existing)
    try:
        write_json(directory / "policy.json", evaluation_policy)
    except FileExistsError:
        # Another worker published its policy after the check above.
        if not (directory / "policy.json").is_file():
            raise
        raise refused(read_json(directory / "policy.json")) from None
    if reference_checks is not None:
        # Logged only once this binding is the version's policy, so a worker refused above logs none. Every worker
        # that binds or resumes the version logs it, once, before any record: one killed in between leaves it to the next.
        append_event(run / "amendments.jsonl", {
            "kind": "reference_recheck", "version": version, "engine_hash": evaluation_policy["engine_hash"],
            "reference_policy_hash": evaluation_policy["reference_policy_hash"],
            "reference_anchors": evaluation_policy["reference_anchors"],
            "unanchored_reference_tasks": evaluation_policy["unanchored_reference_tasks"],
            "frozen_engine_hash": manifest["reference_engine_hash"],
            "frozen_reference_policy_hash": manifest["reference_policy_hash"],
            "frozen_reference_anchors": manifest["reference_anchors"]}, once=True)
    # A worker killed after publishing a record may have left its event unlogged and write_json's temporary file.
    # Records published after they are listed here are logged by live workers, or found by the next run.
    published, leftovers = {path.stem for path in directory.glob("*.json")}, temporary_files(directory)
    unlogged = published - logged_evaluations(run, version)
    evaluated = lambda attempt_id, record: {"event": "attempt_evaluated", "attempt_id": attempt_id, "evaluation_version": version,
                                           "validity_status": record["validity_status"], "metric_status": record["metric_status"]}
    processed = incidents = 0
    for slot in manifest["slots"]:
        if limit is not None and processed + incidents >= limit:
            break
        output = directory / f"{slot['attempt_id']}.json"
        genpath = generation_path(run, slot["attempt_id"])
        stale = leftovers.get(output.name, ())
        if output.exists() and (slot["attempt_id"] in unlogged or stale):
            # Finish the work of a worker killed after publishing this record, under its claim: delete the temporary
            # files only that claim's holder writes, and log the record's event once.
            with claim(output) as held:
                if held:
                    for path in stale:
                        path.unlink(missing_ok=True)
                    append_event(run / "events.jsonl", evaluated(slot["attempt_id"], read_json(output)), once=True)
            continue
        if output.exists():
            # A worker killed after writing this record leaves its claim; remove it unless a process holds it.
            remove_abandoned_claim(output)
            continue
        if not genpath.exists():
            continue
        with claim(output) as held:
            # Another worker is checking this slot, or recorded it after the check above.
            if not held or output.exists():
                continue
            for path in stale:
                path.unlink(missing_ok=True)  # left by a worker killed before publishing this record
            generation = verified_generation(read_json(genpath), slot)
            status = generation.get("generation_status", "completed")
            if status not in GENERATION_OUTCOMES and status not in PROVIDER_STATUSES:
                # A generation label is never a verdict; only Lean can make an attempt succeed.
                raise ValueError(f"Unrecognized generation_status {status!r} for attempt {slot['attempt_id']}")
            if status != "completed":
                result = {"validity_status": status, "metric_status": "unavailable", "metrics": None,
                          "scheduled_outcome_incomplete": status in PROVIDER_STATUSES}
            else:
                proof = adapt_response(generation["raw_response"], manifest["config"]["generation"]["response_adapter"])
                result = evaluate_proof(tasks[slot["task_id"]], proof, manifest["environment"], engine, manifest["config"])
                if result["engine_hash"] != evaluation_policy["engine_hash"]:
                    raise RuntimeError("Engine changed during this run; create a new evaluation version")
                result["adapted_response"] = proof
                if outcome_class(result["validity_status"]) == "incomplete":
                    # A checker failure is not the proof's verdict. Keep its evidence and leave the slot pending.
                    append_event(directory / "incidents.jsonl", {"event": "checker_incident", "attempt_id": slot["attempt_id"],
                                 "generation_hash": digest(generation), **result})
                    incidents += 1
                    continue
                if result["validity_status"] == "success":
                    # Same task's signature must remain constant across all measured attempts.
                    metrics = result.get("metrics")
                    if metrics and metrics["metric_status"] == "available":
                        signature = {"W_S": metrics["W_S"], "U_S_decimal": metrics["U_S_decimal"]}
                        write_json(directory / "signatures" / f"{digest(slot['task_id'])}.json", signature)
            result.update(attempt_id=slot["attempt_id"], generation_hash=digest(generation), generation=generation)
            write_json(output, result)
            append_event(run / "events.jsonl", evaluated(slot["attempt_id"], result))
            processed += 1
    result = {"new_evaluations": processed, "evaluation_version": version, "checker_incidents": incidents}
    if evaluation_policy.get("unanchored_reference_tasks"):
        result["unanchored_reference_tasks"] = sorted(evaluation_policy["unanchored_reference_tasks"])
    if kept := kept_claims(directory):
        # Slots no worker checks until an operator deletes these (README, Parallel workers).
        result["kept_claims"] = [str(path) for path in kept]
    return result
