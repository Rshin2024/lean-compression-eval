"""Export frozen, reference-informed proof-free interfaces in an isolated runtime.

No provider APIs. The supplied reference is elaborated only inside the selected
runtime. This helper exports interface evidence; it does not certify independent
kernel replay, task difficulty, or absence of every semantic answer alias.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
from pathlib import Path
import re
import sys
import tempfile


HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
REPO = BASE / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE.parent / "preparation"))

from lean_compression.environment import load_environment
from lean_compression.io import digest, evaluator_hash, file_hash, read_json
from lean_compression.runner import execute_engine
from extract_scoped_tasks import mask_lean


POLICY = "reference_informed_prior_environment_interfaces_v3"
ENGINE = HERE / "PremiseExporter.lean"
TRANSIENT = {"infrastructure_error", "environment_error", "evaluator_error", "verification_timeout", "verification_resource_limit"}


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().split("\n") if line.strip()]


def write_private(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def request_for(task, proof, validation, *, max_context_bytes=64000, max_constants=4096):
    if not 512 <= max_context_bytes <= 64000 or not 1 <= max_constants <= 4096:
        raise ValueError("Invalid deterministic premise limits")
    context = task["context"].rstrip()
    wrappers = []
    while re.search(r"\bin\s*$", mask_lean(context)):
        match = re.search(r"(?m)^(\s*(?:omit|include|open(?:\s+scoped)?)\b[^\n]*?)\s+in\s*$", context)
        if not match:
            raise ValueError("unsupported_target_local_context_wrapper")
        unwrapped = match.group(1).strip()
        wrappers.append({"original": match.group(0).strip(), "isolated_scope": unwrapped})
        context = context[:match.start()] + unwrapped
    context += "\n\nset_option autoImplicit false\n"
    request = {key: task[key] for key in ("imports", "target_name", "binders", "proposition", "forbidden_declarations")}
    if task.get("import_specs"):
        request["import_specs"] = task["import_specs"]
    request.update(context=context, proof=proof, max_context_bytes=max_context_bytes,
                   max_constants=max_constants, max_definition_bytes=5000,
                   max_heartbeats=validation["max_heartbeats"],
                   permitted_axioms=validation["permitted_axioms"])
    return request, wrappers


def accepted_export(result, task, *, max_context_bytes=64000):
    if result.get("validity_status") != "success" or result.get("premise_export_status") != "exported":
        return None
    if result.get("policy") != POLICY or result.get("proof_bodies_exported") is not False:
        raise ValueError("invalid_premise_export_policy")
    if result.get("kernel_replay") is not False:
        raise ValueError("premise_export_must_not_claim_independent_kernel_replay")
    scope = result.get("source_scope_snapshot")
    if (not isinstance(scope, dict) or scope.get("source") != "trusted_pre_target_command_state"
            or not isinstance(scope.get("current_namespace"), str)
            or not isinstance(scope.get("resolved_open_declarations"), list)
            or not isinstance(scope.get("active_scoped_namespaces"), list)):
        raise ValueError("missing_trusted_source_scope_snapshot")
    data = result.get("export")
    if not isinstance(data, dict) or not isinstance(data.get("premise_context"), str):
        raise ValueError("missing_premise_context")
    text = data["premise_context"]
    if len(text.encode()) != data.get("context_bytes") or len(text.encode()) > max_context_bytes:
        raise ValueError("premise_context_byte_limit_mismatch")
    names, interfaces = data.get("source_dep_names"), data.get("interfaces")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError("invalid_premise_names")
    if not isinstance(interfaces, list) or len(interfaces) != len(names):
        raise ValueError("premise_interface_count_mismatch")
    forbidden = set(task["forbidden_declarations"]) | {task["target_name"].removeprefix("_root_.")}
    for name, entry in zip(names, interfaces):
        info = entry.get("interface", {})
        if info.get("name") != name or info.get("present_before_target") is not True or name in forbidden:
            raise ValueError("export_contains_unavailable_or_forbidden_declaration")
        if info.get("kind") in {"theorem", "axiom", "opaque"} and info.get("definition_body_included") is not False:
            raise ValueError("export_contains_proof_implementation")
    return {"task_id": task["task_id"], "premise_context": text, "premise_context_sha256": file_text_hash(text),
            "source_dep_names": names, "included_count": data["included_count"],
            "omitted_count": data["omitted_count"], "context_bytes": data["context_bytes"],
            "interface_error_count": data["interface_error_count"], "policy": POLICY,
            "source_scope_snapshot": scope,
            "reference_informed_retrieval": True, "model_generation_enabled": False}


def file_text_hash(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


class Exporter:
    def __init__(self, environment, config, output, policy):
        self.environment, self.config, self.output = environment, config, output
        self.policy_hash = digest(policy)
        self.max_bytes = policy["max_context_bytes"]
        self.max_constants = policy["max_constants"]
        self.wall_seconds = policy["export_wall_seconds"]

    def one(self, task, reference):
        if reference.get("proof_sha256") != file_text_hash(reference["proof"]):
            raise ValueError(f"reference_proof_hash_mismatch:{task['task_id']}")
        request, wrappers = request_for(task, reference["proof"], self.config["validation"],
                                        max_context_bytes=self.max_bytes, max_constants=self.max_constants)
        identity = digest({"policy_hash": self.policy_hash, "request": request,
                           "task_source_provenance": task.get("provenance")})
        path = self.output / "records" / (task["task_id"] + ".json")
        if path.exists():
            prior = read_json(path)
            if prior.get("identity") != identity:
                raise ValueError(f"cached_premise_source_identity_changed:{task['task_id']}")
            result = prior["result"]
            if result.get("validity_status") not in TRANSIENT:
                return {"task_id": task["task_id"], "identity": identity, "result": result,
                        "export": accepted_export(result, task, max_context_bytes=self.max_bytes)}
        validation = self.config["validation"]
        result = execute_engine(request, self.environment, ENGINE, validation, mode="validate",
                                wall_seconds=self.wall_seconds)
        exported = accepted_export(result, task, max_context_bytes=self.max_bytes)
        record = {"task_id": task["task_id"], "identity": identity, "policy_hash": self.policy_hash,
                  "source_request_hash": digest(request), "context_wrapper_transforms": wrappers,
                  "reference_proof_sha256": file_text_hash(reference["proof"]), "result": result,
                  "export": exported}
        write_private(path, record)
        return record


def merge_task(task, export, identity):
    """Preserve checked mathematical inputs while invalidating old premise audits."""
    candidate = copy.deepcopy(task)
    if export is not None:
        candidate["premise_context"] = export["premise_context"]
        candidate.setdefault("hashes", {})["premise_context"] = export["premise_context_sha256"]
        candidate.setdefault("provenance", {}).update(premise_policy=POLICY,
            premise_export_identity=identity, reference_informed_retrieval=True,
            premise_source_dep_names=export["source_dep_names"], premise_omitted_count=export["omitted_count"],
            premise_source_scope=export.get("source_scope_snapshot"))
        candidate.setdefault("audit", {}).update(premise_interfaces_exported=True, answer_access_audited=False)
    candidate["model_generation_enabled"] = False
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=HERE.parent / "preparation/draft_v1/private/scoped_tasks.jsonl")
    parser.add_argument("--references", type=Path, default=HERE.parent / "preparation/draft_v1/private/references.jsonl")
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--max-context-bytes", type=int, default=64000)
    parser.add_argument("--max-constants", type=int, default=4096)
    parser.add_argument("--export-wall-seconds", type=int, default=600,
                        help="Preparation-only wall budget; strict proof-validation limits are unchanged")
    args = parser.parse_args()
    if not 1 <= args.export_wall_seconds <= 900:
        raise ValueError("Premise preparation wall budget must be between 1 and 900 seconds")
    output = args.output.resolve()
    if not output.is_relative_to(HERE) or output == HERE:
        raise ValueError("Output must remain in a new premises subdirectory")
    tasks = jsonl(args.tasks)
    if args.task_id:
        chosen = set(args.task_id)
        tasks = [t for t in tasks if t["task_id"] in chosen]
        if {t["task_id"] for t in tasks} != chosen:
            raise ValueError("Unknown selected task ID")
    refs = {r["task_id"]: r for r in jsonl(args.references)}
    if len({t["task_id"] for t in tasks}) != len(tasks) or not tasks:
        raise ValueError("Tasks must be nonempty and uniquely identified")
    environment, config = load_environment(args.environment), read_json(args.config)
    if config["validation"].get("isolation") != "docker":
        raise ValueError("Actual reference premise export requires the isolated Docker runtime")
    if environment.get("docker_image") != config["validation"].get("docker_image"):
        raise ValueError("Environment and premise-export container identities differ")
    for key in ("lean_toolchain", "mathlib_commit"):
        if environment.get(key) != config["environment"].get(key):
            raise ValueError(f"Premise environment/config mismatch: {key}")
        if any(task.get("environment", {}).get(key) != environment.get(key) for task in tasks):
            raise ValueError(f"Premise task/environment mismatch: {key}")
    policy = {"version": POLICY, "engine_sha256": file_hash(ENGINE), "wrapper_sha256": file_hash(Path(__file__)),
              "evaluator_source_hash": evaluator_hash(),
              "environment_hash": environment["environment_hash"], "config_hash": digest(config),
              "tasks_file_sha256": file_hash(args.tasks), "references_file_sha256": file_hash(args.references),
              "max_context_bytes": args.max_context_bytes, "max_constants": args.max_constants,
              "export_wall_seconds": args.export_wall_seconds,
              "selected_task_ids": [t["task_id"] for t in tasks], "reference_informed_retrieval": True,
              "model_calls": 0, "independent_kernel_replay": False}
    if (output / "policy.json").exists() and read_json(output / "policy.json") != policy:
        raise ValueError("Frozen premise-export policy changed; use a new output directory")
    write_private(output / "policy.json", policy)
    exporter = Exporter(environment, config, output, policy)
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        jobs = {pool.submit(exporter.one, task, refs[task["task_id"]]): task["task_id"] for task in tasks}
        for future in as_completed(jobs):
            ident = jobs[future]
            record = future.result()
            results[ident] = record
            print(json.dumps({"task_id": ident, "status": record["result"].get("validity_status"),
                              "exported": record["export"] is not None}), flush=True)
    ordered = [results[t["task_id"]] for t in tasks]
    exports = [r["export"] for r in ordered if r["export"] is not None]
    merged = []
    for task in tasks:
        export = results[task["task_id"]]["export"]
        merged.append(merge_task(task, export, results[task["task_id"]]["identity"]))
    write_private(output / "exports.json", exports)
    write_private(output / "tasks_with_premises.json", merged)
    write_private(output / "summary.json", {"selected": len(tasks), "exported": len(exports),
        "status_counts": dict(Counter(r["result"].get("validity_status") for r in ordered)),
        "policy_hash": digest(policy), "model_calls": 0, "model_generation_enabled": False})


if __name__ == "__main__":
    main()
