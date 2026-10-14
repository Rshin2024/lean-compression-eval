"""Freeze the eight-model OM100 cohort after the independent preparation audit.

This creates frozen runs and a proposed launch plan, never launch_ready.json.
The root operator must validate the resulting inputs before releasing the carrier.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

WORK = Path(__file__).resolve().parent
BASE = WORK.parents[1]
REPO = BASE / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.dataset import answer_access_errors, readiness_errors, validate_manifest, validate_references
from lean_compression.environment import load_environment
from lean_compression.io import digest, evaluator_hash, file_hash, measurement_hash, read_json, read_jsonl, write_json
from lean_compression.store import create_run, load_reference_checks, load_run, require_reference_anchors
from run_full_evaluation import MODELS, CONDITIONS, validate_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True, help="Final independently audited task JSONL; generation enabled")
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--reference-checks", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True, help="Root review record binding exact final inputs")
    args = parser.parse_args()
    tasks, refs = read_jsonl(args.tasks), read_jsonl(args.references)
    audit = read_json(args.audit)
    if (audit.get("all_100_ready") is not True or audit.get("tasks_sha256") != file_hash(args.tasks) or
            audit.get("references_sha256") != file_hash(args.references)):
        raise ValueError("Preparation audit is missing or does not bind these final task/reference files")
    expected = {f"OM100-{i:03d}" for i in range(1, 101)}
    if len(tasks) != 100 or {t["task_id"] for t in tasks} != expected:
        raise ValueError("The selected100 IDs must be preserved exactly")
    errors = (validate_manifest(tasks) + validate_references(tasks, refs) +
              readiness_errors(tasks) + answer_access_errors(tasks, refs))
    if errors:
        raise ValueError("; ".join(errors))
    if any(t.get("model_generation_enabled") is not True for t in tasks):
        raise ValueError("A question still has generation disabled")
    if any(t["split"] != "main" for t in tasks) or len({t["family_id"] for t in tasks}) != 19:
        raise ValueError("This exploratory selection requires all100 tasks and19 source families")
    env = load_environment(args.environment)
    if env.get("hash_mode") != "full" or env.get("source_exclusion_audit") != {"lean_sources": 0, "git_directories": 0}:
        raise ValueError("The source-stripped fully hashed runtime is required")
    template = read_json(WORK / "config.preparation.json")
    template["validation"]["docker_image"] = env["docker_image"]
    checked, policy = load_reference_checks(args.reference_checks)
    require_reference_anchors(tasks, checked, policy, env,
                              {"measurement_hash": measurement_hash(template)}, measurable=True)
    engine_hash = file_hash(REPO / "lean/Evaluator.lean")
    if policy["engine_hash"] != engine_hash or policy["evaluator_source_hash"] != evaluator_hash():
        raise ValueError("Reference checking used different evaluator code")
    entries = []
    for model in sorted(MODELS):
        original = read_json(REPO / "configs/models" / (model + ".json"))
        config = copy.deepcopy(template)
        config["generation"] = copy.deepcopy(original["generation"])
        for key in ("response_adapter", "timeout_seconds", "context_byte_cap", "max_output_tokens", "reasoning_setting"):
            config["generation"][key] = template["generation"][key]
        config["pricing"] = original["pricing"]
        config["cost_tracking_required"] = True
        config["dataset"]["selection_kind"] = "user_selected_100_exploratory_full_evaluation"
        config["dataset"]["formal_250_question_preregistered_study"] = False
        config["dataset"]["premise_retrieval"] = "reference_informed_prior_interfaces_same_for_all_models_no_proof_bodies"
        config_path = WORK / "frozen-configs" / (model + ".json")
        write_json(config_path, config)
        directory = "om100-" + model + "-high"
        path = REPO / "runs" / directory
        if (path / "manifest.json").exists():
            manifest = load_run(path)
            if (manifest["config_hash"] != digest(config) or manifest["task_manifest_hash"] != digest(tasks) or
                    manifest["environment"]["environment_hash"] != env["environment_hash"]):
                raise ValueError("A prior frozen run differs; existing evidence preserved")
        else:
            manifest = create_run(path, tasks, config, env, split="main", model=model, development=True,
                                  reference_records=checked, reference_policy=policy, engine_hash=engine_hash)
        entries.append({"model": model, "directory": directory, "run_hash": manifest["run_hash"],
                        "manifest_sha256": file_hash(path / "manifest.json")})
    plan = {"ready": True, "experiment": "openai_math_100_v1", "source_hash": evaluator_hash(),
            "engine_hash": engine_hash, "generation_workers_per_model": 8, "checker_workers": 48,
            "preparation_audit": audit, "preparation_audit_sha256": file_hash(args.audit),
            "runs": entries, "conditions": CONDITIONS, "reference_checks": str(args.reference_checks.resolve()),
            "environment_sha256": file_hash(args.environment), "tasks_sha256": file_hash(args.tasks),
            "reference_policy_hash": digest(policy), "scheduled_attempts": 9600}
    validate_plan(plan)
    write_json(WORK / "launch_proposed.json", plan)
    print(json.dumps({"frozen_models": 8, "tasks_per_model": 100, "scheduled_attempts": 9600,
                      "status": "frozen_not_released", "plan": str(WORK / "launch_proposed.json")}))


if __name__ == "__main__":
    main()
