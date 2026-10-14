"""Resume the validated OM100 run, with local API calls and Azure-only Lean checks.

The credential carrier launches this file only after launch_ready.json is published.
Credentials remain in this local process tree. No runtime settings or frozen inputs
are changed here. Each paid-call shard and each Lean attempt has its own claim.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WORK = Path(__file__).resolve().parent
BASE = WORK.parent.parent
REPO = BASE / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.io import digest, evaluator_hash, file_hash, task_input_hash
from lean_compression.store import load_run

MODELS = {"gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra",
          "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}
CONDITIONS = ["standard", "mathematical_compression", "source_concision"]
SSH_CONFIG = WORK / "runtime/azure/ssh-config"
HOST = "lce-om100-azure"
CLOUD_BASE = Path("/home/leanrunner/om100-eval")
CLOUD_WORK = CLOUD_BASE / "work/full-eval-100"
CLOUD_REPO = CLOUD_BASE / "outputs/lean-compression-eval"


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".partial")
    with tmp.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def log(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps({"time": time.time(), **value}) + "\n")


def safe_error(error):
    message = str(error)
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if value := os.environ.get(name):
            message = message.replace(value, "[REDACTED]")
    return message[-4000:]


def ids(directory):
    return {p.stem for p in Path(directory).glob("*.json") if re.fullmatch(r"[0-9a-f]{64}", p.stem)}


def counts(runs, relative):
    result = {}
    for entry, run, manifest in runs:
        scheduled = {slot["attempt_id"] for slot in manifest["slots"]}
        found = ids(run / relative)
        if found - scheduled:
            raise ValueError(f"Unscheduled records found in {entry['model']}/{relative}")
        result[entry["model"]] = len(found & scheduled)
    return result


def validate_plan(plan):
    if plan.get("ready") is not True or plan.get("experiment") != "openai_math_100_v1":
        raise ValueError("Launch is not validated")
    if plan.get("source_hash") != evaluator_hash():
        raise ValueError("Python evaluator changed after validation")
    if plan.get("engine_hash") != file_hash(REPO / "lean/Evaluator.lean"):
        raise ValueError("Lean evaluator changed after validation")
    entries = plan.get("runs", [])
    if len(entries) != 8 or {e["model"] for e in entries} != MODELS:
        raise ValueError("Expected exactly the eight configured models")
    if plan.get("preparation_audit", {}).get("all_100_ready") is not True:
        raise ValueError("All100 question audit missing")
    all_tasks = None
    runs = []
    for entry in entries:
        name = entry["directory"]
        if not re.fullmatch(r"om100-[a-z0-9.-]+-high", name):
            raise ValueError("Unexpected run path")
        run = REPO / "runs" / name
        manifest = load_run(run)
        if manifest["run_hash"] != entry["run_hash"] or manifest["model_snapshot"] != entry["model"]:
            raise ValueError("Frozen manifest differs from launch plan")
        config = manifest["config"]
        generation = config["generation"]
        if (manifest["evaluator_source_hash"] != plan["source_hash"] or
                manifest["engine_hash"] != plan["engine_hash"]):
            raise ValueError("Frozen evaluator identity differs")
        if (config["experiment"] != plan["experiment"] or config["conditions"] != CONDITIONS or
                config["samples_per_goal_condition"] != 4 or
                generation["reasoning_setting"] != "high" or generation["max_output_tokens"] != 8192 or
                generation["response_adapter"] != "single_outer_wrapper_v2" or
                not config.get("cost_tracking_required") or not config.get("pricing")):
            raise ValueError("Frozen protocol differs from authorized cohort")
        if len(manifest["slots"]) != 1200 or len({s["attempt_id"] for s in manifest["slots"]}) != 1200:
            raise ValueError("Invalid frozen schedule")
        tasks = {t["task_id"]: task_input_hash(t) for t in manifest["tasks"]}
        if set(tasks) != {f"OM100-{i:03d}" for i in range(1, 101)}:
            raise ValueError("Question set differs from all100 selection")
        if any(t.get("model_generation_enabled") is not True for t in manifest["tasks"]):
            raise ValueError("A question has not been approved for generation")
        if all_tasks is None:
            all_tasks = tasks
        elif tasks != all_tasks:
            raise ValueError("Models do not receive identical questions")
        if len(manifest.get("reference_anchors") or {}) != 100:
            raise ValueError("Missing verified reference anchors")
        runs.append((entry, run, manifest))
    return runs


def access_check(runs):
    results = []
    for provider in ("openai", "anthropic"):
        models = {m["model_snapshot"] for _, _, m in runs if m["config"]["generation"]["provider"] == provider}
        key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
        key = os.environ.get(key_name, "").strip()
        if not key:
            raise ValueError(f"{key_name} missing from credential carrier")
        headers = ({"Authorization": f"Bearer {key}"} if provider == "openai" else
                   {"x-api-key": key, "anthropic-version": "2023-06-01"})
        if provider == "anthropic" and os.environ.get("ANTHROPIC_WORKSPACE_ID"):
            headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
        url = "https://api.openai.com/v1/models" if provider == "openai" else "https://api.anthropic.com/v1/models?limit=100"
        available = set()
        for _ in range(20):
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
                body = json.load(response)
            available.update(r["id"] for r in body.get("data", []) if isinstance(r.get("id"), str))
            if provider == "openai" or not body.get("has_more"):
                break
            cursor = body.get("last_id")
            if not isinstance(cursor, str):
                raise ValueError("Anthropic model pagination has no cursor")
            url = "https://api.anthropic.com/v1/models?limit=100&after_id=" + urllib.parse.quote(cursor, safe="")
        result = {"provider": provider, "authenticated": True, "models": {m: m in available for m in sorted(models)}}
        results.append(result)
        if models - available:
            save(WORK / "access-check.json", results)
            raise ValueError(f"Configured models not available for {provider}: {sorted(models - available)}")
    save(WORK / "access-check.json", results)


def command(args, *, timeout=180):
    result = subprocess.run([str(x) for x in args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {safe_error(result.stderr or result.stdout)}")
    return result.stdout


def remote(script, *, timeout=180):
    return command(["/usr/bin/ssh", "-F", SSH_CONFIG, HOST, script], timeout=timeout)


def sync(source, target, *, immutable=False):
    args = ["rsync", "-az", "--exclude=.*", "--exclude=*.claim", "--exclude=*.retained",
            "-e", shlex.join(["/usr/bin/ssh", "-F", str(SSH_CONFIG)])]
    if immutable:
        args.append("--ignore-existing")
    command([*args, source, target], timeout=300)


def launch_cloud(plan):
    # This file and launch plan contain no credential values. The private key directory is never copied.
    remote("mkdir -p " + shlex.quote(str(CLOUD_WORK)))
    for path in (Path(__file__).resolve(), WORK / "launch_ready.json"):
        sync(str(path), f"{HOST}:{CLOUD_WORK}/{path.name}")
    for entry in plan["runs"]:
        target = CLOUD_REPO / "runs" / entry["directory"]
        remote("mkdir -p " + shlex.quote(str(target / "generations")))
        source = REPO / "runs" / entry["directory"] / "manifest.json"
        sync(str(source), f"{HOST}:{target}/manifest.json", immutable=True)
        actual = remote("sha256sum " + shlex.quote(str(target / "manifest.json"))).split()[0]
        if actual != file_hash(source):
            raise ValueError("Cloud manifest differs; existing evidence preserved")
    checked_at = time.time()
    remote(f"python3 {shlex.quote(str(CLOUD_WORK / 'run_full_evaluation.py'))} --cloud --validate-only")
    # Explicitly detach and close inherited SSH descriptors; shell backgrounding
    # can keep the SSH channel open for the entire lifetime of the checker.
    launch = ("import subprocess; "
              f"log=open({str(CLOUD_WORK / 'cloud-controller.log')!r},'ab'); "
              f"p=subprocess.Popen(['python3',{str(CLOUD_WORK / 'run_full_evaluation.py')!r},'--cloud'],"
              f"cwd={str(CLOUD_REPO)!r},stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,"
              "start_new_session=True,close_fds=True); print(p.pid)")
    remote("python3 -c " + shlex.quote(launch))
    for _ in range(30):
        try:
            state = json.loads(remote("cat " + shlex.quote(str(CLOUD_WORK / "cloud-status.json"))))
            if (state.get("updated_at", 0) >= checked_at - 10 and
                    state.get("state") in {"running", "checked_complete"} and not state.get("errors")):
                save(WORK / "cloud-status.json", state)
                return
        except (RuntimeError, ValueError):
            pass
        time.sleep(2)
    raise ValueError("Cloud checker did not acknowledge a healthy validated launch; no paid calls started")


def shard_is_complete(manifest, run, index, count):
    return all((run / "generations" / (slot["attempt_id"] + ".json")).is_file()
               for slot in manifest["slots"][index::count])


def shard_record_count(manifest, run, index, count):
    return sum((run / "generations" / (slot["attempt_id"] + ".json")).is_file()
               for slot in manifest["slots"][index::count])


def retry_is_safe(run, manifest=None, index=0, count=1):
    # A read-only reconcile can recover already captured completions, but it never
    # releases unknown outcomes here. No marker means the provider rejected without a model outcome.
    owned = ({slot["attempt_id"] for slot in manifest["slots"][index::count]} if manifest else None)
    if any(owned is None or p.stem in owned for p in (run / "inflight").glob("*.json")):
        return False
    path = run / "transport.jsonl"
    if not path.exists():
        return False
    events = [json.loads(line) for line in path.read_text().splitlines()]
    events = [event for event in events if owned is None or event.get("attempt_id") in owned]
    if not events or events[-1].get("event") != "no_model_outcome":
        return False
    return (events[-1].get("last_error") or {}).get("http_status") in {408, 429, 500, 502, 503, 504, 529}


def local_main(plan, runs):
    if not os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        workspaces = BASE / "work/eval-session/workspaces.json"
        defaults = [v["id"] for v in read(workspaces).get("data", []) if v.get("name") == "Default"] if workspaces.exists() else []
        if len(defaults) == 1:
            os.environ["ANTHROPIC_WORKSPACE_ID"] = defaults[0]
    access_check(runs)
    generation_env = dict(os.environ)
    generation_env.pop("DOCKER_CONTEXT", None)
    generation_env["DOCKER_HOST"] = "ssh://" + HOST
    generation_env["PATH"] = str(WORK / "bin") + ":" + generation_env.get("PATH", "/usr/bin:/bin")
    launch_cloud(plan)
    count = plan.get("generation_workers_per_model", 8)
    if not 1 <= count <= 8:
        raise ValueError("Invalid worker limit")
    handles, blocked, retries, next_try = {}, {}, {}, {}
    started = time.time()
    last_sync = 0.0
    while True:
        generated = counts(runs, "generations")
        checked = counts(runs, "evaluations/v1")
        new_workers = 0
        parallelism_path = WORK / "parallelism.json"
        parallelism = read(parallelism_path).get("generation_active_per_model", {}) if parallelism_path.exists() else {}
        # Complete recorded generations are never requested again on restart.
        for entry, run, manifest in runs:
            model = entry["model"]
            active_limit = parallelism.get(model, count)
            if type(active_limit) is not int or not 1 <= active_limit <= count:
                raise ValueError("Generation active limit must remain between1 and frozen shard count")
            for index in range(count):
                name = f"{model}/{index}"
                if name in handles:
                    process, stream, before_count = handles[name]
                    if process.poll() is None:
                        continue
                    stream.close()
                    del handles[name]
                    if not shard_is_complete(manifest, run, index, count):
                        if process.returncode == 0 and shard_record_count(manifest, run, index, count) > before_count:
                            retries.pop(name, None)
                        elif retry_is_safe(run, manifest, index, count) and retries.get(name, 0) < 3:
                            retries[name] = retries.get(name, 0) + 1
                            next_try[name] = time.time() + 60 * retries[name]
                        else:
                            blocked[name] = {"exit_code": process.returncode, "at": time.time(),
                                             "log": f"generation-logs/{model}-{index}.log"}
                if name in blocked or time.time() < next_try.get(name, 0):
                    continue
                if shard_is_complete(manifest, run, index, count):
                    continue
                if new_workers >= 8:
                    continue
                if sum(key.startswith(model + "/") for key in handles) >= active_limit:
                    continue
                logdir = WORK / "generation-logs"
                logdir.mkdir(exist_ok=True)
                stream = (logdir / f"{model}-{index}.log").open("a")
                args = [str(REPO / "run-eval"), "generate", "--run", str(run),
                        "--shard-index", str(index), "--shard-count", str(count), "--limit", "4"]
                before_count = shard_record_count(manifest, run, index, count)
                process = subprocess.Popen(args, cwd=REPO, env=generation_env, stdout=stream,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                handles[name] = (process, stream, before_count)
                new_workers += 1
        sync_error = None
        if time.time() - last_sync >= 20:
            try:
                def one(item):
                    entry, run, _ = item
                    target = CLOUD_REPO / "runs" / entry["directory"]
                    if (run / "generations").is_dir():
                        sync(str(run / "generations") + "/", f"{HOST}:{target}/generations/", immutable=True)
                    (run / "evaluations/v1").mkdir(parents=True, exist_ok=True)
                    sync(f"{HOST}:{target}/evaluations/v1/", str(run / "evaluations/v1") + "/", immutable=True)
                with cf.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(one, runs))
                sync(f"{HOST}:{CLOUD_WORK}/cloud-status.json", str(WORK / "cloud-status.json"))
            except Exception as error:
                sync_error = safe_error(error)
            last_sync = time.time()
        complete = sum(checked.values()) == 9600 and sum(generated.values()) == 9600 and not handles
        cloud = read(WORK / "cloud-status.json") if (WORK / "cloud-status.json").exists() else {}
        snapshot = {"state": "checked_complete" if complete else "needs_attention" if blocked or cloud.get("errors") else "running",
                    "pid": os.getpid(), "started_at": started, "updated_at": time.time(),
                    "generated": generated, "checked": checked, "generation_total": sum(generated.values()),
                    "checked_total": sum(checked.values()), "scheduled": 9600, "active_generation_workers": len(handles),
                    "blocked_shards": blocked, "cloud": cloud, "sync_error": sync_error}
        save(WORK / "status.json", snapshot)
        if complete:
            return
        # A human/root repair may request restarting blocked shards; all immutable
        # records and in-flight markers are still checked by generate itself.
        resume = WORK / "resume_shards.json"
        if resume.exists():
            request = read(resume)
            for name in request.get("shards", []):
                if name in blocked and request.get("issued_at", 0) > blocked[name]["at"]:
                    blocked.pop(name)
                    retries.pop(name, None)
            resume.rename(WORK / f"resume_shards.applied-{time.time_ns()}.json")
        time.sleep(5)


def cloud_main(plan, runs):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if os.environ.get(key):
            raise ValueError("Cloud checker must not receive API credentials")
    capacity = plan.get("checker_workers", 24)
    if not 1 <= capacity <= 48:
        raise ValueError("Invalid cloud checker capacity")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0])
    requested_mib = max(m["config"]["validation"]["memory_mib"] for _, _, m in runs)
    if memory["MemTotal"] / 1024 < capacity * requested_mib + 16384:
        raise ValueError("Cloud memory does not fit checker capacity plus operating-system headroom")
    errors = {}
    started = time.time()
    def check(item):
        entry, run, manifest = item
        config = manifest["config"]
        worker_timeout = 4 * (config["validation"]["wall_seconds"] + config["extraction"]["wall_seconds"] + 60) + 120
        result = subprocess.run([str(REPO / "run-eval"), "evaluate", "--run", str(run), "--version", "v1", "--limit", "4"],
                                cwd=REPO, capture_output=True, text=True, timeout=worker_timeout)
        try:
            summary = json.loads(result.stdout)
        except ValueError:
            summary = {}
        return {"model": entry["model"], "exit_code": result.returncode, "summary": summary,
                "stderr": result.stderr[-4000:], "finished_at": time.time()}
    with cf.ThreadPoolExecutor(max_workers=capacity) as pool:
        pending = {}
        while True:
            generated = counts(runs, "generations")
            checked = counts(runs, "evaluations/v1")
            active = {e["model"]: sum(m == e["model"] for m in pending.values()) for e, _, _ in runs}
            while len(pending) < capacity:
                ready = [item for item in runs if item[0]["model"] not in errors and
                         generated[item[0]["model"]] - checked[item[0]["model"]] - active[item[0]["model"]] > 0]
                if not ready:
                    break
                item = max(ready, key=lambda v: (generated[v[0]["model"]] - checked[v[0]["model"]]) /
                                               (active[v[0]["model"]] + 1))
                model = item[0]["model"]
                pending[pool.submit(check, item)] = model
                active[model] += 1
            complete = sum(checked.values()) == 9600 and not pending
            save(WORK / "cloud-status.json", {"state": "checked_complete" if complete else "needs_attention" if errors else "running",
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
                    result = {"model": model, "exit_code": 1, "error": safe_error(error), "summary": {}}
                log(WORK / "cloud-events.jsonl", result)
                if result["exit_code"] or result["summary"].get("checker_incidents") or result["summary"].get("kept_claims"):
                    errors[model] = result
            resume = WORK / "resume_cloud_models.json"
            if resume.exists():
                request = read(resume)
                for model in request.get("models", []):
                    if model in errors and request.get("issued_at", 0) > errors[model].get("finished_at", 0):
                        errors.pop(model)
                resume.rename(WORK / f"resume_cloud_models.applied-{time.time_ns()}.json")
            if not pending:
                time.sleep(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloud", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    lock = (WORK / ("cloud-controller.lock" if args.cloud else "full-controller.lock")).open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    if not args.cloud and not args.validate_only and sys.platform == "darwin":
        # Keep local authenticated requests alive while the cloud checker runs.
        # The assertion ends automatically with this process; display sleep remains allowed.
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while True:
        try:
            plan = read(WORK / "launch_ready.json")
            runs = validate_plan(plan)
            if args.validate_only:
                print(json.dumps({"validated": True, "models": 8, "questions": 100, "attempts": 9600}))
                return
            (cloud_main if args.cloud else local_main)(plan, runs)
            return
        except Exception as error:
            save(WORK / ("cloud-failure.json" if args.cloud else "controller-failure.json"),
                 {"state": "needs_attention", "pid": os.getpid(), "updated_at": time.time(),
                  "error_type": type(error).__name__, "error": safe_error(error)})
            if args.validate_only:
                raise
            # Keep the user's inherited credentials in memory while an operator fixes
            # infrastructure; never automatically restart a failed paid-call session.
            repair = WORK / ("resume_cloud_controller.json" if args.cloud else "resume_controller.json")
            while not repair.exists():
                time.sleep(5)
            repair.rename(repair.with_name(repair.stem + f".applied-{time.time_ns()}.json"))


if __name__ == "__main__":
    main()
