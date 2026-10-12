"""A frozen run manifest, immutable generations, and append-only evaluation revisions."""
from __future__ import annotations

import random
from pathlib import Path

from .io import digest, evaluator_hash, now, read_json, reference_matches_task, task_input_hash, write_json
from .prompts import CONDITIONS, messages, prompt_policy_hash


def schedule(tasks: list[dict], config: dict, environment: dict, model: str) -> list[dict]:
    conditions = config.get("conditions", list(CONDITIONS))
    if conditions != list(CONDITIONS):
        raise ValueError("Core evaluation requires exactly the three protocol conditions in order")
    count = config.get("samples_per_goal_condition", 4)
    if not isinstance(count, int) or count < 1:
        raise ValueError("samples_per_goal_condition must be a positive integer")
    slots = []
    for task in tasks:
        for condition in conditions:
            prompt = messages(task, condition, environment["lean_toolchain"])
            if len(prompt[1]["content"].encode()) > config["generation"]["context_byte_cap"]:
                raise ValueError(f"Task exceeds frozen prompt byte cap: {task['task_id']}")
            for sample in range(count):
                identity = {"experiment": config["experiment"],
                            "protocol_version": config["protocol_version"],
                            "task_id": task["task_id"], "task_hash": digest(task),
                            "model_snapshot": model, "condition": condition,
                            "sample_index": sample, "config_hash": digest(config),
                            "environment_hash": environment["environment_hash"]}
                slots.append({**identity, "attempt_id": digest(identity), "messages": prompt,
                              "prompt_hash": digest(prompt)})
    random.Random(config.get("schedule_seed", 20261011)).shuffle(slots)
    return [{**slot, "schedule_index": index} for index, slot in enumerate(slots)]


def create_run(path: Path, tasks: list[dict], config: dict, environment: dict, *,
               split: str, model: str, development: bool = False,
               reference_records: dict | None = None) -> dict:
    if path.exists() and (path / "manifest.json").exists():
        raise FileExistsError(f"Run already frozen: {path}; use the existing run to resume")
    if config["generation"].get("model_snapshot") not in (None, model):
        raise ValueError("Requested model differs from generation.model_snapshot in the protocol")
    if not development:
        from .dataset import readiness_errors
        if not config["generation"].get("snapshot_pinned"):
            raise ValueError("Production run requires an explicitly pinned model snapshot")
        if not config["generation"].get("settings_resolved"):
            raise ValueError("Explicitly resolve the model's reasoning and sampling settings before freezing")
        if split == "main" and not config.get("preregistration", {}).get("pilot_report_hash"):
            raise ValueError("Main runs require a preregistration containing the completed pilot report hash")
        if environment.get("hash_mode") != "full":
            raise ValueError("Production run requires full source and compiled environment hashes")
        if config["validation"].get("isolation") != "docker":
            raise ValueError("Production run requires the enforced Docker CPU and memory profile")
        pending = readiness_errors(tasks)
        if pending:
            raise ValueError("Verify/audit every task before freezing a production run: " + "; ".join(pending))
        if environment.get("runtime_backend") != "docker":
            raise ValueError("Production environment hashes must be collected inside the pinned runtime image")
        if environment.get("docker_image") != config["validation"].get("docker_image"):
            raise ValueError("Runtime image differs from the image whose environment was indexed")
    for key in ("lean_toolchain", "mathlib_commit"):
        if config["environment"].get(key) != environment.get(key):
            raise ValueError(f"Environment {key} differs from the frozen protocol")
    if config["validation"].get("isolation") == "docker":
        if environment.get("runtime_backend") != "docker" or environment.get("docker_image") != config["validation"].get("docker_image"):
            raise ValueError("Container runs must use environment evidence collected inside the same pinned image")
        if environment.get("source_exclusion_audit") != {"lean_sources": 0, "git_directories": 0}:
            raise ValueError("Container runtime must pass the forbidden-source exclusion audit")
    selected = [t for t in tasks if t["split"] == split]
    if not selected:
        raise ValueError(f"No tasks in split {split}")
    if not development:
        if reference_records is None:
            raise ValueError("Production freezing requires actual verified reference records for this environment")
        for task in selected:
            ref = reference_records.get(task["task_id"], {})
            if (ref.get("validity_status") != "success" or ref.get("metric_status") != "available"
                    or ref.get("environment_hash") != environment["environment_hash"]
                    or not reference_matches_task(ref, task)):
                raise ValueError(f"Missing or incompatible verified reference: {task['task_id']}")
    slots = schedule(selected, config, environment, model)
    manifest = {"schema_version": 1, "created_at": now(), "development": development,
                "split": split, "config": config, "environment": environment,
                "tasks": selected, "model_snapshot": model,
                "prompt_policy_hash": prompt_policy_hash(), "task_manifest_hash": digest(selected),
                "config_hash": digest(config), "evaluator_source_hash": evaluator_hash(), "scheduled_attempts": len(slots),
                "reference_records_hash": digest(reference_records) if reference_records is not None else None,
                "slots": slots}
    manifest["run_hash"] = digest(manifest)
    write_json(path / "manifest.json", manifest)
    return manifest


def load_run(path: Path) -> dict:
    value = read_json(path / "manifest.json")
    expected = value["run_hash"]
    unhashed = {k: v for k, v in value.items() if k != "run_hash"}
    if digest(unhashed) != expected:
        raise ValueError("Frozen run manifest was modified; create a versioned amendment/new run")
    return value


def generation_path(path: Path, attempt_id: str) -> Path:
    return path / "generations" / f"{attempt_id}.json"


def record_generation(path: Path, slot: dict, raw: str, **metadata) -> dict:
    value = {"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
             "created_at": now(), "raw_response": raw, "raw_response_hash": digest(raw), **metadata}
    target = generation_path(path, slot["attempt_id"])
    if target.exists():
        existing = read_json(target)
        if existing["raw_response_hash"] != digest(raw):
            raise FileExistsError(f"Attempt already has a different generation: {slot['attempt_id']}")
        return existing
    write_json(target, value)
    return value


def records(path: Path, evaluation_version: str = "v1") -> list[dict]:
    """Materialize all scheduled outcomes, including never generated/never evaluated slots."""
    manifest = load_run(path)
    output = []
    for slot in manifest["slots"]:
        identity = {k: v for k, v in slot.items() if k != "messages"}
        evaluation = path / "evaluations" / evaluation_version / f"{slot['attempt_id']}.json"
        genpath = generation_path(path, slot["attempt_id"])
        generation = read_json(genpath) if genpath.exists() else None
        if evaluation.exists():
            checked = read_json(evaluation)
            if checked.get("attempt_id") != slot["attempt_id"]:
                raise ValueError("Evaluation record identity does not match its scheduled attempt")
            for key in identity:
                if key in checked and checked[key] != identity[key]:
                    raise ValueError(f"Evaluation record changes frozen identity field {key}")
            output.append({**checked, **identity})
        else:
            status = "not_generated" if generation is None else generation.get("generation_status", "not_evaluated")
            if status == "completed":
                status = "not_evaluated"
            output.append({**identity, "validity_status": status, "metric_status": "unavailable",
                           "metrics": None, "generation": generation,
                           "scheduled_outcome_incomplete": status in ("not_generated", "not_evaluated", "transport_error", "model_version_mismatch", "provider_failure")})
    return output
