"""Assemble all 100 checked tasks with explicit links to preserved preparation evidence."""
import argparse
import copy
import json
from pathlib import Path
import materialize as m
from lean_compression.io import task_input_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-record", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, default=m.HERE / "materialized_final_v1")
    args = parser.parse_args()
    base = m.HERE
    layouts = {}
    for path in args.layout_record:
        path = path.resolve()
        record = m.read_json(path)
        tid = record["task"]["task_id"]
        if tid in layouts or tid not in {"OM100-041", "OM100-094"}:
            raise ValueError("Unexpected or duplicated layout-repair task")
        layouts[tid] = (path, record)
    if set(layouts) != {"OM100-041", "OM100-094"}:
        raise ValueError("Both layout-repair records are required")
    source_tasks = m.jsonl(m.DEFAULT_DRAFT / "scoped_tasks.jsonl")
    source_refs = {ref["task_id"]: ref for ref in m.jsonl(m.DEFAULT_DRAFT / "references.jsonl")}
    selected = []
    for original in source_tasks:
        tid = original["task_id"]
        if tid in layouts:
            path, record = layouts[tid]
        elif tid in {"OM100-048", "OM100-078"}:
            path = base / "materialized_v1/edges_v4/records" / tid / "prepared.json"
            record = m.read_json(path)
        else:
            path = base / "materialized_v1/records" / tid / "prepared.json"
            record = m.read_json(path)
            if record["evidence"]["status"] != "materialized_measurable" and tid != "OM100-090":
                path = base / "materialized_v1/repairs_v2/records" / tid / "prepared.json"
                record = m.read_json(path)
        record = copy.deepcopy(record)
        evidence = [{"path": str(path.relative_to(base)), "sha256": m.file_hash(path)}]
        if tid == "OM100-090":
            calibration_path = base / "reference-calibration-090-900s/result.json"
            calibrated = m.read_json(calibration_path)["result"]
            if calibrated["validity_status"] != "success" or calibrated["metric_status"] != "available":
                raise ValueError("090 calibration is not a successful measurement")
            if calibrated["task_input_hash"] != task_input_hash(record["task"]):
                raise ValueError("090 calibration checked a different question")
            record["task"]["audit"]["metric_closure_verified"] = True
            record["task"]["preparation_status"] = "materialized_measurable"
            record["reference"]["metric_status"] = "available"
            record["evidence"]["status"] = "materialized_measurable"
            evidence.append({"path": str(calibration_path.relative_to(base)), "sha256": m.file_hash(calibration_path)})
        if record["evidence"]["status"] != "materialized_measurable":
            raise ValueError(f"Task {tid} is not ready for official reference checks")
        if record["reference"]["proof"] != source_refs[tid]["proof"]:
            raise ValueError(f"Reference proof changed: {tid}")
        if record["reference"].get("proof_sha256") != m.sha256_text(record["reference"]["proof"]):
            raise ValueError(f"Reference proof hash mismatch: {tid}")
        for key in ("reference_verified", "explicit_signature_frozen", "metric_closure_verified"):
            if record["task"]["audit"].get(key) is not True:
                raise ValueError(f"Task {tid} lacks mechanical audit {key}")
        record["task"]["model_generation_enabled"] = False
        record["task"]["provenance"]["materialization_evidence"] = evidence
        record["reference"]["materialization_evidence"] = evidence
        m.refresh_hashes(record["task"])
        selected.append(record)
    if len(selected) != 100 or len({r["task"]["task_id"] for r in selected}) != 100:
        raise ValueError("The selected 100 questions are not fully accounted for")
    tasks = [record["task"] for record in selected]
    refs = [record["reference"] for record in selected]
    problems = m.validate_manifest(tasks) + m.validate_references(tasks, refs)
    if problems:
        raise ValueError("; ".join(problems))
    for name, values in (("prepared_tasks.jsonl", tasks), ("prepared_references.jsonl", refs)):
        m.write_private(args.output / name, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in values))
    m.write_private(args.output / "summary.json", m.json_text({"accounted_count": 100, "materialized_measurable": 100,
        "tasks_hash": m.digest(tasks), "references_hash": m.digest(refs), "model_generation_enabled": False,
        "official_reference_config": "config.reference-final.json", "official_reference_checks": "pending",
        "remaining_review": ["Canonical prompt interfaces", "Answer-access audit", "Family review"],
        "task_evidence": {record["task"]["task_id"]: record["task"]["provenance"]["materialization_evidence"] for record in selected}}))
    print("Assembled 100 materialized, measurable tasks; official reference checks and independent reviews remain pending")


if __name__ == "__main__":
    main()
