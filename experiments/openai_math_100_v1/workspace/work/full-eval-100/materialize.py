"""Prepare all selected OM100 signatures using isolated, trusted reference checks.

This is an offline preparation tool, never a model-generation entry point. It
preserves source propositions, adds only missing declaration-header parameters,
and accepts the result only after Lean's kernel compares the two whole types.
All compiler contexts and reference proofs remain in the private output folder.
Answer-access, family, and prompt-interface review remain separate obligations.

Run ``python3 materialize.py --self-test`` without a Lean runtime. Real checking
requires explicit --environment, --config, --engine, and --output arguments.
Use a new output directory when any input, code, engine, or policy changes.
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
BASE = HERE.parents[1]
REPO = BASE / "outputs/lean-compression-eval"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))

from lean_compression.dataset import sha256_text, validate_manifest, validate_references
from lean_compression.environment import load_environment
from lean_compression.integrity import boundary_expansion_modules, require_implemented_policy
from lean_compression.io import digest, evaluator_hash, file_hash, now, read_json
from lean_compression.runner import evaluate_proof, execute_engine
from preparation.extract_scoped_tasks import mask_lean, top_tokens

VERSION = "om100_signature_materialization_v1"
DEFAULT_DRAFT = HERE / "preparation/draft_v1/private"
OPENERS = {"explicit": ("(", ")"), "implicit": ("{", "}"),
           "strict_implicit": ("⦃", "⦄"), "instance": ("[", "]")}
KINDS = {left: kind for kind, (left, _) in OPENERS.items()}
PAIRS = dict(OPENERS.values())
TRANSIENT = {"infrastructure_error", "environment_error", "evaluator_error"}
MECHANICAL_AUDITS = ("reference_verified", "explicit_signature_frozen", "metric_closure_verified",
                     "independent_kernel_replay", "axiom_policy_pass", "transitive_forbidden_dependency_pass",
                     "original_declaration_absent")


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


def write_private(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().split("\n") if line.strip()]


def clean_name(name):
    return name.split("._@.", 1)[0].removeprefix("«").removesuffix("»")


def quoted_name(name):
    name = clean_name(name)
    if not name or name == "_" or any(character in name for character in "«»\n\r\x00"):
        raise ValueError(f"unsupported_materialized_binder_name:{name!r}")
    return f"«{name}»"


def binder_groups(source):
    """Read source-header groups, ignoring delimiters inside comments/strings."""
    masked = mask_lean(source)
    groups, i = [], 0
    while i < len(masked):
        if masked[i].isspace():
            i += 1
            continue
        if masked[i] not in PAIRS:
            raise ValueError("unsupported_source_binder_syntax:" + source[i:i + 100])
        start, left = i, masked[i]
        stack = [PAIRS[left]]
        i += 1
        while stack and i < len(masked):
            character = masked[i]
            if character in PAIRS:
                stack.append(PAIRS[character])
            elif character == stack[-1]:
                stack.pop()
            i += 1
        if stack:
            raise ValueError("unclosed_source_binder")
        groups.append((left, source[start + 1:i - 1]))
    return groups


def source_header_binders(source):
    """Flatten only written header binders; never telescope the proposition."""
    result = []
    for left, body in binder_groups(source):
        colons = top_tokens(mask_lean(body), ":")
        if left == "[" and not colons:
            result.append({"name": None, "kind": "instance", "source_type": body.strip()})
            continue
        names = body[:colons[0]] if colons else body
        names = re.findall(r"«[^»]+»|[^\s]+", names)
        if not names:
            raise ValueError("empty_source_binder_group")
        for name in names:
            result.append({"name": None if name == "_" else clean_name(name), "kind": KINDS[left],
                           "source_type": body[colons[0] + 1:].strip() if colons else None})
    return result


def prefix_count(task, telescope):
    """Locate the contiguous written header within Lean's trusted telescope.

    All named header binders must agree, not just the first convenient name.
    Anonymous binders constrain their kind. Ambiguous alignments fail closed.
    When the header is empty, a proposition forall/arrow remains in the source;
    the eventual kernel comparison rejects any mistaken additional parameters.
    """
    trusted = telescope["binders"]
    header = source_header_binders(task["binders"])
    if header:
        positions = []
        for start in range(len(trusted) - len(header) + 1):
            if all(expected["kind"] == actual["binder_info"] and
                   (expected["name"] is None or expected["name"] == clean_name(actual["name"]))
                   for expected, actual in zip(header, trusted[start:start + len(header)])):
                positions.append(start)
        if len(positions) != 1:
            raise ValueError(f"source_header_alignment_not_unique:{positions}")
        return positions[0]
    proposition = task["proposition"].lstrip(" (\n\t")
    first_forall = re.match(r"(?:∀|forall)\s*[{(⦃]?\s*(«[^»]+»|[^\s:,{(⦃]+)", proposition)
    if first_forall:
        name = clean_name(first_forall.group(1))
        positions = [i for i, binder in enumerate(trusted) if clean_name(binder["name"]) == name]
        if len(positions) != 1:
            raise ValueError("proposition_forall_alignment_not_unique")
        return positions[0]
    for i, binder in enumerate(trusted):
        if "._@._internal." in binder["name"]:
            return i
    return len(trusted)


def normalize_target_context(task):
    """Lift a restricted trailing `... in` into this single-target process.

    A wrapper cannot be left dangling because the engine elaborates context
    separately from the target. Only scope operations with equivalent meaning
    for all subsequent declarations are lifted. Unknown wrappers remain errors.
    """
    context = task["context"].rstrip()
    changes = []
    while re.search(r"\bin\s*$", mask_lean(context)):
        match = re.search(r"(?m)^(\s*(?:omit|include|open(?:\s+scoped)?)\b[^\n]*?)\s+in\s*$", context)
        if not match:
            raise ValueError("unsupported_target_local_context_wrapper")
        wrapper = match.group(1).strip()
        context = context[:match.start()] + wrapper
        changes.append({"original": match.group(0).strip(), "isolated_scope": wrapper})
    # Keep source prefix declarations under their original options. The target
    # and its standalone signature must not invent undeclared term variables.
    context += "\n\nset_option autoImplicit false\n"
    result = copy.deepcopy(task)
    result["context"] = context
    return result, changes


def materialized_candidate(task, before):
    telescope = before.get("trusted_telescope")
    if not isinstance(telescope, dict) or not isinstance(telescope.get("binders"), list):
        raise ValueError("trusted_telescope_missing")
    count = prefix_count(task, telescope)
    prefix = telescope["binders"][:count]
    names = [clean_name(binder["name"]) for binder in prefix]
    header_names = {binder["name"] for binder in source_header_binders(task["binders"]) if binder["name"]}
    if len(set(names)) != len(names) or set(names) & header_names:
        raise ValueError("materialized_binder_name_collision")
    added = []
    for binder in prefix:
        if binder["binder_info"] not in OPENERS or not isinstance(binder.get("type_source"), str):
            raise ValueError("malformed_trusted_binder")
        left, right = OPENERS[binder["binder_info"]]
        added.append(f"{left}{quoted_name(binder['name'])} : {binder['type_source']}{right}")
    candidate = copy.deepcopy(task)
    candidate["binders"] = (" ".join(added) + " " + task["binders"]).strip()
    # A transformed proposition, even one with an equivalent type, would change
    # which source tokens are counted as the fixed question. Never rewrite it.
    assert candidate["proposition"] == task["proposition"]
    declared = set()
    for line in mask_lean(candidate["context"]).splitlines():
        if re.match(r"\s*universes?\s", line):
            declared.update(line.strip().split()[1:])
    extra = [level for level in before.get("universe_parameters", []) if level not in declared and
             re.search(r"(?<![\w'])" + re.escape(level) + r"(?![\w'])", " ".join(added))]
    if extra:
        candidate["context"] += "\nuniverse " + " ".join(quoted_name(level) for level in extra) + "\n"
    return candidate, {"missing_header_parameter_count": count, "added_header": " ".join(added),
                       "declared_universes": extra, "source_proposition_preserved": True}


def comparison_task(scoped, candidate, proof):
    """Compare elaborated types in a process containing no model output."""
    identifier = re.sub(r"[^A-Za-z0-9_]", "_", scoped["task_id"])
    old = f"CompressionEvalMaterialize_{identifier}_Original"
    fresh = f"CompressionEvalMaterialize_{identifier}_Explicit"
    check = f"CompressionEvalMaterialize_{identifier}_Check"
    result = copy.deepcopy(candidate)
    result["context"] += (
        f"\n\ntheorem _root_.{old} {scoped['binders']} : {scoped['proposition']} :=\n{proof}\n"
        f"\n\ntheorem _root_.{fresh} {candidate['binders']} : {candidate['proposition']} :=\n{proof}\n"
        "\nrun_cmd do\n"
        "  let env ← Lean.getEnv\n"
        f"  let Option.some old := env.find? `{old} | throwError \"missing original comparison declaration\"\n"
        f"  let Option.some fresh := env.find? `{fresh} | throwError \"missing materialized comparison declaration\"\n"
        "  unless old.levelParams.length == fresh.levelParams.length do throwError \"universe parameter count changed\"\n"
        "  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)\n"
        "  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do\n"
        "    throwError \"materialized signature differs from original\"\n")
    result.update(target_name=f"_root_.{check}", binders="", proposition="True")
    return result


def refresh_hashes(task):
    task["hashes"] = {key: sha256_text(task[key]) for key in ("context", "premise_context", "binders", "proposition")}
    return task


class Materializer:
    def __init__(self, environment, config, engine, output, policy):
        self.environment, self.config, self.engine, self.output = environment, config, engine, output
        self.policy_hash = digest(policy)

    def stage(self, task, proof, name, *, measure=False):
        identity = digest({"policy_hash": self.policy_hash, "task": task, "proof": proof,
                           "stage": name, "measure": measure})
        path = self.output / "records" / task["task_id"] / f"{name}.json"
        if path.exists():
            previous = read_json(path)
            if previous.get("identity") != identity:
                raise ValueError(f"cached_stage_identity_changed:{path}")
            if previous["result"].get("validity_status") not in TRANSIENT:
                return previous["result"]
        if measure:
            result = evaluate_proof(task, proof, self.environment, self.engine, self.config)
        else:
            request = {key: task[key] for key in ("imports", "target_name", "binders", "proposition", "context",
                                                 "forbidden_declarations")}
            if task.get("import_specs"):
                request["import_specs"] = task["import_specs"]
            modules = boundary_expansion_modules(self.config["environment"]["boundary_policy"])
            if modules is not None:
                request["expand_modules"] = modules
            validation = self.config["validation"]
            request.update(proof=proof, max_heartbeats=validation["max_heartbeats"],
                           permitted_axioms=validation["permitted_axioms"])
            result = execute_engine(request, self.environment, self.engine, validation, mode="validate",
                                    wall_seconds=validation["wall_seconds"])
        write_private(path, json_text({"identity": identity, "policy_hash": self.policy_hash,
                                       "checked_at": now(), "result": result}))
        return result

    def run(self, original, reference):
        task = copy.deepcopy(original)
        task.setdefault("audit", {}).update(dict.fromkeys(MECHANICAL_AUDITS, False))
        task["model_generation_enabled"] = False
        evidence = {"task_id": task["task_id"], "policy_hash": self.policy_hash,
                    "status": "preparation_started", "model_generation_enabled": False,
                    "independent_answer_access_review": "pending", "prompt_interface_review": "pending"}
        ref = copy.deepcopy(reference)
        ref.update(verification_status="not_checked", metric_status="unavailable")
        try:
            declaration = task.get("provenance", {}).get("declaration")
            if not declaration or declaration not in task.get("forbidden_declarations", []):
                raise ValueError("original_target_missing_from_forbidden_declarations")
            if task.get("provenance", {}).get("module") in task["imports"]:
                raise ValueError("original_target_module_imported")
            task, wrappers = normalize_target_context(task)
            evidence["context_wrapper_transforms"] = wrappers
            before = self.stage(task, reference["proof"], "scoped")
            evidence["scoped_status"] = before.get("validity_status")
            if before.get("validity_status") != "success" or before.get("kernel_replay") is not True:
                evidence["status"] = "scoped_validation_failed"
                return self.finish(task, ref, evidence)
            evidence["original_declaration_absent"] = True
            evidence["scoped_type_fingerprint"] = before.get("proved_type_fingerprint")
            candidate, transformation = materialized_candidate(task, before)
            evidence["transformation"] = transformation
            compared = comparison_task(task, candidate, reference["proof"])
            equality = self.stage(compared, "True.intro", "comparison")
            evidence["comparison_status"] = equality.get("validity_status")
            if equality.get("validity_status") != "success" or equality.get("kernel_replay") is not True:
                evidence["status"] = "kernel_type_comparison_failed"
                return self.finish(task, ref, evidence)
            candidate = refresh_hashes(candidate)
            after = self.stage(candidate, reference["proof"], "explicit", measure=True)
            evidence.update(explicit_status=after.get("validity_status"), metric_status=after.get("metric_status"),
                            explicit_type_fingerprint=after.get("validation", {}).get("proved_type_fingerprint"))
            if after.get("validity_status") != "success" or after.get("validation", {}).get("kernel_replay") is not True:
                evidence["status"] = "explicit_validation_failed"
                return self.finish(task, ref, evidence)
            task = candidate
            task["audit"].update(reference_verified=True, explicit_signature_frozen=True,
                                 independent_kernel_replay=True, axiom_policy_pass=True,
                                 transitive_forbidden_dependency_pass=True, original_declaration_absent=True,
                                 metric_closure_verified=after.get("metric_status") == "available")
            task["provenance"]["signature_materialization"] = (
                "Trusted missing header parameters added; source proposition unchanged; whole declaration types "
                "compared by Lean.Kernel.isDefEqGuarded modulo ordered universe renaming; original reference "
                "independently replayed and measured without comparison helpers; target autoImplicit=false")
            task["provenance"]["signature_materialization_policy_hash"] = self.policy_hash
            evidence["status"] = "materialized_measurable" if after.get("metric_status") == "available" else "materialized_metrics_unavailable"
            evidence["metric_unavailable_reason"] = after.get("metric_unavailable_reason")
            ref.update(verification_status="isolated_explicit_signature_pass", metric_status=after.get("metric_status"),
                       verification_evidence=f"records/{task['task_id']}/explicit.json",
                       signature_materialization_policy_hash=self.policy_hash)
        except Exception as error:
            evidence.update(status="preparation_error", error=f"{type(error).__name__}: {error}")
        return self.finish(task, ref, evidence)

    def finish(self, task, reference, evidence):
        task["preparation_status"] = evidence["status"]
        refresh_hashes(task)
        record = {"task": task, "reference": reference, "evidence": evidence}
        write_private(self.output / "records" / task["task_id"] / "prepared.json", json_text(record))
        return record


def self_test():
    """Pure transformation tests: no Lean, Docker, network or generation."""
    import unittest

    class OfflineTests(unittest.TestCase):
        def test_header_counts_ignore_proposition_forall_and_nested_delimiters(self):
            value = "{α : Type u} [Fintype α] (x y : α) (h : ∀ z : α, z = x)"
            self.assertEqual(len(source_header_binders(value)), 5)
            self.assertEqual(len(source_header_binders('(x : Nat /- ) -/) (h : x = x)')), 2)

        def test_alignment_uses_all_written_binders(self):
            t = {"binders": "[Fintype α] (x y : α)", "proposition": "∀ z, z = x → y = z"}
            bs = [{"name": name, "binder_info": kind, "type_source": "α"} for name, kind in
                  [("α", "implicit"), ("inst", "instance"), ("x", "explicit"),
                   ("y", "explicit"), ("z", "explicit"), ("a._@._internal.1", "explicit")]]
            self.assertEqual(prefix_count(t, {"binders": bs}), 1)
            bs[3]["name"] = "unexpected"
            with self.assertRaisesRegex(ValueError, "not_unique"):
                prefix_count(t, {"binders": bs})

        def test_empty_header_preserves_proposition_binders(self):
            bs = [{"name": "α", "binder_info": "implicit"}, {"name": "x", "binder_info": "explicit"}]
            self.assertEqual(prefix_count({"binders": "", "proposition": "∀ x : α, x = x"}, {"binders": bs}), 1)
            bs[1]["name"] = "a._@._internal.1"
            self.assertEqual(prefix_count({"binders": "", "proposition": "P → P"}, {"binders": bs}), 1)

        def test_collision_and_ambiguous_anonymous_headers_fail(self):
            bs = [{"name": "a", "binder_info": "instance"}, {"name": "b", "binder_info": "instance"}]
            with self.assertRaisesRegex(ValueError, "not_unique"):
                prefix_count({"binders": "[Fintype α]", "proposition": "True"}, {"binders": bs})

        def test_known_wrappers_lift_and_target_implicit_inference_disabled(self):
            source = {"context": "namespace Foo\nomit [Fintype V] in"}
            prepared, changes = normalize_target_context(source)
            self.assertNotIn(" in", prepared["context"])
            self.assertTrue(prepared["context"].endswith("set_option autoImplicit false\n"))
            self.assertEqual(len(changes), 1)
            with self.assertRaisesRegex(ValueError, "unsupported_target"):
                normalize_target_context({"context": "set_option maxHeartbeats 0 in"})

        def test_materialization_preserves_source_proposition_and_no_reference_in_premises(self):
            t = {"task_id": "fixture", "binders": "(x : α)", "proposition": "∀ y : α, x = y → y = x",
                 "context": "set_option autoImplicit false\n", "premise_context": "Available: Eq.symm"}
            before = {"trusted_telescope": {"binders": [
                {"name": "α", "binder_info": "implicit", "type_source": "Type u"},
                {"name": "x", "binder_info": "explicit", "type_source": "α"},
                {"name": "y", "binder_info": "explicit", "type_source": "α"}]}, "universe_parameters": ["u"]}
            candidate, info = materialized_candidate(t, before)
            self.assertEqual(candidate["binders"], "{«α» : Type u} (x : α)")
            self.assertEqual(candidate["proposition"], t["proposition"])
            self.assertEqual(candidate["premise_context"], t["premise_context"])
            self.assertEqual(info["missing_header_parameter_count"], 1)
            compared = comparison_task(t, candidate, "by intro y h; exact h.symm")
            self.assertIn("Lean.Kernel.isDefEqGuarded", compared["context"])
            self.assertEqual(compared["premise_context"], t["premise_context"])
            self.assertNotIn("Kernel.isDefEqGuarded", candidate["context"])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OfflineTests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_DRAFT / "scoped_tasks.jsonl")
    parser.add_argument("--references", type=Path, default=DEFAULT_DRAFT / "references.jsonl")
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--expected-count", type=int, default=100)
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    for name in ("environment", "config", "engine", "output"):
        if getattr(args, name) is None:
            parser.error(f"--{name} is required for runtime verification")
    if not 1 <= args.workers <= 64:
        parser.error("--workers must be between 1 and 64")
    tasks, references = jsonl(args.tasks), jsonl(args.references)
    if len(tasks) != args.expected_count or len(references) != args.expected_count:
        raise ValueError(f"Expected {args.expected_count} tasks and references; got {len(tasks)} and {len(references)}")
    problems = validate_manifest(tasks) + validate_references(tasks, references)
    if problems:
        raise ValueError("Input validation failed: " + "; ".join(problems))
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task["task_id"]) for task in tasks):
        raise ValueError("Unsafe task_id for private record paths")
    config, environment = read_json(args.config), load_environment(args.environment)
    require_implemented_policy(config)
    if config["validation"].get("isolation") != "docker":
        raise ValueError("OM100 reference preparation requires the enforced Docker runtime")
    if environment.get("docker_image") != config["validation"].get("docker_image"):
        raise ValueError("Environment and validation image identities differ")
    for key in ("lean_toolchain", "mathlib_commit"):
        if environment.get(key) != config["environment"].get(key):
            raise ValueError(f"Environment and config disagree on {key}")
        if any(task.get("environment", {}).get(key) != environment.get(key) for task in tasks):
            raise ValueError(f"Selected task sources and environment disagree on {key}")
    policy = {"version": VERSION, "script_hash": file_hash(Path(__file__)),
              "source_scanner_hash": file_hash(HERE / "preparation/extract_scoped_tasks.py"),
              "evaluator_source_hash": evaluator_hash(), "engine_hash": file_hash(args.engine),
              "tasks_hash": digest(tasks), "references_hash": digest(references),
              "config_hash": digest(config), "environment_hash": environment["environment_hash"],
              "expected_count": args.expected_count, "generation_enabled": False}
    policy_path = args.output / "policy.json"
    if policy_path.exists() and read_json(policy_path) != policy:
        raise ValueError("Preparation identity changed; use a new output directory")
    write_private(policy_path, json_text(policy))
    worker = Materializer(environment, config, args.engine, args.output, policy)
    by_id, refs = {}, {reference["task_id"]: reference for reference in references}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker.run, task, refs[task["task_id"]]): task["task_id"] for task in tasks}
        for future in as_completed(futures):
            record = future.result()
            by_id[record["task"]["task_id"]] = record
            print(f"{len(by_id)}/{len(tasks)} {record['task']['task_id']} {record['evidence']['status']}", flush=True)
    ordered = [by_id[task["task_id"]] for task in tasks]
    for filename, key in (("prepared_tasks.jsonl", "task"), ("prepared_references.jsonl", "reference")):
        write_private(args.output / filename, "".join(json.dumps(record[key], ensure_ascii=False, sort_keys=True) + "\n" for record in ordered))
    summary = {"policy_hash": digest(policy), "completed_at": now(), "selected_count": len(tasks),
               "accounted_count": len(ordered), "statuses": dict(Counter(record["evidence"]["status"] for record in ordered)),
               "model_generation_enabled": False, "tasks": [record["evidence"] for record in ordered],
               "remaining_review": ["Answer-access audit", "Family/dependency review", "Canonical prompt interfaces"]}
    write_private(args.output / "summary.json", json_text(summary))
    print(json_text({key: value for key, value in summary.items() if key != "tasks"}), flush=True)
    return 0 if summary["statuses"].get("materialized_measurable", 0) == len(tasks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
