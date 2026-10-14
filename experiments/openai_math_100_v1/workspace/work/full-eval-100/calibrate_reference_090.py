"""One trusted extraction-budget calibration; no generation or source edits."""
from pathlib import Path
import json
import materialize as m

base = Path(__file__).resolve().parent
out = base / "reference-calibration-090-900s"
out.mkdir(mode=0o700, exist_ok=True)
if (out / "result.json").exists():
    raise SystemExit("Calibration already recorded; no check repeated")
record = m.read_json(base / "materialized_v1/records/OM100-090/prepared.json")
task, reference = record["task"], record["reference"]
config = m.read_json(base / "config.preparation.json")
config["extraction"]["wall_seconds"] = 900
environment = m.load_environment(base / "environment.azure.json")
engine = m.REPO / "lean/Evaluator.lean"
m.write_private(out / "config.used.json", m.json_text(config))
m.write_private(out / "policy.json", m.json_text({
    "task_id": task["task_id"], "task_hash": m.digest(task), "proof_hash": m.digest(reference["proof"]),
    "config_hash": m.digest(config), "environment_hash": environment["environment_hash"],
    "engine_hash": m.file_hash(engine), "evaluator_source_hash": m.evaluator_hash(),
    "purpose": "Trusted reference only; original 180-second timeout preserved; final uniform budget pending",
    "generation_enabled": False,
}))
result = m.evaluate_proof(task, reference["proof"], environment, engine, config)
m.write_private(out / "result.json", m.json_text({"task_id": task["task_id"], "result": result}))
print(json.dumps({"task_id": task["task_id"], **{key: result.get(key) for key in
                 ("validity_status", "metric_status", "metric_unavailable_reason")},
                 "validation_resources": result.get("validation", {}).get("resources"),
                 "extraction_resources": result.get("extraction", {}).get("resources")}, indent=2), flush=True)
