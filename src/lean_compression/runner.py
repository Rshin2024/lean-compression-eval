"""Fresh-process verification and separately budgeted metric extraction."""
from __future__ import annotations

import json
import math
import os
import platform
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .io import append_event, digest, evaluator_hash, file_hash, now, read_json, task_input_hash, verification_request_hash, write_json
from .metrics import score_graph
from .prompts import adapt_response
from .store import generation_path, load_run


def sandbox_profile(read_paths: list[str], scratch: str) -> str:
    """macOS development isolation; production uses container cgroups/read-only mounts.

    System frameworks may read OS caches outside /System. Deny user/private paths
    instead of pretending this development profile enforces a full filesystem allowlist.
    """
    quote = lambda p: json.dumps(str(Path(p).resolve()))
    protected = ["/Users", "/Volumes", "/private/tmp", "/private/var/folders"]
    rules = ["(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)"]
    rules += [f"(deny file-read-data (subpath {quote(p)}))" for p in protected]
    rules += [f"(allow file-read-data (subpath {quote(p)}))" for p in [*read_paths, scratch]]
    rules += [f"(allow file-write* (subpath {quote(scratch)}))", '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules)


def _limits(memory_mib: int, wall_seconds: float):
    import resource
    # CPU-seconds cap is a backstop, not a CPU quota. Docker enforces the latter.
    resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(wall_seconds) + 1, math.ceil(wall_seconds) + 2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    if platform.system() == "Linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory_mib * 1024 * 1024,) * 2)


def execute_engine(request: dict, environment: dict, engine: Path, policy: dict,
                   *, mode: str, wall_seconds: float) -> dict:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="lean-compression-") as directory:
        scratch = Path(directory).resolve()
        input_file, output_file = scratch / "request.json", scratch / "result.json"
        write_json(input_file, {**request, "mode": mode})
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
                             "heartbeats_used_reason": "pinned_driver_does_not_export_counter", "model_tool_calls": 0}
        container_name = None
        if isolation == "macos_sandbox":
            if platform.system() != "Darwin":
                raise ValueError("macos_sandbox requires macOS")
            profile = scratch / "sandbox.sb"
            profile.write_text(sandbox_profile(extra_read, str(scratch)))
            cmd = ["/usr/bin/sandbox-exec", "-f", str(profile), environment["lean_binary"],
                   "--run", str(source_engine), str(input_file), str(output_file)]
            resource_evidence["profile_hash"] = file_hash(profile)
            resource_evidence["profile_scope"] = "development; system reads permitted; no enforced CPU quota or memory cap"
        elif isolation == "docker":
            image = policy.get("docker_image")
            if not image or not ("@sha256:" in image or image.startswith("sha256:")):
                raise ValueError("Docker policy needs an immutable docker_image content digest")
            container_name = f"lean-compression-{os.getpid()}-{time.time_ns()}"
            cmd = ["docker", "run", "--rm", "--name", container_name, "--network", "none",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--pids-limit", "64", "--memory", f"{policy.get('memory_mib', 4096)}m",
                   "--memory-swap", f"{policy.get('memory_mib', 4096)}m",
                   "--ulimit", "fsize=268435456:268435456", "--log-driver", "none",
                   "--cpus", str(policy.get("cpu_quota", 1)),
                   "--mount", f"type=bind,source={scratch},target=/job",
                   "--mount", f"type=bind,source={source_engine.parent},target=/engine,readonly",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", image,
                   "/opt/eval/run-lean", "/engine/Evaluator.lean", "/job/request.json", "/job/result.json"]
            clean_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
            # Docker needs its user socket/config location; it is not passed into the container.
            if os.environ.get("HOME"):
                clean_env["HOME"] = os.environ["HOME"]
            resource_evidence.update(memory_enforced=True, cpu_quota=policy.get("cpu_quota", 1), image=image)
        else:
            raise ValueError("No unsandboxed candidate mode is supported")
        try:
            stdout_file, stderr_file = scratch / "stdout.log", scratch / "stderr.log"
            timed_out = False
            with stdout_file.open("wb") as stdout_stream, stderr_file.open("wb") as stderr_stream:
                proc = subprocess.Popen(cmd, cwd=scratch, env=clean_env, stdout=stdout_stream,
                                        stderr=stderr_stream, start_new_session=True,
                                        preexec_fn=(lambda: _limits(policy.get("memory_mib", 4096), wall_seconds)))
                try:
                    proc.wait(timeout=wall_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            if container_name and (timed_out or proc.returncode != 0):
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10)
            def bounded_log(path):
                size = path.stat().st_size
                with path.open("rb") as stream:
                    value = stream.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
                return value, size, size > 4 * 1024 * 1024
            stdout, stdout_size, stdout_truncated = bounded_log(stdout_file)
            stderr, stderr_size, stderr_truncated = bounded_log(stderr_file)
            resource_evidence.update(stdout_bytes=stdout_size, stderr_bytes=stderr_size,
                                     stdout_log_truncated=stdout_truncated, stderr_log_truncated=stderr_truncated)
            peak_file = scratch / "peak_memory_bytes"
            if isolation == "docker" and peak_file.exists():
                peak = peak_file.read_text().strip()
                if peak.isdigit():
                    resource_evidence.update(peak_memory_mib=int(peak) / (1024 * 1024), peak_memory_reason=None)
            if timed_out:
                return {"validity_status": "verification_timeout" if mode == "validate" else "extractor_timeout",
                        "metric_status": "unavailable", "stdout": stdout, "stderr": stderr,
                        "resources": {**resource_evidence, "elapsed_seconds": time.monotonic() - started}}
            resource_exit = proc.returncode in (137, -signal.SIGKILL, -signal.SIGXCPU, -signal.SIGXFSZ)
            result = read_json(output_file) if output_file.exists() else {
                "validity_status": ("verification_resource_limit" if mode == "validate" else "extractor_resource_limit") if resource_exit else "infrastructure_error", "metric_status": "unavailable",
                "error": "Lean did not produce its result record"}
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
    base["supporting_diagnostics"] = {
        "proof_nodes": diagnostic.get("proof_nodes"),
        "helper_count": max(0, len(checked["candidate_declarations"]) - 1) if "candidate_declarations" in checked else None,
        "helper_count_scope": "generated auxiliary declarations; local have/let source remains included in W_B",
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
        base["metric_unavailable_reason"] = "engine_changed_during_extraction"
        return base
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


def evaluate_run(run: Path, engine: Path, *, version: str = "v1", limit: int | None = None) -> dict:
    if not version or "/" in version or ".." in version:
        raise ValueError("Evaluation version must be one safe directory name")
    manifest = load_run(run)
    if (manifest["config"]["validation"].get("isolation") != "docker"
            and manifest["model_snapshot"] != "reference_fixture"):
        raise ValueError("Model-generated proofs require Docker isolation; native mode is limited to explicitly trusted reference fixtures")
    tasks = {t["task_id"]: t for t in manifest["tasks"]}
    directory = run / "evaluations" / version
    evaluation_policy = {"engine_hash": file_hash(engine), "evaluator_source_hash": evaluator_hash(), "config_hash": manifest["config_hash"],
                         "run_hash": manifest["run_hash"], "version": version}
    write_json(directory / "policy.json", evaluation_policy)
    processed = 0
    for slot in manifest["slots"]:
        if limit is not None and processed >= limit:
            break
        output = directory / f"{slot['attempt_id']}.json"
        genpath = generation_path(run, slot["attempt_id"])
        if output.exists() or not genpath.exists():
            continue
        generation = read_json(genpath)
        status = generation.get("generation_status", "completed")
        if status != "completed":
            result = {"validity_status": status, "metric_status": "unavailable", "metrics": None,
                      "scheduled_outcome_incomplete": status in ("transport_error", "model_version_mismatch", "provider_failure")}
        else:
            proof = adapt_response(generation["raw_response"], manifest["config"]["generation"]["response_adapter"])
            result = evaluate_proof(tasks[slot["task_id"]], proof, manifest["environment"], engine, manifest["config"])
            if result["engine_hash"] != evaluation_policy["engine_hash"]:
                raise RuntimeError("Engine changed during this run; create a new evaluation version")
            result["adapted_response"] = proof
            if result["validity_status"] == "success":
                # Same task's signature must remain constant across all measured attempts.
                metrics = result.get("metrics")
                if metrics and metrics["metric_status"] == "available":
                    signature = {"W_S": metrics["W_S"], "U_S_decimal": metrics["U_S_decimal"]}
                    write_json(directory / "signatures" / f"{digest(slot['task_id'])}.json", signature)
        result.update(attempt_id=slot["attempt_id"], generation_hash=digest(generation), generation=generation)
        write_json(output, result)
        append_event(run / "events.jsonl", {"event": "attempt_evaluated", "attempt_id": slot["attempt_id"],
                     "evaluation_version": version, "validity_status": result["validity_status"],
                     "metric_status": result["metric_status"]})
        processed += 1
    return {"new_evaluations": processed, "evaluation_version": version}
