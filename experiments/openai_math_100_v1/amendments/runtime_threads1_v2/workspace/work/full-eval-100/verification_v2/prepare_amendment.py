"""Index uniformly rechecked references and prepare (never activate) a portable v2 amendment."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
sys.path.insert(0, str(WORK))
import materialize as m
from lean_compression.io import measurement_hash, verification_request_hash
from lean_compression.store import load_run, without_graph
from lean_compression.metrics import SCORER_HASH


def main():
    launch = m.read_json(WORK / "launch_ready.json")
    environment = m.load_environment(HERE / "environment.json")
    config = m.read_json(HERE / "reference-config.json")
    directory = HERE / "reference-checks"
    policy = m.read_json(directory / "policy.json")
    expected = {"kind": "verify-references", "environment_hash": environment["environment_hash"],
                "measurement_hash": measurement_hash(config), "engine_hash": launch["engine_hash"],
                "evaluator_source_hash": launch["source_hash"], "scorer_hash": SCORER_HASH}
    if any(policy.get(key) != value for key, value in expected.items()):
        raise ValueError("Reverified reference policy differs from the prepared runtime")
    records, bindings = {}, {}
    for path in directory.glob("*.json"):
        if path.name == "policy.json":
            continue
        record = m.read_json(path)
        tid = record["task_id"]
        if tid in records or record["validity_status"] != "success" or record["metric_status"] != "available":
            raise ValueError("Every v2 reference must be unique, verified and measurable: " + tid)
        if record["environment_hash"] != environment["environment_hash"] or record["engine_hash"] != launch["engine_hash"]:
            raise ValueError("Reference runtime provenance mismatch: " + tid)
        bindings[tid] = {"path": path.relative_to(WORK).as_posix(), "sha256": m.file_hash(path),
                         "record_hash": m.digest(record)}
        records[tid] = without_graph(record)
    if len(records) != 100:
        raise ValueError("All 100 reference rechecks must finish before freezing an amendment")
    generations = []
    for entry in launch["runs"]:
        run = m.REPO / "runs" / entry["directory"]
        manifest = load_run(run)
        if manifest["run_hash"] != entry["run_hash"] or m.file_hash(run / "manifest.json") != entry["manifest_sha256"]:
            raise ValueError("Original generation manifest changed")
        if len(manifest["slots"]) != 1200 or len(manifest["tasks"]) != 100:
            raise ValueError("Generation schedule does not preserve the selected 100 × 3 × 4 design")
        for task in manifest["tasks"]:
            reference = records[task["task_id"]]
            if (reference["verification_request_hash"] != verification_request_hash(task)
                    or m.digest(reference["proof"]) != manifest["reference_proof_hashes"][task["task_id"]]):
                raise ValueError("Question inputs or hidden reference proof changed")
        generations.append({"model": entry["model"], "directory": entry["directory"], "run_hash": entry["run_hash"],
            "manifest_sha256": entry["manifest_sha256"], "slots_hash": m.digest(manifest["slots"]),
            "generation_config_hash": manifest["config_hash"],
            "generation_environment_hash": manifest["environment"]["environment_hash"]})
    if len(generations) != 8:
        raise ValueError("Eight original model runs are required")
    index = {"kind": "runtime_reference_index", "version": "v2_threads1", "record_count": 100,
             "source_policy": policy, "source_policy_hash": m.digest(policy),
             "environment_hash": environment["environment_hash"], "measurement_hash": measurement_hash(config),
             "engine_hash": launch["engine_hash"], "evaluator_source_hash": launch["source_hash"],
             "reference_records": records, "record_bindings": bindings}
    index_path = HERE / "reference-index.json"
    if index_path.exists() and m.read_json(index_path) != index:
        raise ValueError("Refusing to replace a different reference index")
    m.write_private(index_path, m.json_text(index))
    amendment = {"schema_version": 1, "version": "v2_threads1", "state": "frozen",
        "reason": "Lean 4.34.1 ignored LEAN_NUM_THREADS as a shell thread cap; one validation hit frozen 64-PID cgroup cap. Explicit -j1 applies uniformly to all replayed proofs.",
        "source_core_hash": launch["source_hash"], "engine_hash": launch["engine_hash"],
        "verifier_sha256": m.file_hash(WORK / "verify_runtime_v2.py"),
        "verification_environment": {"path": "verification_v2/environment.json", "sha256": m.file_hash(HERE / "environment.json")},
        "runtime_build": {"path": "verification_v2/runtime-build.json", "sha256": m.file_hash(HERE / "runtime-build.json")},
        "reference_index": {"path": "verification_v2/reference-index.json", "sha256": m.file_hash(index_path)},
        "generation_runs": generations, "scheduled_attempts": 9600, "references": 100,
        "uniform_full_replay": True, "original_manifests_and_attempt_ids_unchanged": True,
        "generation_settings_and_responses_unchanged": True, "new_model_calls": False,
        "v1_records_preserved": True, "verification_change": "Immutable runtime launcher adds -j1 before --run only",
        "limits": {key: config["validation"][key] for key in ("wall_seconds", "memory_mib", "cpu_quota", "max_heartbeats")},
        "extraction_wall_seconds": config["extraction"]["wall_seconds"], "pids_limit": 64,
        "reporting": "Use phase-aware adapter; no mixed v1/v2 primary comparisons and no v1 reference fallback"}
    target = WORK / "runtime-amendment-v2.proposed.json"
    m.write_private(target, m.json_text(amendment))
    print(m.json_text({"proposed": str(target), "amendment_hash": m.digest(amendment),
                      "references": 100, "original_slots": 9600, "activated": False}), flush=True)


if __name__ == "__main__":
    main()
