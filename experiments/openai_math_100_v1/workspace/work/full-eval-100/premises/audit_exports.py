"""Audit and merge all 100 exported contexts without authorizing generation.

This checks the implemented prior-environment interface policy and literal proof/
hint absence. It does not certify training novelty or rule out every logically
equivalent theorem. Source-context and independent reference checks stay separate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile

import export_premises as exporter
from lean_compression.io import digest, evaluator_hash, file_hash
from lean_compression.prompts import messages


VERSION = "om100_prior_interface_audit_v2"
EXPECTED = [f"OM100-{i:03d}" for i in range(1, 101)]


def normalized(text):
    return " ".join(text.split())


def audit(tasks_path, references_path, exports_dir, config_path, catalogue_path, output):
    output = output.resolve()
    if not output.is_relative_to(exporter.HERE) or output == exporter.HERE:
        raise ValueError("Audit output must remain inside the premises directory")
    tasks = exporter.jsonl(tasks_path)
    refs = exporter.jsonl(references_path)
    if [t["task_id"] for t in tasks] != EXPECTED or [r["task_id"] for r in refs] != EXPECTED:
        raise ValueError("Audit requires all 100 unique selected IDs in order")
    refs = {r["task_id"]: r for r in refs}
    policy = json.loads((exports_dir / "policy.json").read_text())
    config = json.loads(config_path.read_text())
    expected_policy = {
        "version": exporter.POLICY, "engine_sha256": file_hash(exporter.ENGINE),
        "wrapper_sha256": file_hash(Path(exporter.__file__)),
        "evaluator_source_hash": evaluator_hash(),
        "config_hash": digest(config), "tasks_file_sha256": file_hash(tasks_path),
        "references_file_sha256": file_hash(references_path), "selected_task_ids": EXPECTED,
    }
    for key, value in expected_policy.items():
        if policy.get(key) != value:
            raise ValueError(f"Premise-export binding mismatch: {key}")
    if policy.get("max_context_bytes") != 56000:
        raise ValueError("All-task audit expects the uniformly frozen 56,000-byte policy")
    raw_catalogue = exporter.jsonl(catalogue_path)
    if (len(raw_catalogue) != 100 or sorted(r.get("id", "") for r in raw_catalogue) != EXPECTED
            or any(not isinstance(r.get("proof_summary"), str) or not r["proof_summary"].strip()
                   for r in raw_catalogue)):
        raise ValueError("Catalogue must contain exactly the 100 selected unique IDs and nonempty curator summaries")
    reference_texts = {i: normalized(r["proof"]) for i, r in refs.items()}
    summaries = {r["id"]: normalized(r.get("proof_summary", "")) for r in raw_catalogue}
    rows, merged = [], []
    for task in tasks:
        ident = task["task_id"]
        row = {"task_id": ident, "checks": {}, "findings": []}
        record_path = exports_dir / "records" / (ident + ".json")
        candidate = copy.deepcopy(task)
        candidate["model_generation_enabled"] = False
        if not record_path.exists():
            row.update(status="failed", findings=["missing_export_record"])
            rows.append(row)
            merged.append(candidate)
            continue
        record = json.loads(record_path.read_text())
        reference = refs[ident]
        try:
            if exporter.file_text_hash(reference["proof"]) != reference.get("proof_sha256"):
                raise ValueError("reference_proof_hash_mismatch")
            request, _ = exporter.request_for(task, reference["proof"], config["validation"],
                                              max_context_bytes=policy["max_context_bytes"],
                                              max_constants=policy["max_constants"])
            identity = digest({"policy_hash": digest(policy), "request": request,
                               "task_source_provenance": task.get("provenance")})
            if record.get("task_id") != ident or record.get("identity") != identity:
                raise ValueError("export_record_source_identity_mismatch")
            if record.get("source_request_hash") != digest(request):
                raise ValueError("source_request_hash_mismatch")
            result = record["result"]
            if result.get("target") != task["target_name"].removeprefix("_root_."):
                raise ValueError("wrong_target_export")
            export = exporter.accepted_export(result, task, max_context_bytes=56000)
            if export is None:
                raise ValueError("premise_export_not_successful:" + result.get("validity_status", "unknown"))
            data = result["export"]
            row["checks"].update(source_identity_bound=True, prior_environment_membership=True,
                                  target_names_excluded=True, theorem_implementations_omitted=True,
                                  bounded_context=True, reference_informed_policy_disclosed=True)
            if data.get("interface_error_count") != 0:
                row["findings"].append("interface_pretty_print_errors")
            context = export["premise_context"]
            flat = normalized(context)
            copied_proofs = [other for other, proof in reference_texts.items() if len(proof) >= 9 and proof in flat]
            copied_hints = [other for other, summary in summaries.items() if len(summary) >= 40 and summary in flat]
            if copied_proofs:
                row["findings"].append("selected_reference_text_present:" + ",".join(copied_proofs))
            if copied_hints:
                row["findings"].append("curator_summary_present:" + ",".join(copied_hints))
            row["checks"].update(all_100_reference_bodies_absent=not copied_proofs,
                                  all_100_curator_summaries_absent=not copied_hints)
            candidate = exporter.merge_task(task, export, identity)
            prompt_sizes = {c: len(messages(candidate, c, config["environment"]["lean_toolchain"])[1]["content"].encode())
                            for c in config["conditions"]}
            row["checks"]["all_prompt_conditions_within_cap"] = max(prompt_sizes.values()) <= config["generation"]["context_byte_cap"]
            if not row["checks"]["all_prompt_conditions_within_cap"]:
                row["findings"].append("full_prompt_exceeds_configured_byte_cap")
            names = data["source_dep_names"]
            imported_private = [entry["interface"]["name"] for entry in data["interfaces"]
                                if entry["interface"]["name"].startswith("_private.")
                                and entry["interface"]["owner_module"] != "EvaluatorCandidate"]
            row["checks"]["no_inaccessible_imported_private_names"] = not imported_private
            if imported_private:
                row["findings"].append("inaccessible_imported_private_names")
            row.update(premise_context_sha256=export["premise_context_sha256"], source_dep_count=len(names),
                       context_bytes=data["context_bytes"], prompt_bytes=prompt_sizes,
                       omitted_count=data["omitted_count"],
                       omitted_imported_private_count=data.get("omitted_imported_private_count"),
                       result_hash=digest(result), export_record_hash=file_hash(record_path))
        except (ValueError, KeyError, TypeError) as exc:
            row["findings"].append(str(exc))
        row["status"] = "passed_implemented_interface_policy" if not row["findings"] else "failed"
        candidate.setdefault("audit", {})["premise_interface_audit_pass"] = not row["findings"]
        candidate["audit"]["answer_access_audited"] = False
        candidate.setdefault("provenance", {})["premise_interface_audit_policy"] = VERSION
        candidate["model_generation_enabled"] = False
        rows.append(row)
        merged.append(candidate)
    report = {"version": VERSION, "selected_count": 100, "accounted_count": len(rows),
              "passed_count": sum(r["status"] == "passed_implemented_interface_policy" for r in rows),
              "statuses": dict(Counter(r["status"] for r in rows)), "export_policy_hash": digest(policy),
              "tasks_input_sha256": file_hash(tasks_path), "references_input_sha256": file_hash(references_path),
              "catalogue_input_sha256": file_hash(catalogue_path), "catalogue_summary_count": len(summaries),
              "auditor_sha256": file_hash(Path(__file__)), "model_generation_enabled": False,
              "model_calls": 0, "per_task": rows,
              "scope": "Trusted pre-target membership and proof-implementation suppression; all selected literal reference bodies/curator summaries absent; bounded identical context for all models",
              "remaining_review": ["Independent official reference validation records must match mathematical inputs",
                                   "Review semantic equivalent-answer aliases and family/dependency policy",
                                   "Root must approve final answer-access flags and frozen generation manifest"],
              "limitations": ["No certified-unseen or training-exposure claim",
                              "Literal absence and prior environment membership do not exclude every logically equivalent theorem",
                              "Reference-informed retrieval is part of the fixed task presentation",
                              "Omission counts concern known candidates under the frozen caps"]}
    exporter.write_private(output / "audit.json", report)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as stream:
        for task in merged:
            stream.write(json.dumps(task, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(output / "final_tasks_candidate.jsonl")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--exports", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, default=exporter.REPO / "data/question_sets/openai_math_100_v1/private/candidates.jsonl")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.tasks, args.references, args.exports, args.config, args.catalogue, args.output)
    print(json.dumps({k: v for k, v in report.items() if k not in {"per_task", "limitations", "remaining_review"}}, indent=2))
    return 0 if report["passed_count"] == 100 else 2


if __name__ == "__main__":
    raise SystemExit(main())
