"""Credential-free supervision/synchronization of the explicit thread-limit replay.

The original authenticated controller continues API generation. This process never
calls a provider, changes a frozen manifest, or reads credentials. v1 evidence is
retained separately. Start only after verification-launch-v2.json is validated.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

import run_full_evaluation as original

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
VERSION = "v2_threads1"


def validate_launch():
    plan = original.read(WORK / "verification-launch-v2.json")
    if plan.get("ready") is not True or plan.get("version") != VERSION:
        raise ValueError("Verification amendment is not ready")
    if set(plan.get("source_files", {})) != {
            "verify_runtime_v2.py", "supervise_verification_v2.py", "run_full_evaluation.py"}:
        raise ValueError("Verification launch must pin all three operational sources")
    for name, expected in plan["source_files"].items():
        if Path(name).name != name or original.file_hash(WORK / name) != expected:
            raise ValueError(f"Changed verification source: {name}")
    if original.file_hash(WORK / "runtime-amendment-v2.json") != plan["amendment_sha256"]:
        raise ValueError("Verification amendment changed")
    runs = original.validate_plan(original.read(WORK / "launch_ready.json"))
    if {entry["model"]: manifest["run_hash"] for entry, _, manifest in runs} != plan["source_run_hashes"]:
        raise ValueError("Original frozen runs changed")
    return plan, runs


def complete_counts(runs):
    return original.counts(runs, f"evaluations/{VERSION}")


def failed_result(result):
    summary = result.get("summary")
    return (result.get("exit_code") != 0 or not isinstance(summary, dict) or
            type(summary.get("new_evaluations")) is not int or summary["new_evaluations"] < 0 or
            type(summary.get("checker_incidents")) is not int or summary["checker_incidents"] != 0 or
            bool(summary.get("kept_claims")))


def sync_append_logs(source, destination):
    # JSON records are immutable; incident/event JSONL logs keep growing and must
    # not be frozen by --ignore-existing. Final backups separately verify hashes.
    original.command(["rsync", "-az", "--include=*/", "--include=*.jsonl", "--exclude=*",
                      "-e", shlex.join(["/usr/bin/ssh", "-F", str(original.SSH_CONFIG)]),
                      source, destination], timeout=300)


def cloud_main(plan, runs):
    if any(os.environ.get(key) for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")):
        raise ValueError("Cloud checker must not have API credentials")
    capacity = plan["checker_workers"]
    if type(capacity) is not int or not 1 <= capacity <= 48:
        raise ValueError("Invalid checker capacity")
    memory_kib = int(next(line for line in Path("/proc/meminfo").read_text().splitlines()
                          if line.startswith("MemTotal:")).split()[1])
    memory_mib = max(manifest["config"]["validation"]["memory_mib"] for _, _, manifest in runs)
    if memory_kib / 1024 < capacity * memory_mib + 16384:
        raise ValueError("Insufficient memory for checker capacity and OS headroom")
    for _, run, _ in runs:
        original.command(["python3", WORK / "verify_runtime_v2.py", "validate", "--run", run], timeout=600)
    errors = {}
    started = time.time()

    def check(item):
        entry, run, manifest = item
        config = manifest["config"]
        timeout = 4 * (config["validation"]["wall_seconds"] + config["extraction"]["wall_seconds"] + 60) + 120
        result = subprocess.run(["python3", str(WORK / "verify_runtime_v2.py"), "evaluate", "--run", str(run),
                                 "--limit", "4"], cwd=REPO, capture_output=True, text=True, timeout=timeout)
        try:
            summary = json.loads(result.stdout)
        except ValueError:
            summary = {}
        return {"model": entry["model"], "exit_code": result.returncode, "summary": summary,
                "stderr": result.stderr[-4000:], "finished_at": time.time()}

    with cf.ThreadPoolExecutor(max_workers=capacity) as pool:
        pending = {}
        while True:
            generated = original.counts(runs, "generations")
            checked = complete_counts(runs)
            active = {entry["model"]: sum(model == entry["model"] for model in pending.values())
                      for entry, _, _ in runs}
            while len(pending) < capacity:
                ready = [item for item in runs if item[0]["model"] not in errors and
                         generated[item[0]["model"]] - checked[item[0]["model"]] - active[item[0]["model"]] > 0]
                if not ready:
                    break
                item = max(ready, key=lambda row: (generated[row[0]["model"]] - checked[row[0]["model"]]) /
                           (active[row[0]["model"]] + 1))
                model = item[0]["model"]
                pending[pool.submit(check, item)] = model
                active[model] += 1
            complete = sum(checked.values()) == 9600 and not pending
            original.save(WORK / "cloud-verification-status.json", {
                "version": VERSION, "state": "checked_complete" if complete else "needs_attention" if errors else "running",
                "pid": os.getpid(), "started_at": started, "updated_at": time.time(), "capacity": capacity,
                "active_workers": len(pending), "generated": generated, "checked": checked,
                "checked_total": sum(checked.values()), "errors": errors})
            if complete:
                return
            done, _ = cf.wait(pending, timeout=5, return_when=cf.FIRST_COMPLETED) if pending else (set(), set())
            for future in done:
                model = pending.pop(future)
                try:
                    result = future.result()
                except Exception as error:
                    result = {"model": model, "exit_code": 1, "error": str(error)[-4000:], "summary": {}}
                original.log(WORK / "cloud-verification-events.jsonl", result)
                if failed_result(result):
                    errors[model] = result
            resume = WORK / "resume_verification_models.json"
            if resume.exists():
                request = original.read(resume)
                for model in request.get("models", []):
                    if model in errors and request.get("issued_at", 0) > errors[model].get("finished_at", 0):
                        errors.pop(model)
                resume.rename(WORK / f"resume_verification_models.applied-{time.time_ns()}.json")
            if not pending:
                time.sleep(5)


def local_main(plan, runs):
    started = time.time()
    while True:
        error = None
        try:
            def one(item):
                entry, run, _ = item
                target = original.CLOUD_REPO / "runs" / entry["directory"] / "evaluations" / VERSION
                local = run / "evaluations" / VERSION
                local.mkdir(parents=True, exist_ok=True)
                original.sync(f"{original.HOST}:{target}/", str(local) + "/", immutable=True)
                sync_append_logs(f"{original.HOST}:{target}/", str(local) + "/")
            with cf.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(one, runs))
            original.sync(f"{original.HOST}:{original.CLOUD_WORK}/cloud-verification-status.json",
                          str(WORK / "cloud-verification-status.json"))
        except Exception as exc:
            error = str(exc)[-4000:]
        cloud_path = WORK / "cloud-verification-status.json"
        cloud = original.read(cloud_path) if cloud_path.exists() else {}
        saved = complete_counts(runs)
        snapshot = {**cloud, "version": VERSION, "saved": saved, "saved_total": sum(saved.values()),
                    "sync_pid": os.getpid(), "sync_started_at": started, "sync_updated_at": time.time(), "sync_error": error}
        original.save(WORK / "verification-status.json", snapshot)
        if sum(saved.values()) == 9600 and cloud.get("state") == "checked_complete" and error is None:
            return
        time.sleep(20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    name = "cloud-verification" if args.cloud else "verification-sync"
    lock = (WORK / (name + ".lock")).open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    try:
        plan, runs = validate_launch()
        if args.validate_only:
            print(json.dumps({"validated": True, "version": VERSION, "models": len(runs)}))
            return
        (cloud_main if args.cloud else local_main)(plan, runs)
    except Exception as error:
        original.save(WORK / (name + "-failure.json"), {
            "state": "needs_attention", "pid": os.getpid(), "updated_at": time.time(),
            "error_type": type(error).__name__, "error": str(error)[-4000:]})
        raise


if __name__ == "__main__":
    main()
