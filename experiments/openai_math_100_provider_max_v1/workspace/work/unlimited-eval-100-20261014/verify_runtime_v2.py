"""Uniform runtime replay with original generation identity and separate verification provenance.

Never calls a provider or changes a source manifest/generation/v1 result. Do not
use stock records()/analyze on these records: load_records preserves two phases.
"""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import sys

WORK = Path(__file__).resolve().parent
REPO = WORK.parents[1] / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
from lean_compression.environment import verify_environment
from lean_compression.integrity import require_implemented_policy, verified_generation
from lean_compression.io import (CLAIM_PROTOCOL, append_event, claim, digest, evaluator_hash, file_hash, kept_claims,
    measurement_hash, now, read_json, remove_abandoned_claim, temporary_files, verification_request_hash, write_json)
from lean_compression.metrics import SCORER_HASH, score_graph
from lean_compression.outcomes import GENERATION_OUTCOMES, PROVIDER_STATUSES, outcome_class
from lean_compression.prompts import adapt_response
from lean_compression.runner import evaluate_proof
from lean_compression.store import load_run, require_reference_anchors, without_graph

VERSION = "v2_threads1"
DEFAULT_AMENDMENT = WORK / "runtime-amendment-v2.json"


def artifact(relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Amendment artifacts must use relative paths within the operational workspace")
    resolved = (WORK / path).resolve()
    if not resolved.is_relative_to(WORK.resolve()):
        raise ValueError("Amendment artifact escapes the operational workspace")
    return resolved


def bound_artifact(descriptor: dict) -> dict:
    path = artifact(descriptor["path"])
    if file_hash(path) != descriptor["sha256"]:
        raise ValueError(f"Bound amendment artifact changed: {descriptor['path']}")
    return read_json(path)


def generation_identity(slot: dict) -> dict:
    return {key: value for key, value in slot.items() if key != "messages"}


def load_verification_context(run: Path, amendment_path: Path = DEFAULT_AMENDMENT) -> dict:
    run = Path(run)
    amendment = read_json(amendment_path)
    if amendment.get("version") != VERSION or amendment.get("state") != "frozen":
        raise ValueError("A frozen v2_threads1 runtime amendment is required")
    if amendment["verifier_sha256"] != file_hash(Path(__file__)):
        raise ValueError("External verifier changed after amendment freeze")
    if amendment["source_core_hash"] != evaluator_hash():
        raise ValueError("Frozen core Python package changed")
    engine = REPO / "lean/Evaluator.lean"
    if amendment["engine_hash"] != file_hash(engine):
        raise ValueError("Frozen Lean evaluator changed")
    manifest = load_run(run)
    bindings = [item for item in amendment["generation_runs"] if item["run_hash"] == manifest["run_hash"]]
    if len(bindings) != 1:
        raise ValueError("Source generation run is not bound exactly once by this amendment")
    binding = bindings[0]
    if binding["manifest_sha256"] != file_hash(run / "manifest.json") or binding["slots_hash"] != digest(manifest["slots"]):
        raise ValueError("Original generation manifest or schedule changed")
    if manifest["evaluator_source_hash"] != amendment["source_core_hash"] or manifest["engine_hash"] != amendment["engine_hash"]:
        raise ValueError("Source run did not freeze this core and Lean engine")
    environment = bound_artifact(amendment["verification_environment"])
    verify_environment(environment)
    build = bound_artifact(amendment["runtime_build"])
    delta = environment.get("verification_runtime_amendment", {})
    if (delta.get("lean_threads") != 1 or delta.get("source_environment_hash") != manifest["environment"]["environment_hash"]
            or delta.get("source_docker_image") != manifest["environment"]["docker_image"]):
        raise ValueError("Verification runtime is not the explicit -j1 amendment of the generation runtime")
    if (build.get("docker_image") != environment["docker_image"] or build.get("core_hash") != amendment["source_core_hash"]
            or build.get("wrapper_sha256") != delta.get("wrapper_sha256")
            or build.get("source_wrapper_sha256") != delta.get("source_wrapper_sha256")
            or build.get("lean_threads") != 1
            or build.get("new_layers", [])[:-1] != build.get("base_layers")):
        raise ValueError("Immutable runtime build evidence does not match the amendment")
    except_keys = {"docker_image", "environment_hash", "verification_runtime_amendment"}
    if ({key: value for key, value in environment.items() if key not in except_keys} !=
            {key: value for key, value in manifest["environment"].items() if key not in except_keys}):
        raise ValueError("Runtime amendment changed mathematical files or other environment evidence")
    config = copy.deepcopy(manifest["config"])
    config["validation"]["docker_image"] = environment["docker_image"]
    require_implemented_policy(config)
    for key in ("lean_toolchain", "mathlib_commit"):
        if config["environment"][key] != environment[key]:
            raise ValueError("Mathematical environment changed: " + key)
    index = bound_artifact(amendment["reference_index"])
    if (index.get("kind") != "runtime_reference_index" or index.get("version") != VERSION
            or index.get("evaluator_source_hash") != amendment["source_core_hash"]
            or index.get("engine_hash") != amendment["engine_hash"]):
        raise ValueError("Reference index is not from the uniform v2 verifier")
    references = index["reference_records"]
    if len(references) != 100 or set(references) != {task["task_id"] for task in manifest["tasks"]}:
        raise ValueError("Reference index must cover all 100 source questions")
    if index["source_policy_hash"] != digest(index["source_policy"]):
        raise ValueError("Reference policy hash mismatch")
    require_reference_anchors(manifest["tasks"], references, index["source_policy"], environment,
                              {"measurement_hash": measurement_hash(config)}, measurable=True)
    for tid, reference in references.items():
        if digest(reference["proof"]) != manifest["reference_proof_hashes"][tid]:
            raise ValueError("Reference proof changed: " + tid)
    policy = {"schema_version": 1, "kind": "phase_separated_runtime_verification", "version": VERSION,
        "amendment_hash": digest(amendment), "verifier_sha256": amendment["verifier_sha256"],
        "engine_hash": amendment["engine_hash"], "evaluator_source_hash": amendment["source_core_hash"],
        "claim_protocol": CLAIM_PROTOCOL, "source_run_hash": manifest["run_hash"],
        "source_manifest_sha256": binding["manifest_sha256"], "source_slots_hash": binding["slots_hash"],
        "generation_config_hash": manifest["config_hash"],
        "generation_environment_hash": manifest["environment"]["environment_hash"],
        "verification_config_hash": digest(config), "verification_environment_hash": environment["environment_hash"],
        "verification_docker_image": environment["docker_image"], "measurement_hash": measurement_hash(config),
        "reference_policy_hash": index["source_policy_hash"],
        "reference_index_sha256": amendment["reference_index"]["sha256"], "generation_replayed": True,
        "new_model_calls": False, "uniform_all_scheduled_attempts": True}
    return {"run": run, "manifest": manifest, "amendment": amendment, "policy": policy,
            "environment": environment, "config": config, "engine": engine, "reference_index": index,
            "references": references, "directory": run / "evaluations" / VERSION}


def load_reference_records(run: Path, amendment_path: Path = DEFAULT_AMENDMENT) -> dict:
    """Graph-free v2 reference records, never original-environment anchors."""
    return load_verification_context(run, amendment_path)["references"]


def assert_code(context):
    policy = context["policy"]
    if (file_hash(Path(__file__)) != policy["verifier_sha256"] or evaluator_hash() != policy["evaluator_source_hash"]
            or file_hash(context["engine"]) != policy["engine_hash"]):
        raise RuntimeError("Verification code changed while replaying; keep this version frozen")


def verify_result(context, slot, generation, result):
    status = generation.get("generation_status", "completed")
    if status != "completed":
        if result.get("validity_status") != status or result.get("metrics") is not None or result.get("metric_status") != "unavailable":
            raise ValueError("Noncompleted generation was relabeled as a proof outcome")
        return
    task = next(task for task in context["manifest"]["tasks"] if task["task_id"] == slot["task_id"])
    proof = adapt_response(generation["raw_response"], context["config"]["generation"]["response_adapter"])
    expected = {"source_hash": digest(proof), "task_hash": digest(task),
        "verification_request_hash": verification_request_hash(task),
        "environment_hash": context["environment"]["environment_hash"], "engine_hash": context["policy"]["engine_hash"],
        "boundary_policy_hash": digest(context["config"]["environment"]["boundary_policy"])}
    for key, value in expected.items():
        if result.get(key) != value:
            raise ValueError("Verification result provenance mismatch: " + key)
    if result.get("adapted_response") != proof:
        raise ValueError("Response adapter or adapted proof changed")
    if result.get("boundary_policy") != context["config"]["environment"]["boundary_policy"]:
        raise ValueError("Actual expansion boundary changed")
    if outcome_class(result.get("validity_status")) == "incomplete":
        raise ValueError("Incomplete checker outcomes must remain incidents without a terminal record")
    for phase in ("validation", "extraction"):
        if phase not in result:
            if phase == "validation":
                raise ValueError("A checked proof lacks validation evidence")
            continue
        resources = result[phase].get("resources", {})
        expected_resources = {"wall_seconds_limit": context["config"][phase]["wall_seconds"],
            "memory_mib_requested": context["config"]["validation"]["memory_mib"],
            "cpu_quota": context["config"]["validation"]["cpu_quota"],
            "image": context["environment"]["docker_image"], "isolation": "docker",
            "network": "disabled", "credentials_inherited": False, "memory_enforced": True}
        if any(resources.get(key) != value for key, value in expected_resources.items()):
            raise ValueError("Verification resource policy changed: " + phase)
    if result.get("validity_status") == "success" and (result.get("validation", {}).get("validity_status") != "success"
            or result.get("validation", {}).get("kernel_replay") is not True):
        raise ValueError("Only the Lean validation result can make a proof succeed")
    if result.get("metric_status") == "available":
        metrics = result.get("metrics", {})
        if (result.get("validity_status") != "success" or result.get("validation", {}).get("kernel_replay") is not True
                or result.get("extraction", {}).get("validity_status") != "success"
                or result.get("extraction", {}).get("kernel_replay") is not True):
            raise ValueError("Metrics require actual successful validation and extraction")
        if metrics.get("scorer_hash") != SCORER_HASH:
            raise ValueError("Scorer identity changed")
        if metrics.get("metric_status") != "available" or result.get("graph_hash") != digest(result["extraction"]["nodes"]):
            raise ValueError("Measured dependency graph integrity mismatch")
        if any(metrics.get(key) != result["extraction"].get("source_tokens", {}).get(key) for key in ("W_S", "W_B")):
            raise ValueError("Metrics differ from observed source-token counts")
        extraction = result["extraction"]
        rescored = score_graph(extraction["nodes"], extraction["root"],
                               extraction["source_tokens"]["W_S"], extraction["source_tokens"]["W_B"])
        if metrics != rescored:
            raise ValueError("Numeric metrics differ from the unchanged scorer applied to the recorded graph")
        reference = context["references"][task["task_id"]]["metrics"]
        if any(metrics.get(key) != reference.get(key) for key in ("W_S", "U_S_decimal")):
            raise ValueError("Measured statement signature differs from its uniform v2 reference")
        expected_tokens = digest({"version": "original_syntax_v1", "engine_hash": context["policy"]["engine_hash"]})
        if result.get("source_token_policy_hash") != expected_tokens:
            raise ValueError("Source-token policy integrity mismatch")


def make_record(context, slot, generation, generation_file_sha256, result):
    return {"schema_version": 1, "record_kind": "phase_separated_verified_attempt", "evaluation_version": VERSION,
        "attempt_id": slot["attempt_id"], "source_run_hash": context["manifest"]["run_hash"],
        "source_manifest_sha256": context["policy"]["source_manifest_sha256"],
        "generation_identity": generation_identity(slot), "generation": generation,
        "generation_hash": digest(generation), "generation_file_sha256": generation_file_sha256,
        "verification_policy_hash": digest(context["policy"]),
        "verification_environment_hash": context["environment"]["environment_hash"],
        # An explicit verification alias also makes stock records() reject mixing this
        # phase with the original slot's generation environment instead of mislabeling it.
        "environment_hash": context["environment"]["environment_hash"],
        "verification_config_hash": context["policy"]["verification_config_hash"],
        "checker_executed": generation.get("generation_status", "completed") == "completed",
        "validity_status": result["validity_status"], "metric_status": result["metric_status"],
        "verification_result": result, "recorded_at": now()}


def evaluate(run: Path, amendment_path: Path = DEFAULT_AMENDMENT, limit: int | None = None) -> dict:
    context = load_verification_context(run, amendment_path)
    directory, policy = context["directory"], context["policy"]
    if not (directory / "policy.json").exists() and any(directory.glob("*.json")):
        raise ValueError("Version contains records without its amendment policy")
    write_json(directory / "policy.json", policy)
    tasks = {task["task_id"]: task for task in context["manifest"]["tasks"]}
    leftovers = temporary_files(directory)
    completed = incidents = 0
    for slot in context["manifest"]["slots"]:
        if limit is not None and completed + incidents >= limit:
            break
        output = directory / (slot["attempt_id"] + ".json")
        generation_path = Path(run) / "generations" / (slot["attempt_id"] + ".json")
        if output.exists():
            remove_abandoned_claim(output)
            continue
        if not generation_path.exists():
            continue
        with claim(output) as held:
            if not held or output.exists():
                continue
            for temporary in leftovers.get(output.name, ()):
                temporary.unlink(missing_ok=True)
            generation = verified_generation(read_json(generation_path), slot)
            generation_file_sha256 = file_hash(generation_path)
            status = generation.get("generation_status", "completed")
            if status not in GENERATION_OUTCOMES and status not in PROVIDER_STATUSES:
                raise ValueError("Unrecognized original generation status: " + str(status))
            assert_code(context)
            if status != "completed":
                result = {"validity_status": status, "metric_status": "unavailable", "metrics": None,
                          "scheduled_outcome_incomplete": status in PROVIDER_STATUSES}
            else:
                proof = adapt_response(generation["raw_response"], context["config"]["generation"]["response_adapter"])
                result = evaluate_proof(tasks[slot["task_id"]], proof, context["environment"], context["engine"], context["config"])
                result["adapted_response"] = proof
                assert_code(context)
                if file_hash(generation_path) != generation_file_sha256:
                    raise ValueError("Original generation changed during replay")
                if outcome_class(result["validity_status"]) == "incomplete":
                    append_event(directory / "incidents.jsonl", {"event": "checker_incident", "attempt_id": slot["attempt_id"],
                        "generation_hash": digest(generation), "verification_policy_hash": digest(policy),
                        "verification_result": result, "recorded_at": now()})
                    incidents += 1
                    continue
            if file_hash(generation_path) != generation_file_sha256:
                raise ValueError("Original generation changed during replay")
            try:
                verify_result(context, slot, generation, result)
            except ValueError as error:
                append_event(directory / "incidents.jsonl", {"event": "checker_incident", "attempt_id": slot["attempt_id"],
                    "generation_hash": digest(generation), "verification_policy_hash": digest(policy),
                    "reason": str(error), "verification_result": result, "recorded_at": now()})
                incidents += 1
                continue
            if result.get("metric_status") == "available":
                signature = {key: result["metrics"][key] for key in ("W_S", "U_S_decimal")}
                write_json(directory / "signatures" / (digest(slot["task_id"]) + ".json"), signature)
            record = make_record(context, slot, generation, generation_file_sha256, result)
            write_json(output, record)
            append_event(directory / "events.jsonl", {"event": "attempt_evaluated", "attempt_id": slot["attempt_id"],
                "evaluation_version": VERSION, "validity_status": result["validity_status"], "metric_status": result["metric_status"]})
            completed += 1
    summary = {"new_evaluations": completed, "checker_incidents": incidents, "evaluation_version": VERSION}
    if retained := kept_claims(directory):
        summary["kept_claims"] = [str(path) for path in retained]
    return summary


def load_records(run: Path, *, amendment_path: Path = DEFAULT_AMENDMENT, graphs: bool = False) -> list[dict]:
    """Validated phase-separated records/placeholders in original scheduled order.

    Return shape always retains generation_identity and verification_result.
    Metadata identities are never silently overwritten with the other phase.
    """
    context = load_verification_context(run, amendment_path)
    directory = context["directory"]
    if (directory / "policy.json").exists() and read_json(directory / "policy.json") != context["policy"]:
        raise ValueError("Stored version has a different runtime amendment policy")
    if not (directory / "policy.json").exists() and any(directory.glob("*.json")):
        raise ValueError("Version records lack their policy")
    records = []
    for slot in context["manifest"]["slots"]:
        generation_path = Path(run) / "generations" / (slot["attempt_id"] + ".json")
        generation = verified_generation(read_json(generation_path), slot) if generation_path.exists() else None
        path = directory / (slot["attempt_id"] + ".json")
        if path.exists():
            record = read_json(path)
            expected = {"schema_version": 1, "record_kind": "phase_separated_verified_attempt",
                "evaluation_version": VERSION, "attempt_id": slot["attempt_id"],
                "source_run_hash": context["manifest"]["run_hash"], "source_manifest_sha256": context["policy"]["source_manifest_sha256"],
                "generation_identity": generation_identity(slot), "verification_policy_hash": digest(context["policy"]),
                "verification_environment_hash": context["environment"]["environment_hash"],
                "environment_hash": context["environment"]["environment_hash"],
                "verification_config_hash": context["policy"]["verification_config_hash"]}
            if any(record.get(key) != value for key, value in expected.items()):
                raise ValueError("Replay record changed phase provenance: " + slot["attempt_id"])
            if generation is None or not digest(generation) == record.get("generation_hash") == digest(record.get("generation")):
                raise ValueError("Replay does not embed the immutable original generation")
            if record.get("generation_file_sha256") != file_hash(generation_path):
                raise ValueError("Original generation file bytes changed")
            result = record["verification_result"]
            if record["validity_status"] != result["validity_status"] or record["metric_status"] != result["metric_status"]:
                raise ValueError("Replay summary differs from its verification result")
            if record.get("checker_executed") != (generation.get("generation_status", "completed") == "completed"):
                raise ValueError("Replay checker-execution marker changed")
            verify_result(context, slot, generation, result)
            if not graphs:
                record = {**record, "verification_result": without_graph(result),
                    "verification_record": {"path": path.relative_to(run).as_posix(), "sha256": file_hash(path)}}
        else:
            status = "not_generated" if generation is None else generation.get("generation_status", "completed")
            if status == "completed":
                status = "not_evaluated"
            elif generation is not None and status not in GENERATION_OUTCOMES and status not in PROVIDER_STATUSES:
                status = "unrecognized_generation_status"
            record = {"schema_version": 1, "record_kind": "phase_separated_pending_attempt", "evaluation_version": VERSION,
                "attempt_id": slot["attempt_id"], "source_run_hash": context["manifest"]["run_hash"],
                "generation_identity": generation_identity(slot), "generation": generation,
                "verification_policy_hash": digest(context["policy"]),
                "verification_environment_hash": context["environment"]["environment_hash"],
                "verification_config_hash": context["policy"]["verification_config_hash"],
                "verification_result": {"validity_status": status, "metric_status": "unavailable", "metrics": None,
                                        "scheduled_outcome_incomplete": outcome_class(status) == "incomplete"}}
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "evaluate"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--amendment", type=Path, default=DEFAULT_AMENDMENT)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.command == "evaluate":
        result = evaluate(args.run, args.amendment, args.limit)
    else:
        context = load_verification_context(args.run, args.amendment)
        result = {"valid": True, "version": VERSION, "scheduled_attempts": len(context["manifest"]["slots"]),
                  "references": len(context["references"]), "verification_policy_hash": digest(context["policy"]),
                  "generation_environment_hash": context["policy"]["generation_environment_hash"],
                  "verification_environment_hash": context["policy"]["verification_environment_hash"]}
    import json
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
