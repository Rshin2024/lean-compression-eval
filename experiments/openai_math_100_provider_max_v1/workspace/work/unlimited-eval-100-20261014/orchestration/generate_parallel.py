"""Generation-only high-concurrency supervisor for a separately frozen OM100 rerun.

No Azure operations or proof verification occur here. Existing provider functions
retain their captures, attempt claims, cost accounting, and unknown-outcome holds.
A shared, hash-validated manifest cache avoids loading a 67 MB manifest per call.
Every slot is its own deterministic shard so an uncertain call cannot strand
other unattempted slots. This module makes no calls when imported or validated.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import re
import stat
import sys
import time

SESSION = Path(__file__).resolve().parents[1]
BASE = SESSION.parents[1]
REPO = BASE / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lean_compression import providers, store
from lean_compression.io import digest, evaluator_hash, file_hash, task_input_hash

MODELS = {"gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra",
          "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}
CONDITIONS = ["standard", "mathematical_compression", "source_concision"]
ORIGINAL_LOAD = store.load_run


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".partial")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)


def stamp(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class ManifestCache:
    def __init__(self):
        self.items = {}

    def add(self, run, expected):
        run = Path(run).resolve()
        path = run / "manifest.json"
        before = stamp(path)
        if file_hash(path) != expected:
            raise ValueError("Manifest file differs from launch hash")
        manifest = ORIGINAL_LOAD(run)
        if stamp(path) != before:
            raise ValueError("Manifest changed while loading")
        self.items[run] = (before, manifest, expected)
        return manifest

    def __call__(self, run):
        run = Path(run).resolve()
        before, manifest, expected = self.items[run]
        if stamp(run / "manifest.json") != before:
            raise ValueError("Frozen manifest changed while generation was running")
        return manifest

    def rehash(self):
        for run, (before, manifest, expected) in self.items.items():
            if stamp(run / "manifest.json") != before or file_hash(run / "manifest.json") != expected:
                raise ValueError("Frozen manifest failed periodic full hash validation")


def prompt_map(manifest):
    return {(s["task_id"], s["condition"], s["sample_index"]):
            (s["prompt_hash"], digest(s["messages"])) for s in manifest["slots"]}


def validate(plan):
    if plan.get("ready") is not True or plan.get("new_independent_run") is not True:
        raise ValueError("A separately validated independent launch plan is required")
    if plan["experiment"] == "openai_math_100_v1":
        raise ValueError("Never resume or modify the prior run through this controller")
    if plan["source_hash"] != evaluator_hash() or plan["engine_hash"] != file_hash(REPO / "lean/Evaluator.lean"):
        raise ValueError("Frozen core or Lean evaluator changed")
    if len(plan["runs"]) != 8 or {e["model"] for e in plan["runs"]} != MODELS:
        raise ValueError("Expected exactly the eight authorized models")
    cache, runs, all_ids, tasks = ManifestCache(), [], set(), None
    for entry in plan["runs"]:
        run = (REPO / "runs" / entry["directory"]).resolve()
        baseline = (REPO / "runs" / entry["baseline_directory"]).resolve()
        if run.parent != (REPO / "runs").resolve() or run == baseline:
            raise ValueError("New run must be a distinct direct child of runs")
        if baseline.parent != (REPO / "runs").resolve():
            raise ValueError("Baseline run escaped runs directory")
        m = cache.add(run, entry["manifest_sha256"])
        if m["run_hash"] != entry["run_hash"] or m["model_snapshot"] != entry["model"]:
            raise ValueError("Run identity differs from launch")
        cfg, gen = m["config"], m["config"]["generation"]
        if (cfg["experiment"] != plan["experiment"] or cfg["conditions"] != CONDITIONS or
                cfg["samples_per_goal_condition"] != 4 or gen["reasoning_setting"] != "high" or
                gen["response_adapter"] != "single_outer_wrapper_v2" or not cfg.get("pricing") or
                not cfg.get("cost_tracking_required")):
            raise ValueError("New cohort differs from authorized questions, effort, samples, or pricing")
        if gen.get("max_output_tokens") != entry["max_output_tokens"]:
            raise ValueError("Output setting differs from the explicit per-model launch policy")
        if m["evaluator_source_hash"] != plan["source_hash"] or m["engine_hash"] != plan["engine_hash"]:
            raise ValueError("Run is bound to another evaluator")
        ids = {s["attempt_id"] for s in m["slots"]}
        if len(m["slots"]) != 1200 or len(ids) != 1200 or ids & all_ids:
            raise ValueError("Invalid or repeated attempt identifiers")
        all_ids |= ids
        if file_hash(baseline / "manifest.json") != entry["baseline_manifest_sha256"]:
            raise ValueError("Baseline changed")
        old = ORIGINAL_LOAD(baseline)
        if ids & {s["attempt_id"] for s in old["slots"]} or prompt_map(m) != prompt_map(old):
            raise ValueError("Attempt identifiers must be new and all prompts unchanged")
        current = {t["task_id"]: task_input_hash(t) for t in m["tasks"]}
        if (set(current) != {f"OM100-{i:03d}" for i in range(1, 101)} or
                any(t.get("model_generation_enabled") is not True for t in m["tasks"])):
            raise ValueError("Expected all 100 enabled questions")
        if tasks is not None and current != tasks:
            raise ValueError("Models receive different questions")
        if current != {t["task_id"]: task_input_hash(t) for t in old["tasks"]}:
            raise ValueError("Questions changed from the baseline")
        tasks = current
        runs.append((entry, run, m))
    parallel = plan.get("parallelism", {})
    initial, maximum = parallel.get("initial_per_model", 32), parallel.get("maximum_per_model", 128)
    total = parallel.get("maximum_total", 1024)
    if not all(type(v) is int for v in (initial, maximum, total)) or not 1 <= initial <= maximum <= 1200 or not 1 <= total <= 2048:
        raise ValueError("Invalid explicit concurrency policy")
    return cache, runs


def last_attempt_event(run, attempt):
    found = None
    path = run / "transport.jsonl"
    if path.exists():
        with path.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("attempt_id") == attempt:
                    found = event
    return found or {}


def retry_status(run, attempt):
    if (run / "inflight" / (attempt + ".json")).exists():
        return None
    event = last_attempt_event(run, attempt)
    if event.get("event") != "no_model_outcome":
        return None
    if permanent_quota_rejection(event):
        return None
    status = (event.get("last_error") or {}).get("http_status")
    # Only explicit rejected/not-executed HTTP responses qualify. Unknown or
    # disconnected outcomes retain their marker and are never released here.
    return status if status in {408, 429, 500, 502, 503, 504, 529} else None


def permanent_quota_rejection(event):
    detail = event.get("last_error") or {}
    body = str(detail.get("body", "")).lower()
    return detail.get("http_status") == 429 and any(value in body for value in (
        "insufficient_quota", "enforced_spend_limit", "billing_hard_limit_reached", "credit balance is too low"))


def configure_environment(plan):
    workspace = plan.get("anthropic_workspace_id")
    if workspace is not None:
        if not isinstance(workspace, str) or not re.fullmatch(r"wrkspc_[A-Za-z0-9]+", workspace):
            raise ValueError("Malformed explicit Anthropic workspace selection")
        previous = os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if previous and previous != workspace:
            raise ValueError("Inherited Anthropic workspace differs from frozen launch selection")
        os.environ["ANTHROPIC_WORKSPACE_ID"] = workspace
    endpoint = plan.get("runtime_preflight", {}).get("docker_host")
    if endpoint is None:
        return
    expected = "unix:///tmp/lce-om100-unlimited-docker-20261014.sock"
    if endpoint != expected or not stat.S_ISSOCK(Path(endpoint[len("unix://"):]).stat().st_mode):
        raise ValueError("Docker readiness endpoint must be the task's existing Unix socket tunnel")
    os.environ["DOCKER_HOST"] = endpoint
    os.environ.pop("DOCKER_CONTEXT", None)


def sanitized(error):
    message = str(error)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID"):
        if value := os.environ.get(key):
            message = message.replace(value, "[REDACTED]")
    return message[-1500:]


def worker_function(plan):
    descriptor = plan.get("generation_adapter")
    if descriptor is None:
        return providers.generate_run
    path = (SESSION / descriptor["path"]).resolve()
    if not path.is_relative_to(SESSION) or file_hash(path) != descriptor["sha256"]:
        raise ValueError("External generation adapter must be pinned inside this new session")
    spec = importlib.util.spec_from_file_location("provider_max_generation_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(module, "configure"):
        module.configure(plan, SESSION)
    if hasattr(module, "preflight"):
        module.preflight()
    module.generate_run.reserve_dispatch = getattr(module, "reserve_dispatch", None)
    module.generate_run.rate_snapshot = getattr(getattr(module, "_rates", None), "snapshot", lambda: {})
    return module.generate_run


def supervise(plan, cache, runs):
    # This substitution changes no source files, settings, prompts or evidence.
    # The cache has already verified canonical run hashes and file SHA256s.
    providers.load_run = store.load_run = cache
    generate = worker_function(plan)
    cfg = plan.get("parallelism", {})
    maximum = cfg.get("maximum_per_model", 128)
    ceiling = cfg.get("maximum_total", 1024)
    limits = {entry["model"]: cfg.get("initial_per_model", 32) for entry, _, _ in runs}
    last_throttle = dict.fromkeys(limits, 0.0)
    retries, next_try, blocked, blocked_models, running, seen = {}, {}, {}, {}, {}, set()
    started = time.time()
    last_raise = started
    last_hash = started
    pending = {(entry["model"], index): (run, m, slot) for entry, run, m in runs for index, slot in enumerate(m["slots"])}
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, max(soft, ceiling * 6 + 1024)), hard))
    with cf.ThreadPoolExecutor(max_workers=ceiling) as pool:
        while True:
            now = time.time()
            for future in [f for f in running if f.done()]:
                key = running.pop(future)
                run, m, slot = pending[key]
                attempt = slot["attempt_id"]
                try:
                    result = future.result()
                    failure = None
                except Exception as error:
                    result, failure = None, sanitized(error)
                if (run / "generations" / (attempt + ".json")).exists():
                    seen.add(key)
                    if failure:
                        # The core stops on a model-version mismatch or malformed
                        # response even when it preserved that response as evidence.
                        blocked_models[key[0]] = {"attempt_id": attempt, "error": failure, "at": now}
                elif (status := retry_status(run, attempt)) is not None and retries.get(key, 0) < 12:
                    retries[key] = retries.get(key, 0) + 1
                    next_try[key] = now + min(300, 10 * 2 ** min(retries[key], 5))
                    if status in {429, 529}:
                        model = key[0]
                        # Do not cancel running requests; only reduce new dispatch.
                        if now - last_throttle[model] >= 10:
                            limits[model] = max(1, limits[model] // 2)
                            last_throttle[model] = now
                else:
                    blocked[key] = {"attempt_id": attempt, "at": now, "error": failure,
                                    "result": result, "inflight": (run / "inflight" / (attempt + ".json")).exists()}
                    event = last_attempt_event(run, attempt)
                    if (event.get("last_error") or {}).get("http_status") in {400, 401, 403, 404, 422} or permanent_quota_rejection(event):
                        blocked_models[key[0]] = {"attempt_id": attempt, "error": "Provider configuration/access rejected", "at": now}
            for key, (run, m, slot) in pending.items():
                if key not in seen and (run / "generations" / (slot["attempt_id"] + ".json")).exists():
                    seen.add(key)
            if now - last_raise >= 20:
                quota_snapshot = getattr(generate, "rate_snapshot", lambda: {})()
                for model in limits:
                    if getattr(generate, "reserve_dispatch", None) and quota_snapshot.get(model, {}).get("last_sequence", -1) < 0:
                        continue  # Observe at least the first provider headers before ramping.
                    if now - last_throttle[model] >= 60:
                        limits[model] = min(maximum, max(limits[model] + 1, limits[model] * 2))
                last_raise = now
            if now - last_hash >= 60:
                cache.rehash()
                if evaluator_hash() != plan["source_hash"]:
                    raise RuntimeError("Frozen core source changed while generating")
                last_hash = now
            active = {model: sum(k[0] == model for k in running.values()) for model in limits}
            running_keys = set(running.values())
            # Rotate among models on every dispatch round for fair initial ramp.
            candidates = {model: iter([(k, v) for k, v in pending.items() if k[0] == model and k not in seen
                and model not in blocked_models and k not in blocked and k not in running_keys and now >= next_try.get(k, 0)]) for model in limits}
            launching = True
            while launching and len(running) < ceiling:
                launching = False
                for model in limits:
                    if active[model] >= limits[model] or len(running) >= ceiling:
                        continue
                    item = next(candidates[model], None)
                    if item is None:
                        continue
                    key, (run, m, slot) = item
                    gate = getattr(generate, "reserve_dispatch", None)
                    if gate and not gate(run, slot):
                        continue
                    running[pool.submit(generate, run, limit=1, shard_index=key[1], shard_count=len(m["slots"]))] = key
                    active[model] += 1
                    launching = True
            counts = {model: sum(k[0] == model for k in seen) for model in limits}
            complete = len(seen) == 9600 and not running
            save(SESSION / "generation-status.json", {"state": "complete" if complete else "needs_attention" if blocked or blocked_models else "running",
                "pid": os.getpid(), "started_at": started, "updated_at": now, "generated": counts,
                "generation_total": len(seen), "scheduled": 9600, "active_workers": len(running),
                "active_per_model": active, "limits_per_model": limits,
                "blocked_attempts": {f"{k[0]}/{k[1]}": v for k, v in blocked.items()},
                "blocked_models": blocked_models,
                "rate_budgets": getattr(generate, "rate_snapshot", lambda: {})(),
                "retry_attempts": {f"{k[0]}/{k[1]}": v for k, v in retries.items()}})
            if complete:
                return
            if not running and all(k in seen or k in blocked or k[0] in blocked_models for k in pending):
                return
            cf.wait(running, timeout=1, return_when=cf.FIRST_COMPLETED) if running else time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=SESSION / "launch_ready.json")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    configure_environment(plan)
    cache, runs = validate(plan)
    if args.validate_only:
        providers.load_run = store.load_run = cache
        worker_function(plan)
        print(json.dumps({"validated": True, "attempts": 9600, "models": 8, "no_api_calls": True}))
        return
    with (SESSION / "generation-supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        supervise(plan, cache, runs)


if __name__ == "__main__":
    main()
