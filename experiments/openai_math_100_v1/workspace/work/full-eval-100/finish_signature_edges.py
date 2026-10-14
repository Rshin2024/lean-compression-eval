"""Close source-variable scopes before checking fully explicit OM100 headers."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import re
import materialize as m
import repair_materialization as r


def fresh_scope_suffix(task, universes):
    scopes = [[]]
    scope_names = [""]
    for command in r.commands(task["context"]):
        text = command["masked"].strip()
        match = re.match(r"(?:(?:noncomputable|public)\s+)?(namespace|section|end)\b", text)
        if match:
            if match.group(1) == "end":
                if len(scopes) == 1:
                    raise ValueError("unmatched_source_scope_end")
                scopes.pop()
                scope_names.pop()
            else:
                scopes.append([])
                tail = text[match.end():].strip()
                scope_names.append(tail.split()[0] if tail else "")
        elif re.match(r"open\b", text) and not re.search(r"\bin\s*$", text):
            scopes[-1].append(command["source"].strip())
        elif re.match(r"local\s+(?:notation|infix|prefix|postfix|instance)\b", text):
            raise ValueError("local_scope_feature_requires_explicit_review")
    namespace = r.source_namespace(task)
    return ("\n" + "".join("end" + (" " + name if name else "") + "\n" for name in reversed(scope_names[1:])) +
            ("namespace " + namespace + "\n" if namespace else "") +
            "\n".join(command for scope in scopes for command in scope) +
            ("\nuniverse " + " ".join(m.quoted_name(level) for level in universes) if universes else "") +
            "\nset_option autoImplicit false\n")


def main():
    base = Path(__file__).resolve().parent
    output = base / "materialized_v1/edges_v4"
    config = m.read_json(base / "config.reference-final.json")
    environment = m.load_environment(base / "environment.azure.json")
    engine = m.REPO / "lean/Evaluator.lean"
    tasks = {task["task_id"]: task for task in m.jsonl(m.DEFAULT_DRAFT / "scoped_tasks.jsonl")}
    refs = {ref["task_id"]: ref for ref in m.jsonl(m.DEFAULT_DRAFT / "references.jsonl")}
    policy = {"version": "om100_close_source_variable_scopes_v4", "script_hash": m.file_hash(Path(__file__)),
              "engine_hash": m.file_hash(engine), "evaluator_source_hash": m.evaluator_hash(),
              "config_hash": m.digest(config), "environment_hash": environment["environment_hash"],
              "source_task_hashes": {tid: m.digest(tasks[tid]) for tid in ["OM100-048", "OM100-078"]}}
    if (output / "policy.json").exists() and m.read_json(output / "policy.json") != policy:
        raise ValueError("Edge repair identity changed")
    m.write_private(output / "policy.json", m.json_text(policy))
    worker = m.Materializer(environment, config, engine, output, policy)

    def check(tid):
        source = tasks[tid]
        proof = refs[tid]["proof"]
        raw = m.read_json(base / "materialized_v1/repairs_v2/records" / tid / "bootstrap.json")["result"]["failure_reason"]
        before = json.loads(next(line.split("OM100_BOOTSTRAP ", 1)[1] for line in raw.splitlines() if "OM100_BOOTSTRAP " in line))
        scoped, _ = r.open_source_namespace(source)
        candidate, transformation = r.materialized_candidate(scoped, before)
        suffix = fresh_scope_suffix(scoped, transformation["declared_universes"])
        candidate["context"] = scoped["context"] + suffix
        compared = m.comparison_task(scoped, candidate, proof)
        old = "CompressionEvalMaterialize_" + tid.replace("-", "_") + "_Original"
        fresh = "CompressionEvalMaterialize_" + tid.replace("-", "_") + "_Explicit"
        old_full = ".".join(value for value in (r.source_namespace(source), old) if value)
        compared["context"] = scoped["context"] + compared["context"][len(candidate["context"]):]
        compared["context"] = compared["context"].replace(f"\n\ntheorem _root_.{fresh} ", suffix + f"\n\ntheorem _root_.{fresh} ", 1)
        compared["context"] = compared["context"].replace(f"theorem _root_.{old} ", f"theorem {old} ", 1)
        compared["context"] = compared["context"].replace(f"env.find? `{old}", f"env.find? `{old_full}")
        equality = worker.stage(compared, "True.intro", "comparison")
        evidence = {"task_id": tid, "policy_hash": m.digest(policy), "comparison_status": equality.get("validity_status"),
                    "source_proposition_preserved": candidate["proposition"] == source["proposition"],
                    "source_scope_suffix": suffix, "transformation": transformation}
        if equality.get("validity_status") != "success":
            evidence["status"] = "kernel_type_comparison_failed"
            return worker.finish(source, refs[tid], evidence)
        result = worker.stage(candidate, proof, "explicit", measure=True)
        evidence.update(explicit_status=result.get("validity_status"), metric_status=result.get("metric_status"))
        if result.get("validity_status") != "success" or result.get("validation", {}).get("kernel_replay") is not True:
            evidence["status"] = "explicit_validation_failed"
            return worker.finish(source, refs[tid], evidence)
        candidate["audit"].update(reference_verified=True, explicit_signature_frozen=True,
                                  independent_kernel_replay=True, axiom_policy_pass=True,
                                  transitive_forbidden_dependency_pass=True, original_declaration_absent=True,
                                  metric_closure_verified=result.get("metric_status") == "available")
        candidate["provenance"]["signature_materialization_policy_hash"] = m.digest(policy)
        candidate["provenance"]["signature_materialization"] = (
            "Original source and explicit declaration types kernel-compared; original variable scopes closed and "
            "same source namespace reopened with active opens before checking explicit header, to avoid duplicating "
            "section variables in the trusted axiom; final proof checked and measured with unchanged evaluator")
        reference = copy.deepcopy(refs[tid])
        reference.update(verification_status="isolated_explicit_signature_pass", metric_status=result.get("metric_status"),
                         verification_evidence=f"edges_v4/records/{tid}/explicit.json", signature_materialization_policy_hash=m.digest(policy))
        evidence["status"] = "materialized_measurable" if result.get("metric_status") == "available" else "materialized_metrics_unavailable"
        return worker.finish(candidate, reference, evidence)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for record in pool.map(check, ["OM100-048", "OM100-078"]):
            print(record["task"]["task_id"], record["evidence"], flush=True)


if __name__ == "__main__":
    main()
