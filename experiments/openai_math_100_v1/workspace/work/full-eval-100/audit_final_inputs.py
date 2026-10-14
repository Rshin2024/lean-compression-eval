"""Independent data/evidence audit; no Lean processes and no model calls."""
from collections import Counter
import json
from pathlib import Path
import re
import materialize as m
from preparation.extract_scoped_tasks import commands, split_reference
from lean_compression.io import measurement_hash, task_input_hash, verification_request_hash
from lean_compression.metrics import SCORER_HASH


def source_prefix_end(context, prefix):
    """Locate an unchanged predecessor prefix, allowing whitespace-only edits."""
    expected = [character for character in prefix if not character.isspace()]
    index = 0
    for offset, character in enumerate(context):
        if character.isspace():
            continue
        if index >= len(expected) or character != expected[index]:
            raise ValueError("source_predecessor_prefix_changed")
        index += 1
        if index == len(expected):
            return offset + 1
    if expected:
        raise ValueError("source_predecessor_prefix_truncated")
    return 0


def main():
    base = m.HERE
    bundle = m.REPO / "data/question_sets/openai_math_100_v1"
    selected = {row["task_id"]: row for row in m.jsonl(bundle / "selection.jsonl")}
    bundle_manifest = m.read_json(bundle / "manifest.json")
    tasks = m.jsonl(base / "materialized_final_v1/prepared_tasks.jsonl")
    refs = {ref["task_id"]: ref for ref in m.jsonl(base / "materialized_final_v1/prepared_references.jsonl")}
    drafts = {task["task_id"]: task for task in m.jsonl(m.DEFAULT_DRAFT / "scoped_tasks.jsonl")}
    config = m.read_json(base / "config.reference-final.json")
    environment = m.load_environment(base / "environment.azure.json")
    policy = m.read_json(base / "reference-checks-v1/policy.json")
    failures, observations = [], []

    def check(condition, message):
        if not condition:
            failures.append(message)

    check(len(tasks) == len(refs) == len(selected) == 100, "100_task_identity_count")
    check([task["task_id"] for task in tasks] == bundle_manifest["selected_task_ids"], "selected_order_or_ids_changed")
    check(len({task["family_id"] for task in tasks}) == 19, "family_count_changed")
    check(Counter(task["selection_group"] for task in tasks) ==
          {"algebra": 25, "analysis": 25, "combinatorics": 25, "cs_probability": 25}, "selection_groups_changed")
    for filename, key in (("private/candidates.jsonl", "candidates"), ("private/catalogue.html", "catalogue")):
        check(m.file_hash(bundle / filename) == bundle_manifest["input_sha256"][key], f"source_input_hash:{filename}")
    expected_policy = {"tasks_hash": m.digest(tasks), "references_hash": m.digest(list(refs.values())),
                       "config_hash": m.digest(config), "measurement_hash": measurement_hash(config),
                       "environment_hash": environment["environment_hash"], "engine_hash": m.file_hash(m.REPO / "lean/Evaluator.lean"),
                       "evaluator_source_hash": m.evaluator_hash(), "scorer_hash": SCORER_HASH, "kind": "verify-references"}
    for key, value in expected_policy.items():
        check(policy.get(key) == value, "official_policy_mismatch:" + key)
    check(config["validation"]["wall_seconds"] == 180 and config["extraction"]["wall_seconds"] == 900, "final_time_budget_changed")
    changed_propositions = []
    for task in tasks:
        tid, provenance = task["task_id"], task["provenance"]
        original, row, ref = drafts[tid], selected[tid], refs[tid]
        check(task["family_id"] == row["family_id"] and task["selection_group"] == row["selection_group"], tid + ":selection_mapping")
        for key in ("source_path", "source_sha256", "reference_declaration_sha256", "source_commit", "source_parent_task_id"):
            check(provenance[key] == row[key], tid + ":source_pin:" + key)
        check(provenance["declaration"] == row["declaration_name"], tid + ":source_declaration")
        check(provenance["source_commit"] == bundle_manifest["pins"]["source_commit"], tid + ":commit")
        source_path = bundle / "private/sources" / row["source_path"]
        source = source_path.read_text()
        check(m.file_hash(source_path) == row["source_sha256"], tid + ":source_bytes")
        declaration = "\n".join(source.split("\n")[row["start_line"] - 1:row["end_line"]])
        check(m.sha256_text(declaration) == row["reference_declaration_sha256"], tid + ":reference_span")
        original_proof = split_reference(declaration, row["statement"])["proof"]
        check(ref["proof"] == original_proof and ref["proof_sha256"] == m.sha256_text(original_proof), tid + ":reference_proof_bytes")
        check(task["imports"] == original["imports"] and task["import_specs"] == original["import_specs"], tid + ":imports_changed")
        check(provenance["declaration"] in task["forbidden_declarations"], tid + ":original_target_not_forbidden")
        check(provenance["module"] not in task["imports"], tid + ":original_module_imported")
        check(task.get("model_generation_enabled") is False, tid + ":generation_enabled")
        check(original_proof.strip() not in task["context"], tid + ":own_reference_body_in_compiler_context")
        check(not any(marker in task["context"] for marker in
              ("CompressionEvalBootstrap", "CompressionEvalMaterialize", "OM100_BOOTSTRAP", "OM100LetLayout")),
              tid + ":synthetic_helper_in_compiler_context")
        prefix = original["context"].rstrip()
        if prefix.endswith(" in"):
            prefix = prefix[:-3]
        try:
            tail = task["context"][source_prefix_end(task["context"], prefix):]
            for command in commands(tail):
                check(re.match(r"\s*(?:set_option autoImplicit false|universe\b|open\b|omit\b|end\b|namespace\b)",
                               command["masked"]) is not None, tid + ":unexpected_added_context_command")
        except ValueError as error:
            failures.append(tid + ":" + str(error))
        if task["proposition"] != original["proposition"]:
            changed_propositions.append(tid)
            transform = provenance.get("proposition_layout_transform", {})
            check(transform.get("original_proposition") == original["proposition"], tid + ":layout_original_missing")
            check(transform.get("original_proposition_sha256") == m.sha256_text(original["proposition"]), tid + ":layout_original_hash")
            check(transform.get("normalized_proposition_sha256") == m.sha256_text(task["proposition"]), tid + ":layout_normalized_hash")
            check(re.sub(r"\s+", "", task["proposition"]) == "(" + re.sub(r"\s+", "", original["proposition"]) + ")", tid + ":nonlayout_proposition_change")
            check(transform.get("original_vs_normalized_kernel_type_equal") is True, tid + ":layout_kernel_equality_missing")
        for evidence in provenance["materialization_evidence"]:
            check(m.file_hash(base / evidence["path"]) == evidence["sha256"], tid + ":materialization_evidence_hash")
        prepared_path = base / provenance["materialization_evidence"][0]["path"]
        prepared = m.read_json(prepared_path)
        comparison_path = (prepared_path.parent / f"{tid}-comparison.json" if tid in {"OM100-041", "OM100-094"}
                           else prepared_path.parent / "comparison.json")
        compared = m.read_json(comparison_path)
        compared = compared.get("result", compared)
        check(compared.get("validity_status") == "success", tid + ":kernel_comparison_evidence")
        if tid in {"OM100-048", "OM100-078"}:
            check(prepared["evidence"].get("source_proposition_preserved") is True, tid + ":scope_reset_proposition_changed")
            check("source_scope_suffix" in prepared["evidence"], tid + ":scope_reset_evidence_missing")
        anchor_path = base / "reference-checks-v1" / (m.digest(tid) + ".json")
        anchor = m.read_json(anchor_path)
        check(anchor["task_id"] == tid and anchor.get("record_kind") == "verified_reference", tid + ":anchor_identity")
        check(anchor["proof"] == original_proof and anchor["source_hash"] == m.digest(original_proof), tid + ":anchor_proof")
        check(anchor["task_input_hash"] == task_input_hash(task) and anchor["verification_request_hash"] == verification_request_hash(task), tid + ":anchor_inputs")
        check(anchor["validity_status"] == "success" and anchor["metric_status"] == "available", tid + ":anchor_not_measurable_success")
        check(anchor["engine_hash"] == policy["engine_hash"] and anchor["environment_hash"] == environment["environment_hash"], tid + ":anchor_engine_environment")
        check(anchor["boundary_policy"] == config["environment"]["boundary_policy"] and
              anchor["boundary_policy_hash"] == m.digest(config["environment"]["boundary_policy"]), tid + ":anchor_boundary")
        for phase, budget in (("validation", 180), ("extraction", 900)):
            check(anchor[phase].get("kernel_replay") is True, tid + ":kernel_replay:" + phase)
            resources = anchor[phase]["resources"]
            check(resources["wall_seconds_limit"] == budget and resources["memory_mib_requested"] == 8192 and resources["cpu_quota"] == 2,
                  tid + ":resource_policy:" + phase)
        observations.append({"task_id": tid, "official_anchor_sha256": m.file_hash(anchor_path),
                             "verification_request_hash": anchor["verification_request_hash"]})
    check(set(changed_propositions) == {"OM100-041", "OM100-094"}, "unexpected_proposition_changes")
    report = {"schema_version": 1, "status": "pass" if not failures else "fail", "audited_at": m.now(),
        "audit_script_hash": m.file_hash(Path(__file__)), "tasks": len(tasks), "families": 19,
        "source_files": len({row["source_path"] for row in selected.values()}), "original_reference_proofs_unchanged": not any(":reference_proof_bytes" in failure for failure in failures),
        "proposition_layout_changes": changed_propositions, "scope_reset_tasks": ["OM100-048", "OM100-078"],
        "official_policy_hash": m.digest(policy), "measurement_hash": measurement_hash(config),
        "validation_wall_seconds": 180, "extraction_wall_seconds": 900,
        "model_generation_enabled": False, "no_new_lean_or_model_calls": True,
        "limitations": ["Compiler contexts intentionally retain legitimate preceding source proofs; this check excludes each target's own reference body and generated comparison/bootstrap helpers.",
                        "Model-visible premise export and independent answer-access review are separate pending steps."],
        "failures": failures, "official_anchors": observations}
    m.write_private(base / "final-input-independent-audit.json", m.json_text(report))
    print(m.json_text({key: value for key, value in report.items() if key != "official_anchors"}), flush=True)
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
