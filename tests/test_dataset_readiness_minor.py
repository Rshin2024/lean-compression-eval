"""Regression tests for dataset/readiness robustness, provenance and answer-access screening."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.cli import main
from lean_compression.dataset import (
    ANSWER_ACCESS_MIN_CHARS, DOMAINS, SHORT_EXACT_MATCH_MIN_CHARS, answer_access_errors, load_tasks,
    readiness_errors, reference_in_prompt, validate_references,
)
from lean_compression.io import digest, read_jsonl, task_input_hash
from lean_compression.readiness import AUDIT_FLAGS, _source_control, build_readiness_report, write_readiness_report

ROOT = Path(__file__).resolve().parents[1]
SEPARATORS = (" ", " ", "\u0085")


def task(name, split="pilot", domain="sets_logic", origin="fresh", **changes):
    value = {"task_id": name, "family_id": "family_" + name, "domain": domain, "origin": origin,
             "split": split, "stratum": "provisional_easy", "imports": [], "context": "",
             "binders": f"(P{name} : Prop)", "proposition": f"P{name} → P{name}",
             "target_name": "target_" + name, "premise_context": "", "forbidden_declarations": [],
             "audit": {flag: True for flag in AUDIT_FLAGS}}
    value.update(changes)
    return value


def reference(t, proof="by rfl"):
    return {"task_id": t["task_id"], "task_input_hash": task_input_hash(t), "validity_status": "success",
            "environment_hash": "env", "boundary_policy_hash": "policy", "engine_hash": "engine",
            "source_token_policy_hash": "tokens", "metric_status": "available", "proof": proof,
            "validation": {"validity_status": "success", "kernel_replay": True, "axioms": [],
                           "trusted_type_expression": "trusted type"},
            "extraction": {"metric_status": "available", "nodes": {}, "source_tokens": {
                "source": "source", "body_spans": [], "W_B": 2}},
            "metrics": {"metric_status": "available", "W_S": 5, "W_B": 2, "D": 3,
                        "U_S_decimal": "3", "U_B_decimal": "11"}}


FROZEN = {"source_length_cutpoints": [1, 4], "dependency_depth_cutpoints": [1, 4],
          "strata_frozen": True, "difficulty_review_complete": True}


def quota_manifest():
    """The complete 50/200 design: 10 pilot and 30 library + 10 fresh main tasks per domain."""
    tasks = []
    for domain in DOMAINS:
        for split, origins in (("pilot", ["library"] * 10), ("main", ["library"] * 30 + ["fresh"] * 10)):
            for origin in origins:
                tasks.append(task(f"t{len(tasks)}", split, domain, origin))
    return tasks


def write_jsonl(path, rows, ending="\n"):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + ending for row in rows), encoding="utf-8")
    return path


def run_cli(*args):
    out, err = StringIO(), StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            main([str(arg) for arg in args])
        except SystemExit as exit:
            code = exit.code
    return code, out.getvalue(), err.getvalue()


class JsonlReaderTests(unittest.TestCase):
    def test_both_readers_keep_raw_unicode_line_separators_inside_strings(self):
        tasks = [task(f"s{i}", premise_context=f"before{ch}after") for i, ch in enumerate(SEPARATORS)]
        refs = [{"task_id": t["task_id"], "proof": f"by{ch}trivial"} for t, ch in zip(tasks, SEPARATORS)]
        with tempfile.TemporaryDirectory() as folder:
            tasks_path = write_jsonl(Path(folder) / "tasks.jsonl", tasks, "\r\n")
            refs_path = write_jsonl(Path(folder) / "refs.jsonl", refs, "\r")
            for path in (tasks_path, refs_path):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(all(ch in text for ch in SEPARATORS))
            self.assertEqual(load_tasks(tasks_path), tasks)
            self.assertEqual(load_tasks(write_jsonl(Path(folder) / "cr.jsonl", tasks, "\r")), tasks)
            self.assertEqual(read_jsonl(refs_path), refs)
            code, out, err = run_cli("validate-dataset", "--tasks", tasks_path, "--references", refs_path)
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["tasks"], 3)

    def test_errors_cite_physical_line_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tasks.jsonl"
            path.write_text(json.dumps(task("a", premise_context="x y"), ensure_ascii=False) + "\n[]\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"tasks\.jsonl:2: expected a task object"):
                load_tasks(path)
            path.write_text('{"task_id": "a "}\n\n{"task_id": \n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"tasks\.jsonl:3: "):
                read_jsonl(path)
            with self.assertRaisesRegex(ValueError, r"tasks\.jsonl:3: "):
                load_tasks(path)

    def test_directories_are_rejected_with_clean_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "found a directory"):
                load_tasks(folder)
            with self.assertRaisesRegex(ValueError, "found a directory"):
                read_jsonl(folder)
            code, out, err = run_cli("validate-dataset", "--tasks", folder)
            self.assertEqual((code, out), (2, ""))
            self.assertIn("expected a task manifest file, found a directory", err)


class ValidateDatasetGateTests(unittest.TestCase):
    def test_empty_manifest_is_never_ready(self):
        self.assertEqual(readiness_errors([]), ["manifest contains no tasks"])
        with tempfile.TemporaryDirectory() as folder:
            for name, content in (("empty.jsonl", ""), ("blank.jsonl", " \n\n"), ("list.json", "[]"),
                                  ("object.json", '{"tasks": []}')):
                path = Path(folder) / name
                path.write_text(content)
                code, out, err = run_cli("validate-dataset", "--tasks", path)
                self.assertEqual(code, 0, err)
                self.assertFalse(json.loads(out)["ready_for_confirmatory_run"], name)
                code, out, err = run_cli("validate-dataset", "--tasks", path, "--require-ready")
                self.assertEqual(code, 2, name)
                self.assertIn("manifest contains no tasks", err)

    def test_malformed_audit_and_task_ids_are_listed_instead_of_raising(self):
        self.assertEqual(readiness_errors([task("a", audit=None)]), ["a: audit must be an object"])
        self.assertEqual(readiness_errors([task("a", audit=[])]), ["a: audit must be an object"])
        self.assertEqual(readiness_errors([[]]), ["row 1: expected a task object"])
        unnamed = {k: v for k, v in task("b").items() if k != "task_id"}
        refs = [{"task_id": "a", "proof": "by trivial"}]
        self.assertEqual(validate_references([task("a"), unnamed, task("c", task_id=["c"])], refs), [])
        with tempfile.TemporaryDirectory() as folder:
            refs_path = write_jsonl(Path(folder) / "refs.jsonl", refs)
            path = write_jsonl(Path(folder) / "audit.jsonl", [task("a", audit=None)])
            code, out, err = run_cli("validate-dataset", "--tasks", path, "--references", refs_path)
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["readiness_errors"], ["a: audit must be an object"])
            code, out, err = run_cli("validate-dataset", "--tasks", path, "--require-ready")
            self.assertEqual(code, 2)
            self.assertIn("a: audit must be an object", err)
            for rows in ([task("a"), unnamed], [task("a"), task("c", task_id=["c"])]):
                path = write_jsonl(Path(folder) / "ids.jsonl", rows)
                code, out, err = run_cli("validate-dataset", "--tasks", path, "--references", refs_path)
                self.assertEqual(code, 2)
                self.assertNotIn("Traceback", err)
                self.assertRegex(err, "row 2: missing task_id|task_id must be a string")

    def test_malformed_reference_rows_are_listed_instead_of_raising(self):
        rows = [[1, 2], {"task_id": ["a"], "proof": "by trivial"}]
        self.assertEqual(validate_references([task("a")], rows),
                         ["reference row 1: expected an object with a string task_id",
                          "reference row 2: expected an object with a string task_id",
                          "a: expected one private reference, found 0"])
        with tempfile.TemporaryDirectory() as folder:
            tasks = write_jsonl(Path(folder) / "tasks.jsonl", [task("a")])
            refs = write_jsonl(Path(folder) / "refs.jsonl", rows)
            code, out, err = run_cli("validate-dataset", "--tasks", tasks, "--references", refs)
        self.assertEqual((code, out), (2, ""))
        self.assertNotIn("Traceback", err)
        self.assertIn("reference row 1: expected an object with a string task_id\n"
                      "reference row 2: expected an object with a string task_id", err)


class ReadinessReportTests(unittest.TestCase):
    def test_structurally_invalid_manifests_still_produce_a_report(self):
        cases = {"origin": task("a", origin=None), "domain": task("a", domain=None),
                 "family": {k: v for k, v in task("a").items() if k != "family_id"},
                 "task_id": {k: v for k, v in task("a").items() if k != "task_id"},
                 "split": {k: v for k, v in task("a").items() if k != "split"},
                 "family_list": task("a", family_id=["f"]), "audit": task("a", audit=None)}
        for name, broken in cases.items():
            other = task("b")
            refs = [reference(other)] + ([reference(broken)] if "task_id" in broken else [])
            result = build_readiness_report([broken, other], refs, {"enforce_quotas": False})
            self.assertEqual(result["gates"]["structural_manifest_valid"], name == "audit", name)
            self.assertEqual(result["status"], "not_ready_for_dataset_freeze")
            # The pilot fit population counts identified rows by their split, if any.
            self.assertTrue(result["proposed_reference_strata"]["fit_population_complete"], name)
            if name == "audit":
                self.assertEqual(result["tasks"]["a"]["pending_audits"], list(AUDIT_FLAGS))
            with tempfile.TemporaryDirectory() as directory:
                write_readiness_report(result, directory)
        self.assertNotIn("a", build_readiness_report([cases["task_id"]], [], {"enforce_quotas": False})["tasks"])

    def test_evidence_without_a_string_task_id_is_ignored(self):
        t = task("one")
        result = build_readiness_report([t], [{"task_id": ["one"]}, [1, 2], reference(t)], {"enforce_quotas": False})
        self.assertEqual(result["ignored_reference_records"], [{"reason": "not_a_task_reference_record"}] * 2)
        self.assertTrue(result["tasks"]["one"]["reference_evidence_complete"])

    def test_relaxed_quotas_are_recorded_and_block_dataset_freeze(self):
        t = task("only")
        result = build_readiness_report([t], [reference(t)], {"readiness": {"enforce_quotas": False, **FROZEN}})
        self.assertEqual(result["status"], "not_ready_for_dataset_freeze")
        self.assertEqual([gate for gate, passed in result["gates"].items() if not passed], ["quotas_enforced"])
        settings = result["readiness_settings"]
        self.assertFalse(settings["enforce_quotas"])
        self.assertEqual(settings["cutpoint_source"], "caller_supplied")
        self.assertEqual(settings["readiness_config_hash"], digest({"enforce_quotas": False, **FROZEN}))
        pinned = build_readiness_report([t], [reference(t)], {"enforce_quotas": False, "engine_hash": "engine"})
        self.assertEqual(pinned["readiness_settings"]["supplied_pins"]["engine_hash"], "engine")
        self.assertEqual(pinned["readiness_settings"]["cutpoint_source"], "observed_reference_25th_and_75th_percentiles")
        self.assertNotEqual(pinned["readiness_settings"]["config_hash"], settings["config_hash"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("quotas <strong>not enforced</strong>", page)
        self.assertIn(settings["readiness_config_hash"], page)

    def test_complete_quota_manifest_can_be_ready_only_with_quotas_enforced(self):
        tasks = quota_manifest()
        refs = [reference(t) for t in tasks]
        result = build_readiness_report(tasks, refs, {"readiness": FROZEN})
        self.assertEqual(result["status"], "ready_for_dataset_freeze", result["gates"])
        self.assertTrue(result["gates"]["quotas_enforced"])
        self.assertTrue(result["readiness_settings"]["enforce_quotas"])
        relaxed = build_readiness_report(tasks, refs, {"readiness": {**FROZEN, "enforce_quotas": False}})
        self.assertEqual(relaxed["status"], "not_ready_for_dataset_freeze")

    def test_any_bare_single_tactic_is_labeled_with_its_name(self):
        for proof, tactic in (("by grind", "grind"), ("by nlinarith", "nlinarith"), ("by\n  norm_num", "norm_num"),
                              ("by aesop?", "aesop?"), ("by linarith!", "linarith!"), ("by simp_all", "simp_all")):
            self.assertEqual(_source_control({"proof": proof})["classification"], "single_tactic_wrapper", proof)
            self.assertEqual(_source_control({"proof": proof})["tactic"], tactic)
        self.assertEqual(_source_control({"proof": "by rfl"})["classification"], "literal_reflexivity")
        for proof in ("by simp [h]", "by simp[h]", "by (grind)", "by grind;", "by exact h", "by intro h; exact h",
                      "by simp at h", "fun h => h"):
            self.assertIsNone(_source_control({"proof": proof})["classification"], proof)
        a, b, c = task("a"), task("b"), task("c")
        result = build_readiness_report([a, b, c], [reference(a, proof="by grind"), reference(b, proof="by grind"),
                                                    reference(c, proof="by omega")], {"enforce_quotas": False})
        self.assertEqual(result["source_only_controls"]["single_tactic_wrapper"], ["a", "b", "c"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("single_tactic_wrapper: 3 tasks (grind 2, omega 1)", page)

    def test_manifest_digest_scheme_and_task_file_hash_are_explicit(self):
        t = task("one")
        result = build_readiness_report([t], [reference(t)], {"enforce_quotas": False}, task_file_sha256="f" * 64)
        self.assertEqual(result["task_manifest_hash"], digest([t]))
        self.assertEqual(result["task_manifest_hash_method"], "sha256_of_canonical_json_of_parsed_task_list")
        self.assertEqual(result["task_file_sha256"], "f" * 64)
        self.assertIsNone(build_readiness_report([t], [], {"enforce_quotas": False})["task_file_sha256"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("Canonical task-list digest (SHA-256 of canonical JSON of the parsed tasks", page)
        self.assertIn("Task file SHA-256: <code>" + "f" * 64, page)


class ReadinessCommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks = write_jsonl(self.root / "tasks.jsonl", [task("one")])
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"readiness": {"enforce_quotas": False}}))
        self.evidence = self.root / "evidence"
        self.evidence.mkdir()

    def tearDown(self):
        self.folder.cleanup()

    def readiness(self, *references, tasks=None, output="out"):
        args = ["readiness", "--tasks", tasks or self.tasks, "--config", self.config, "--output", self.root / output]
        for path in references:
            args += ["--references", path]
        return run_cli(*args)

    def test_reference_arguments_must_be_evidence_directories(self):
        refs = write_jsonl(self.root / "refs.jsonl", [{"task_id": "one", "proof": "by trivial"}])
        for path in (refs, self.root / "missing"):
            code, out, err = self.readiness(self.evidence, path)
            self.assertEqual((code, out), (2, ""))
            self.assertIn("must be an existing verify-references output directory", err)
        self.assertFalse((self.root / "out").exists())
        code, out, err = self.readiness(self.evidence, tasks=self.root)
        self.assertEqual(code, 2)
        self.assertIn("found a directory", err)

    def test_non_object_evidence_is_ignored_explicitly(self):
        (self.evidence / "list.json").write_text("[1, 2]")
        (self.evidence / "bad_id.json").write_text('{"task_id": ["one"]}')
        (self.evidence / "policy.json").write_text('{"kind": "verify-references"}')
        (self.evidence / "one.json").write_text(json.dumps(reference(task("one"))))
        code, out, err = self.readiness(self.evidence)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["reference_records"], 1)
        report = json.loads((self.root / "out" / "readiness.json").read_text())
        self.assertEqual([(Path(r["evidence_source_path"]).name, r["reason"]) for r in report["ignored_reference_records"]],
                         [("bad_id.json", "not_a_task_reference_record"), ("list.json", "not_a_task_reference_record")])
        self.assertTrue(report["tasks"]["one"]["reference_evidence_complete"])

    def test_report_records_the_task_file_sha256(self):
        code, out, err = self.readiness(self.evidence)
        self.assertEqual(code, 0, err)
        report = json.loads((self.root / "out" / "readiness.json").read_text())
        self.assertEqual(report["task_file_sha256"], hashlib.sha256(self.tasks.read_bytes()).hexdigest())
        self.assertEqual(report["task_manifest_hash"], digest(load_tasks(self.tasks)))


class AnswerAccessTests(unittest.TestCase):
    SIBLING = "⟨hf.1, fun x hx => by simpa only [← hfg hx] using hf.2 hx⟩"

    def leaking_task(self):
        context = f"theorem ConvexOn.congr (hf : ConvexOn 𝕜 s f) :\n  ConvexOn 𝕜 s g :=\n  {self.SIBLING}\n"
        return task("leak", premise_context=context)

    def test_reference_proof_visible_in_prompt_is_flagged(self):
        t = self.leaking_task()
        reformatted = self.SIBLING.replace(" => ", " =>\n    ")
        for proof in (self.SIBLING, reformatted):
            self.assertTrue(reference_in_prompt(t, proof))
            self.assertEqual(answer_access_errors([t], [{"task_id": "leak", "proof": proof}]),
                             ["leak: private reference proof occurs in the model-visible prompt (answer access)"])
        stated = task("stated", binders="(h : Nat.succ_le_of_lt (Nat.lt_succ_self 0) = rfl)")
        self.assertTrue(reference_in_prompt(stated, "Nat.succ_le_of_lt (Nat.lt_succ_self 0)"))
        self.assertFalse(reference_in_prompt(t, "by simpa using hf.2 hx hy"))
        self.assertIsNone(reference_in_prompt(t, None))
        self.assertIsNone(reference_in_prompt(t, " \n"))
        # A task that cannot be rendered is unchecked whatever the proof's length.
        self.assertIsNone(reference_in_prompt({"task_id": "x"}, self.SIBLING))
        self.assertIsNone(reference_in_prompt({"task_id": "x"}, "rfl"))

    def test_generic_short_proofs_are_not_answer_access(self):
        t = task("short", premise_context="theorem a : x = x := rfl\ntheorem b : y := by simp\n"
                                          "theorem c : z := dite_eq_right h")
        for proof in ("rfl", "by simp"):
            self.assertTrue(reference_in_prompt(t, proof))
            self.assertEqual(answer_access_errors([t], [{"task_id": "short", "proof": proof}]), [])
        # Not generic: curation amended lib_sets_logic_main_equiv_basic_10 for this 15-character proof.
        self.assertEqual(answer_access_errors([t], [{"task_id": "short", "proof": "dite_eq_right h"}]),
                         ["short: private reference proof occurs in the model-visible prompt (answer access)"])

    def test_threshold_gates_from_fifteen_characters_and_lists_shorter_matches(self):
        self.assertEqual((SHORT_EXACT_MATCH_MIN_CHARS, ANSWER_ACCESS_MIN_CHARS), (9, 15))
        tasks, refs = [], []
        for size in (8, 9, 14, 15, 16, 17, 18, 19):
            # The private copy wraps differently; both normalize to "by x...x" of `size` characters.
            body = "x" * (size - 3)
            t = task(f"n{size}", premise_context=f"theorem sibling : P := by {body}\n")
            tasks.append(t)
            refs.append(reference(t, proof=f"by\n    {body}"))
            self.assertTrue(reference_in_prompt(t, refs[-1]["proof"]), size)
        gated = ["n15", "n16", "n17", "n18", "n19"]
        self.assertEqual(answer_access_errors(tasks, refs), [
            f"{name}: private reference proof occurs in the model-visible prompt (answer access)" for name in gated])
        result = build_readiness_report(tasks, refs, {"enforce_quotas": False})
        screen = result["answer_access_screen"]
        self.assertEqual((screen["flagged_task_ids"], screen["short_exact_match_task_ids"], screen["unchecked_task_ids"]),
                         (gated, ["n9", "n14"], []))
        self.assertTrue(all(row["reference_proof_in_prompt"] for row in result["tasks"].values()))
        self.assertFalse(result["gates"]["reference_proofs_absent_from_prompts"])
        short = build_readiness_report(tasks[:3], refs[:3], {"enforce_quotas": False})
        self.assertTrue(short["gates"]["reference_proofs_absent_from_prompts"])
        self.assertEqual(short["answer_access_screen"]["short_exact_match_task_ids"], ["n9", "n14"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(short, directory)["html"]).read_text()
        self.assertIn("at least 15 characters occurs in the model-visible prompt: none. Shorter exact matches: n9, n14.", page)

    def test_validate_dataset_lists_answer_access_as_a_readiness_error(self):
        t = self.leaking_task()
        with tempfile.TemporaryDirectory() as folder:
            tasks = write_jsonl(Path(folder) / "tasks.jsonl", [t])
            refs = write_jsonl(Path(folder) / "refs.jsonl", [{"task_id": "leak", "proof": self.SIBLING}])
            code, out, err = run_cli("validate-dataset", "--tasks", tasks, "--references", refs)
            self.assertEqual(code, 0, err)
            result = json.loads(out)
            self.assertEqual(result["structural_status"], "valid")
            self.assertIn("leak: private reference proof occurs in the model-visible prompt (answer access)",
                          result["readiness_errors"])
            self.assertFalse(result["ready_for_confirmatory_run"])
            code, out, err = run_cli("validate-dataset", "--tasks", tasks, "--references", refs, "--require-ready")
            self.assertEqual(code, 2)
            self.assertIn("leak: private reference proof", err)

    def test_readiness_gate_lists_flagged_and_unchecked_tasks(self):
        tasks = quota_manifest()
        tasks[0]["premise_context"] = self.leaking_task()["premise_context"]
        refs = [reference(tasks[0], proof=self.SIBLING)] + [reference(t) for t in tasks[1:]]
        result = build_readiness_report(tasks, refs, {"readiness": FROZEN})
        self.assertEqual([gate for gate, passed in result["gates"].items() if not passed],
                         ["reference_proofs_absent_from_prompts"])
        self.assertEqual(result["status"], "not_ready_for_dataset_freeze")
        self.assertEqual(result["answer_access_screen"]["flagged_task_ids"], [tasks[0]["task_id"]])
        self.assertTrue(result["tasks"][tasks[0]["task_id"]]["reference_proof_in_prompt"])
        unchecked = reference(tasks[0])
        del unchecked["proof"]
        result = build_readiness_report(tasks, [unchecked] + refs[1:], {"readiness": FROZEN})
        self.assertEqual(result["answer_access_screen"]["unchecked_task_ids"], [tasks[0]["task_id"]])
        self.assertFalse(result["gates"]["reference_proofs_absent_from_prompts"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("Answer-access screen", page)
        self.assertIn("Unchecked: 1 tasks", page)

    def test_committed_data_screen_matches_the_recorded_answer_access_amendment(self):
        paths = [ROOT / "data/public/tasks.jsonl", ROOT / "data/private/references.jsonl",
                 ROOT / "data/private/tasks-before-answer-access-and-audit-amendment.json"]
        if not all(path.exists() for path in paths):
            self.skipTest("Committed task manifest, private references or pre-amendment rows are unavailable")
        current, references = load_tasks(paths[0]), read_jsonl(paths[1])
        before = {t["task_id"]: t for t in load_tasks(paths[2])}
        earlier = [before.get(t["task_id"], t) for t in current]
        # Amendment-log rows 417-424 replaced the copied predecessor proofs in these prompts.
        amended = ["lib_algebra_groups_pilot_group_basic_02", "lib_algebra_groups_pilot_group_basic_06",
                   "lib_algebra_groups_pilot_group_basic_08", "lib_analysis_inequalities_main_convex_function_06",
                   "lib_analysis_inequalities_main_convex_function_08", "lib_analysis_inequalities_main_convex_function_10",
                   "lib_sets_logic_main_equiv_basic_04", "lib_sets_logic_main_equiv_basic_10"]
        proofs = {r["task_id"]: r["proof"] for r in references}
        for tasks, flagged in ((current, []), (earlier, amended)):
            self.assertEqual([error.split(":")[0] for error in answer_access_errors(tasks, references)], flagged)
            screen = build_readiness_report(tasks, [reference(t, proofs[t["task_id"]]) for t in tasks],
                                            {"enforce_quotas": False})["answer_access_screen"]
            self.assertEqual(screen["flagged_task_ids"], flagged)
            # "dite_eq_left h" (14 characters) remains for the human answer-access audit.
            self.assertEqual(screen["short_exact_match_task_ids"], ["lib_sets_logic_main_equiv_basic_09"])


if __name__ == "__main__":
    unittest.main()
