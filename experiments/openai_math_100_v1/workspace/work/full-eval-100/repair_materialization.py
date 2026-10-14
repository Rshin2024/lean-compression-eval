"""Repair source-scoping/header preparation; never relax the final evaluator.

The bootstrap elaborates the private original proof in its original namespace
to learn section parameters used only by that proof. Its metadata is not a
verification verdict. Final acceptance still requires the unchanged evaluator,
kernel equality to the source declaration, and available extraction metrics.
Existing successful prepared tasks are retained without rerunning their checks.
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
from pathlib import Path
import re

import materialize as m
from preparation.extract_scoped_tasks import commands


def included_parameters(context):
    active = [set()]
    for command in commands(context):
        text = command["masked"].strip()
        scope = re.match(r"(?:(?:noncomputable|public)\s+)?(namespace|section|end)\b", text)
        if scope:
            if scope.group(1) == "end":
                if len(active) == 1:
                    raise ValueError("unmatched_source_scope_end")
                active.pop()
            else:
                active.append(set(active[-1]))
            continue
        match = re.fullmatch(r"(include|omit)\s+([^\n]+)", text)
        if match and not re.search(r"\bin\s*$", text):
            names = match.group(2).split()
            if all(re.fullmatch(r"[^\s\[\]{}()]+", name) for name in names):
                if match.group(1) == "include":
                    active[-1].update(names)
                else:
                    active[-1].difference_update(names)
    return sorted(active[-1])


def source_namespace(task):
    match = re.search(r"^Source namespace: ([^\n]+)", task["premise_context"], re.M)
    if not match:
        raise ValueError("source_namespace_metadata_missing")
    value = match.group(1).strip()
    if value == "_root_":
        return ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*", value):
        raise ValueError("unsupported_source_namespace")
    return value


def open_source_namespace(task):
    result = copy.deepcopy(task)
    context, changes = task["context"].rstrip(), []
    while re.search(r"\bin\s*\Z", m.mask_lean(context)):
        match = re.search(r"(?m)^(\s*(?:omit|include|open(?:\s+scoped)?)\b[^\n]*?)\s+in\s*\Z", context)
        if not match:
            raise ValueError("unsupported_target_local_context_wrapper")
        wrapper = match.group(1).strip()
        context = context[:match.start()] + wrapper
        changes.append({"original": match.group(0).strip(), "isolated_scope": wrapper})
    result["context"] = context + "\n\nset_option autoImplicit false\n"
    namespace = source_namespace(task)
    pieces = namespace.split(".") if namespace else []
    opened = [".".join(pieces[:i]) for i in range(1, len(pieces) + 1)]
    if opened:
        result["context"] += "\nopen " + " ".join(opened) + "\n"
    return result, {"wrapper_transforms": changes, "explicitly_opened_source_namespaces": opened}


def materialized_candidate(task, before):
    telescope = before["trusted_telescope"]
    count = m.prefix_count(task, telescope)
    used = {binder["name"] for binder in m.source_header_binders(task["binders"]) if binder["name"]}
    added, renamed = [], []
    for index, binder in enumerate(telescope["binders"][:count]):
        name = m.clean_name(binder["name"])
        if name in used:
            if binder["binder_info"] != "instance" or "._@." not in binder["name"]:
                raise ValueError("non_hygienic_binder_name_collision:" + name)
            base = "om100_inst_" + str(index)
            name = base
            while name in used:
                name += "_"
            renamed.append({"trusted_name": binder["name"], "materialized_name": name})
        used.add(name)
        left, right = m.OPENERS[binder["binder_info"]]
        added.append(f"{left}{m.quoted_name(name)} : {binder['type_source']}{right}")
    result = copy.deepcopy(task)
    result["binders"] = (" ".join(added) + " " + task["binders"]).strip()
    declared = set()
    for line in task["context"].splitlines():
        if re.match(r"\s*universes?\s", line):
            declared.update(m.clean_name(word) for word in line.strip().split()[1:])
    extra = [level for level in before.get("universe_parameters", []) if level not in declared and
             re.search(r"(?<![\w'])" + re.escape(level) + r"(?![\w'])", " ".join(added))]
    if extra:
        result["context"] += "\nuniverse " + " ".join(m.quoted_name(level) for level in extra) + "\n"
    omitted = included_parameters(task["context"])
    if omitted:
        result["context"] += "\nomit " + " ".join(omitted) + "\n"
    return result, {"missing_header_parameter_count": count, "added_header": " ".join(added),
                    "renamed_hygienic_instances": renamed, "declared_universes": extra,
                    "reset_source_include_parameters": omitted,
                    "source_proposition_preserved": result["proposition"] == task["proposition"]}


def bootstrap_task(task, proof):
    result = copy.deepcopy(task)
    short = "CompressionEvalBootstrap_" + task["task_id"].replace("-", "_")
    full = ".".join(value for value in (source_namespace(task), short) if value)
    result["context"] += f"\n\ntheorem {short} {task['binders']} : {task['proposition']} :=\n{proof}\n"
    result["context"] += f'''
open Lean in
run_cmd do
  let env ← Lean.getEnv
  let Option.some ci := env.find? `{full} | throwError "bootstrap declaration missing"
  let value ← Lean.Elab.Command.liftTermElabM do
    Lean.withOptions (fun opts => opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true) do
      let telescope ← Lean.Meta.forallTelescope ci.type fun vars body => do
        let mut bs : Array Lean.Json := #[]
        for var in vars do
          let decl ← var.fvarId!.getDecl
          let text ← Lean.Meta.ppExpr decl.type
          let kind := match decl.binderInfo with
            | .default => "explicit"
            | .implicit => "implicit"
            | .strictImplicit => "strict_implicit"
            | .instImplicit => "instance"
          bs := bs.push <| Lean.Json.mkObj [("name", toJson decl.userName.toString),
            ("binder_info", toJson kind), ("type_source", toJson text.pretty)]
        let text ← Lean.Meta.ppExpr body
        return Lean.Json.mkObj [("binders", toJson bs), ("proposition", toJson text.pretty)]
      return Lean.Json.mkObj [("trusted_telescope", telescope),
        ("universe_parameters", toJson (ci.levelParams.map Lean.Name.toString)),
        ("metadata_export_not_reference_verdict", toJson true)]
  throwError "OM100_BOOTSTRAP {{value.compress}}"
  return ()
'''
    result.update(target_name="_root_.CompressionEvalBootstrapCheck_" + task["task_id"].replace("-", "_"),
                  binders="", proposition="True")
    return result


class Repair(m.Materializer):
    def __init__(self, environment, config, engine, output, policy, original_output):
        super().__init__(environment, config, engine, output, policy)
        self.original_output = original_output

    def run(self, original, reference):
        original_record = self.original_output / "records" / original["task_id"] / "prepared.json"
        if original_record.exists():
            prior = m.read_json(original_record)
            if prior["evidence"]["status"] in {"materialized_measurable", "materialized_metrics_unavailable"}:
                return prior
        task = copy.deepcopy(original)
        task.setdefault("audit", {}).update(dict.fromkeys(m.MECHANICAL_AUDITS, False))
        task["model_generation_enabled"] = False
        evidence = {"task_id": task["task_id"], "policy_hash": self.policy_hash,
                    "status": "repair_started", "model_generation_enabled": False,
                    "independent_answer_access_review": "pending", "prompt_interface_review": "pending"}
        ref = copy.deepcopy(reference)
        ref.update(verification_status="not_checked", metric_status="unavailable")
        try:
            declaration = task.get("provenance", {}).get("declaration")
            if not declaration or declaration not in task["forbidden_declarations"]:
                raise ValueError("original_target_missing_from_forbidden_declarations")
            task, corrections = open_source_namespace(task)
            evidence["source_scope_corrections"] = corrections
            bootstrap = self.stage(bootstrap_task(task, reference["proof"]), "True.intro", "bootstrap")
            evidence["bootstrap_status"] = bootstrap.get("validity_status")
            # Elab.IO captures command output; a deliberately marked diagnostic
            # carries metadata through the engine without pretending that this
            # bootstrap is a reference-verification verdict.
            diagnostic_lines = bootstrap.get("failure_reason", "").splitlines()
            errors = [line for line in diagnostic_lines if re.search(r": error(?:\([^)]*\))?:", line)]
            lines = [line.split("OM100_BOOTSTRAP ", 1)[1] for line in diagnostic_lines
                     if "OM100_BOOTSTRAP " in line]
            if len(lines) != 1 or len(errors) != 1 or "OM100_BOOTSTRAP " not in errors[0]:
                evidence["status"] = "bootstrap_failed"
                return self.finish(task, ref, evidence)
            before = json.loads(lines[0])
            candidate, transformation = materialized_candidate(task, before)
            evidence["transformation"] = transformation
            compared = m.comparison_task(task, candidate, reference["proof"])
            # Compare the original declaration under its original source namespace;
            # _root_ relocation alone can change unqualified name resolution.
            original_short = "CompressionEvalMaterialize_" + task["task_id"].replace("-", "_") + "_Original"
            original_full = ".".join(value for value in (source_namespace(task), original_short) if value)
            fresh_short = "CompressionEvalMaterialize_" + task["task_id"].replace("-", "_") + "_Explicit"
            additions = candidate["context"][len(task["context"]):]
            # The source declaration must retain its original include policy;
            # explicit parameters replace those includes only for the new one.
            compared["context"] = task["context"] + compared["context"][len(candidate["context"]):]
            compared["context"] = compared["context"].replace(
                f"\n\ntheorem _root_.{fresh_short} ", additions + f"\n\ntheorem _root_.{fresh_short} ", 1)
            compared["context"] = compared["context"].replace(f"theorem _root_.{original_short} ", f"theorem {original_short} ")
            compared["context"] = compared["context"].replace(f"env.find? `{original_short}", f"env.find? `{original_full}")
            equality = self.stage(compared, "True.intro", "comparison")
            evidence["comparison_status"] = equality.get("validity_status")
            if equality.get("validity_status") != "success" or equality.get("kernel_replay") is not True:
                evidence["status"] = "kernel_type_comparison_failed"
                return self.finish(task, ref, evidence)
            candidate = m.refresh_hashes(candidate)
            after = self.stage(candidate, reference["proof"], "explicit", measure=True)
            evidence.update(explicit_status=after.get("validity_status"), metric_status=after.get("metric_status"))
            if after.get("validity_status") != "success" or after.get("validation", {}).get("kernel_replay") is not True:
                evidence["status"] = "explicit_validation_failed"
                return self.finish(task, ref, evidence)
            task = candidate
            task["audit"].update(reference_verified=True, explicit_signature_frozen=True,
                                 independent_kernel_replay=True, axiom_policy_pass=True,
                                 transitive_forbidden_dependency_pass=True, original_declaration_absent=True,
                                 metric_closure_verified=after.get("metric_status") == "available")
            task["provenance"]["signature_materialization_policy_hash"] = self.policy_hash
            task["provenance"]["signature_materialization"] = (
                "Original source declaration elaborated privately to recover section parameters used by its proof; "
                "hygienic instance binder names made distinct; source proposition unchanged; original and explicit "
                "whole types compared by Kernel.isDefEqGuarded modulo ordered universe renaming; final reference "
                "independently replayed and measured by the unchanged strict evaluator without bootstrap or comparison helpers")
            evidence["status"] = "materialized_measurable" if after.get("metric_status") == "available" else "materialized_metrics_unavailable"
            evidence["metric_unavailable_reason"] = after.get("metric_unavailable_reason")
            ref.update(verification_status="isolated_explicit_signature_pass", metric_status=after.get("metric_status"),
                       verification_evidence=f"repairs_v2/records/{task['task_id']}/explicit.json",
                       signature_materialization_policy_hash=self.policy_hash)
        except Exception as error:
            evidence.update(status="repair_error", error=f"{type(error).__name__}: {error}")
        return self.finish(task, ref, evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--original-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=m.DEFAULT_DRAFT / "scoped_tasks.jsonl")
    parser.add_argument("--references", type=Path, default=m.DEFAULT_DRAFT / "references.jsonl")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    tasks, references = m.jsonl(args.tasks), m.jsonl(args.references)
    config, environment = m.read_json(args.config), m.load_environment(args.environment)
    m.require_implemented_policy(config)
    policy = {"version": "om100_materialization_repair_v2", "script_hash": m.file_hash(Path(__file__)),
              "materializer_hash": m.file_hash(Path(m.__file__)), "evaluator_source_hash": m.evaluator_hash(),
              "engine_hash": m.file_hash(args.engine), "base_policy_hash": m.digest(m.read_json(args.original_output / "policy.json")),
              "tasks_hash": m.digest(tasks), "references_hash": m.digest(references), "config_hash": m.digest(config),
              "environment_hash": environment["environment_hash"], "generation_enabled": False}
    path = args.output / "policy.json"
    if path.exists() and m.read_json(path) != policy:
        raise ValueError("Repair identity changed; use a new output directory")
    m.write_private(path, m.json_text(policy))
    refs = {ref["task_id"]: ref for ref in references}
    worker = Repair(environment, config, args.engine, args.output, policy, args.original_output)
    selected = [task for task in tasks if not args.only or task["task_id"] in args.only]
    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker.run, task, refs[task["task_id"]]) for task in selected]
        for future in as_completed(futures):
            record = future.result()
            results[record["task"]["task_id"]] = record
            print(f"{len(results)}/{len(selected)} {record['task']['task_id']} {record['evidence']['status']}", flush=True)
    ordered = [results[task["task_id"]] for task in selected]
    for filename, key in (("prepared_tasks.jsonl", "task"), ("prepared_references.jsonl", "reference")):
        m.write_private(args.output / filename, "".join(json.dumps(record[key], sort_keys=True, ensure_ascii=False) + "\n" for record in ordered))
    summary = {"policy_hash": m.digest(policy), "accounted_count": len(ordered),
               "statuses": dict(Counter(record["evidence"]["status"] for record in ordered)),
               "tasks": [record["evidence"] for record in ordered], "model_generation_enabled": False}
    m.write_private(args.output / "summary.json", m.json_text(summary))
    print(m.json_text({key: value for key, value in summary.items() if key != "tasks"}))
    return 0 if summary["statuses"].get("materialized_measurable") == len(selected) else 2


if __name__ == "__main__":
    raise SystemExit(main())
