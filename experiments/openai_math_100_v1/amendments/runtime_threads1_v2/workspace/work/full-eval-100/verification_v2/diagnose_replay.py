"""Four versioned diagnostics only; no production v2 records and no model calls."""
from concurrent.futures import ThreadPoolExecutor
import copy
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import materialize as m
from lean_compression.integrity import verified_generation
from lean_compression.outcomes import outcome_class
from lean_compression.prompts import adapt_response
from lean_compression.store import load_run


def main():
    env = m.load_environment(HERE / "environment.json")
    engine = m.REPO / "lean/Evaluator.lean"
    runs = sorted((m.REPO / "runs").glob("om100-*-high"))
    wanted = {"success": None, "parse_failure": None, "truncated_output": None}
    for run in runs:
        for path in sorted((run / "evaluations/v1").glob("*.json"), key=lambda p: p.stat().st_size):
            if path.name == "policy.json":
                continue
            record = m.read_json(path)
            status = record.get("validity_status")
            if status in wanted and wanted[status] is None:
                wanted[status] = (run, record["attempt_id"])
            if all(wanted.values()):
                break
        if all(wanted.values()):
            break
    if not all(wanted.values()):
        raise ValueError("Could not find the three requested preserved v1 diagnostic outcomes")
    wanted["original_pid_incident"] = (m.REPO / "runs/om100-gpt-6-luna-high",
        "476835adc79d2590578dac727d1e3b93f7c55abfbb22095def662fc4f65560a9")

    def check(item):
        label, (run, attempt) = item
        manifest = load_run(run)
        slot = next(slot for slot in manifest["slots"] if slot["attempt_id"] == attempt)
        path = run / "generations" / (attempt + ".json")
        generation = verified_generation(m.read_json(path), slot)
        original_hash = m.file_hash(path)
        config = copy.deepcopy(manifest["config"])
        config["validation"]["docker_image"] = env["docker_image"]
        task = next(task for task in manifest["tasks"] if task["task_id"] == slot["task_id"])
        if generation.get("generation_status", "completed") != "completed":
            result = {"validity_status": generation["generation_status"], "metric_status": "unavailable", "metrics": None}
            executed = False
        else:
            proof = adapt_response(generation["raw_response"], config["generation"]["response_adapter"])
            result = m.evaluate_proof(task, proof, env, engine, config)
            executed = True
        assert original_hash == m.file_hash(path)
        report = {"case": label, "attempt_id": attempt, "task_id": task["task_id"],
                  "source_run_hash": manifest["run_hash"], "source_generation_file_sha256": original_hash,
                  "verification_environment_hash": env["environment_hash"], "verification_config_hash": m.digest(config),
                  "no_official_v2_record": True, "checker_executed": executed, "result": result}
        m.write_private(HERE / "diagnostics" / (label + ".json"), m.json_text(report))
        if label in {"success", "parse_failure", "truncated_output"}:
            assert result["validity_status"] == label, report
        else:
            assert outcome_class(result["validity_status"]) != "incomplete", report
            assert "failed to create thread" not in result.get("validation", {}).get("stderr", ""), report
        print(label, result["validity_status"], result["metric_status"], flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(check, wanted.items()))


if __name__ == "__main__":
    main()
