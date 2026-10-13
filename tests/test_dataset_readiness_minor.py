"""Regression tests for dataset/readiness robustness, provenance and answer-access screening."""
from contextlib import redirect_stderr, redirect_stdout
from fractions import Fraction
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.cli import main
from lean_compression.dataset import (
    ANSWER_ACCESS_MIN_CHARS, DOMAINS, NEAR_VERBATIM_MAX_COMPONENT_FRACTION, NEAR_VERBATIM_MAX_TOKEN_EDITS,
    NEAR_VERBATIM_TOKENS_PER_EDIT, SHORT_EXACT_MATCH_MIN_CHARS, answer_access_errors, binder_names,
    declaration_values, generic_proof_shape, load_tasks, name_components, near_verbatim_matches, proof_components,
    proof_tokens, readiness_errors, reference_in_prompt, screen_answer_access, validate_references,
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

    def test_any_bare_single_tactic_is_labeled_without_naming_the_tactic(self):
        # For "by <tactic>" the tactic is the whole private answer, so neither the task's row nor a per-tactic
        # tally over the listed task IDs names it: only the label and the number of labeled tasks are reported.
        for proof in ("by grind", "by positivity", "by\n  norm_num", "by aesop?", "by linarith!", "by simp_all"):
            control = _source_control({"proof": proof})
            self.assertEqual(control["classification"], "single_tactic_wrapper", proof)
            self.assertEqual(set(control), {"classification", "source_available", "interpretation"}, proof)
            self.assertNotIn(proof.split()[-1], json.dumps(control), proof)
        self.assertEqual(_source_control({"proof": "by rfl"})["classification"], "literal_reflexivity")
        for proof in ("by simp [qux_corge_def, hq]", "by simp[qux_corge_def, hq]", "by (grind)", "by grind;", "by exact h",
                      "by intro h; exact h", "by simp at hq ⊢", "fun h => h"):
            self.assertIsNone(_source_control({"proof": proof})["classification"], proof)
        a, b, c = task("a"), task("b"), task("c")
        result = build_readiness_report([a, b, c], [reference(a, proof="by positivity"), reference(b, proof="by positivity"),
                                                    reference(c, proof="by norm_num")], {"enforce_quotas": False})
        self.assertEqual(result["source_only_controls"]["single_tactic_wrapper"], ["a", "b", "c"])
        self.assertEqual(result["groups"]["all"]["source_control_counts"], {"single_tactic_wrapper": 3})
        self.assertEqual([set(row["source_control"]) for row in result["tasks"].values()],
                         [{"classification", "source_available", "interpretation"}] * 3)
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("<li>single_tactic_wrapper: 3 tasks</li>", page)
        for text in (json.dumps(result), page):
            self.assertNotIn("positivity", text)
            self.assertNotIn("norm_num", text)

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
    SIBLING = "⟨hq.1, fun x hx => by simpa only [← hpq hx] using hq.2 hx⟩"

    def leaking_task(self):
        context = f"theorem Foo.congr (hq : Foo s q) :\n  Foo s p :=\n  {self.SIBLING}\n"
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
        self.assertFalse(reference_in_prompt(t, "by simpa using hq.2 hx hy"))
        self.assertIsNone(reference_in_prompt(t, None))
        self.assertIsNone(reference_in_prompt(t, " \n"))
        # A task that cannot be rendered is unchecked whatever the proof's length.
        self.assertIsNone(reference_in_prompt({"task_id": "x"}, self.SIBLING))
        self.assertIsNone(reference_in_prompt({"task_id": "x"}, "rfl"))

    def test_generic_short_proofs_are_not_answer_access(self):
        t = task("short", premise_context="theorem a : x = x := rfl\ntheorem b : y := by simp\n"
                                          "theorem c : z := foo_ne_right h")
        for proof in ("rfl", "by simp"):
            self.assertTrue(reference_in_prompt(t, proof))
            self.assertEqual(answer_access_errors([t], [{"task_id": "short", "proof": proof}]), [])
        # Not generic: an application of 9 or more characters gates.
        self.assertEqual(answer_access_errors([t], [{"task_id": "short", "proof": "foo_ne_right h"}]),
                         ["short: private reference proof occurs in the model-visible prompt (answer access)"])

    def test_threshold_gates_from_fifteen_characters_and_from_nine_unless_generic(self):
        self.assertEqual((SHORT_EXACT_MATCH_MIN_CHARS, ANSWER_ACCESS_MIN_CHARS), (9, 15))
        tasks, refs, sizes = [], [], {}
        for size in (8, 9, 14, 15, 16, 17, 18, 19):
            for shape, head in (("app", "f"), ("tactic", "by")):
                # The private copy wraps differently; both normalize to "<head> x...x" of `size` characters.
                body = "x" * (size - len(head) - 1)
                t = task(f"{shape}{size}", premise_context=f"theorem sibling : P := {head} {body}\n")
                tasks.append(t)
                refs.append(reference(t, proof=f"{head}\n    {body}"))
                sizes[t["task_id"]] = size
                self.assertTrue(reference_in_prompt(t, refs[-1]["proof"]), size)
        names = [t["task_id"] for t in tasks]
        flagged = [name for name in names if sizes[name] >= 15]
        # From 9 characters an application gates; one bare tactic is a generic shape.
        short, generic = ["app9", "app14"], ["tactic9", "tactic14"]
        self.assertEqual(answer_access_errors(tasks, refs), [
            f"{name}: private reference proof occurs in the model-visible prompt (answer access)"
            for name in names if name in flagged + short])
        result = build_readiness_report(tasks, refs, {"enforce_quotas": False})
        screen = result["answer_access_screen"]
        self.assertEqual((screen["flagged_task_ids"], screen["short_exact_match_task_ids"],
                          screen["generic_short_exact_match_task_ids"], screen["unchecked_task_ids"]),
                         (flagged, short, generic, []))
        self.assertTrue(all(row["reference_proof_in_prompt"] for row in result["tasks"].values()))
        self.assertFalse(result["gates"]["reference_proofs_absent_from_prompts"])
        below = build_readiness_report(tasks[:6], refs[:6], {"enforce_quotas": False})
        self.assertFalse(below["gates"]["reference_proofs_absent_from_prompts"])
        clear = build_readiness_report(tasks[1:6:2], refs[1:6:2], {"enforce_quotas": False})
        self.assertEqual([t["task_id"] for t in tasks[1:6:2]], ["tactic8"] + generic)
        self.assertTrue(clear["gates"]["reference_proofs_absent_from_prompts"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(below, directory)["html"]).read_text()
        self.assertIn("at least 15 characters occurs in the model-visible prompt: none. Short exact matches gated when a "
                      "whitespace-normalized reference proof of 9-14 characters", page)
        self.assertIn("binders): app9, app14. Generic short exact matches, not gated: tactic9, tactic14.", page)

    def test_short_exact_matches_gate_unless_the_proof_is_a_generic_shape(self):
        shown = ["foo_bar h", "foo_ba h", "foo_ne_left h", "by positivity", "by exact rfl", "Nat.le_refl",
                 "by exact foo", "by measurability"]
        context = "".join(f"theorem s{i} : P := {proof}\n" for i, proof in enumerate(shown))
        t = task("short", premise_context=context, binders="(hfg_le_xy : P) {«α» : Type}")
        expected = {"foo_bar h": (True, None), "foo_ba h": (False, None), "foo_ne_left h": (True, None),
                    "by positivity": (False, "single_tactic"), "by exact rfl": (False, "literal_reflexivity"),
                    "hfg_le_xy": (False, "binder_name"), "Nat.le_refl": (True, None), "by exact foo": (True, None),
                    # From 15 characters every exact match gates, the rule of amendment-log rows 417-424.
                    "by measurability": (True, "single_tactic")}
        for proof, (gated, shape) in expected.items():
            screen = screen_answer_access(t, proof)
            self.assertTrue(screen["exact_match"], proof)
            self.assertEqual((screen["gated_exact_match"], screen["generic_shape"]), (gated, shape), proof)
            self.assertEqual(len(answer_access_errors([t], [{"task_id": "short", "proof": proof}])), int(gated), proof)
        self.assertEqual(binder_names("(hfg_le_xy : P) {«α» : Type} [Decidable p] (a b : Fin (n : ℕ))"),
                         {"hfg_le_xy", "α", "a", "b"})
        self.assertIsNone(generic_proof_shape(t, "Nat.le_refl"))
        # Outside any proof component (here the statement's binders) only the exact rules see the copy.
        stated = task("stated", binders="(hx : foo_bar h = 1)")
        result = build_readiness_report([stated], [reference(stated, proof="foo_bar h")], {"enforce_quotas": False})
        screen = result["answer_access_screen"]
        self.assertEqual((screen["short_exact_match_task_ids"], screen["near_verbatim_task_ids"]), (["stated"], []))
        self.assertFalse(result["gates"]["reference_proofs_absent_from_prompts"])

    @staticmethod
    def near(value, proof, header="theorem sibling (h : P) : Q :=\n  "):
        """near_verbatim_matches of proof against a context whose one declaration value is value (line 2)."""
        return near_verbatim_matches(task("near", premise_context=header + value + "\n\nuniverse u\n"), proof)

    def test_near_verbatim_sibling_proofs_are_answer_access(self):
        cases = [  # (sibling value, reference proof, token edits, component edits); invented names throughout
            ("by\n    ext\n    simp only [foos, mem_filter, mem_range]\n    exact fun ha _ => pos_of_foo ha h",
             "by\n  ext\n  simp only [properFoos, mem_filter, mem_range]\n  exact fun ha _ => pos_of_foo ha h", 1, 1),
            ("foo_ne_left h", "foo_ne_right h", 1, 1),
            ("fun k ↦ by rw [← qux_comm, g.symm]", "fun k ↦ by rw [qux_comm, ← g.symm]", 2, 2),
            ("by simp [Surjective, Qux.foo_lt_foo_iff_left hn]", "by simp [Surjective, Qux.foo_lt_foo_iff_right ha]", 2, 2),
            ("(qux_foos.lt g).1", "(qux_foos.lt g).2", 1, 1),
            ("foo_bar x y z w", "bar_foo x y z w", 1, 1),
            # A left/right dual with swapped arguments: one substitution and one swap.
            ("q.comp (foo_bar_left u v)", "q.comp (foo_bar_right v u)", 2, 2),
            # Three substitutions in a 10-token dual.
            ("⟨K.qux_of_qux_add_right, fun g => qux_add_of_qux_left g k⟩",
             "⟨K.qux_of_qux_add_left, fun g => qux_add_of_qux_right g j⟩", 3, 3),
            # A notation is one token: fooHom -> nnfooHom and 𝕜 -> 𝕜≥0 are two edits.
            ("qux_foo₀ (fooHom : β →*₀ 𝕜) c d", "qux_foo₀ (nnfooHom : β →*₀ 𝕜≥0) c d", 2, 2),
            # Trailing arguments dropped from an 8-token proof: three edits.
            ("qux_foo₀ (fooHom : β →*₀ 𝕜) c d", "qux_bar₀ (fooHom : β →*₀ 𝕜)", 3, 3),
            # Short non-generic answers: 1 of 2 and 2 of 5 name components, a third rounded to the nearest.
            ("lefty_qux", "righty_qux", 1, 1),
            ("Qux.foo_foo_bar_baz", "Qux.foo_bar_quux_baz", 1, 2),
        ]
        for value, proof, edits, components in cases:
            t = task("near", premise_context=f"theorem sibling (h : P) : Q :=\n  {value}\n\nuniverse u\n")
            matches = near_verbatim_matches(t, proof)
            self.assertEqual([(m["token_edits"], m["component_edits"], m["snippet"], m["premise_context_line"],
                               m["declaration_value"]) for m in matches][:1],
                             [(edits, components, " ".join(value.split()), 2, True)], proof)
            self.assertFalse(screen_answer_access(t, proof)["exact_match"], proof)
            self.assertEqual(answer_access_errors([t], [{"task_id": "near", "proof": proof}]), [
                f"near: premise_context line 2 is a near-verbatim copy of the private reference proof "
                f"({edits} token edit{'' if edits == 1 else 's'}; answer access)"])

    def test_tokens_are_runs_without_whitespace_brackets_commas_or_semicolons(self):
        self.assertEqual(proof_tokens("by rw [← h, Nat.succ_le_iff] <;> exact h.1 -- done\n"),
                         ["by", "rw", "[", "←", "h", ",", "Nat.succ_le_iff", "]", "<;>", "exact", "h.1"])
        self.assertEqual(proof_tokens("qux_foo₀ (nnfooHom : β →*₀ 𝕜≥0) c⁻¹ d; ext;simp<;>rfl /- c -/ «x y».z"),
                         ["qux_foo₀", "(", "nnfooHom", ":", "β", "→*₀", "𝕜≥0", ")", "c⁻¹", "d", ";", "ext", ";",
                          "simp", "<;>", "rfl", "«x y».z"])
        self.assertEqual(name_components(["Nat.succ_le_iff", "_", "h.1", "←", "«a b»", "ℝ≥0", ".1", ".."]),
                         ["Nat", "succ", "le", "iff", "_", "h", "1", "←", "«a b»", "ℝ≥0", "1", ".."])
        # Split into 𝕜, ≥ and 0, the notation would cost two more edits and a swap would push this past 3.
        self.assertEqual([(m["token_edits"], m["component_edits"]) for m in self.near(
            "qux_foo₀ (fooHom : β →*₀ 𝕜) d c", "qux_foo₀ (nnfooHom : β →*₀ 𝕜≥0) c d")], [(3, 3)])

    def test_a_swap_of_two_adjacent_tokens_is_one_edit(self):
        self.assertEqual(NEAR_VERBATIM_MAX_TOKEN_EDITS, 3)
        # One substitution and two swaps: three edits; without swaps, five.
        self.assertEqual([(m["token_edits"], m["component_edits"]) for m in self.near(
            "Foo.bar_baz_left b c x z y", "Foo.bar_baz_right c b x y z")], [(3, 3)])
        self.assertEqual(self.near("Foo.bar_baz_left b c z x y", "Foo.bar_baz_right c b x y z"), [])

    def test_token_bound_is_three_edits_or_one_per_six_reference_tokens(self):
        self.assertEqual((NEAR_VERBATIM_MAX_TOKEN_EDITS, NEAR_VERBATIM_TOKENS_PER_EDIT), (3, 6))

        def rules(head, count, changed):
            names = [f"lem_{i}{'x' if i < changed else ''}" for i in range(count)]
            return f"{head} [{', '.join(names)}]"

        for head, count, edits, flagged in (
                ("by simp only", 7, 3, True),   # 18 tokens: 3 edits
                ("by simp only", 7, 4, False),  # 18 tokens: still 3
                ("by simp", 8, 4, True),        # 19 tokens: ceil(19 / 6) = 4
                ("by simp", 8, 5, False),
                ("by simp only", 10, 4, True),  # 24 tokens: 4
                ("by simp only", 10, 5, False),
                ("by simp only", 16, 6, True),  # 36 tokens: 6
                ("by simp only", 16, 7, False)):
            proof = rules(head, count, 0)
            matches = self.near(rules(head, count, edits), proof)
            self.assertEqual(bool(matches), flagged, (len(proof_tokens(proof)), edits))
            if matches:
                self.assertEqual((matches[0]["token_edits"], matches[0]["reference_token_count"]), (edits, len(proof_tokens(proof))))

    def test_component_bound_is_a_third_rounded_to_the_nearest_component(self):
        self.assertEqual(NEAR_VERBATIM_MAX_COMPONENT_FRACTION, Fraction(1, 3))
        for value, proof, changed, flagged in (
                ("lefty_qux", "righty_qux", "1 of 2", True),
                ("qux_ne_bar h", "foo_ne_right h", "2 of 4", False),
                ("Qux.foo_foo_bar_baz", "Qux.foo_bar_quux_baz", "2 of 5", True),
                ("Qux.foo_foo_bar", "Qux.foo_bar_quux_baz", "3 of 5", False),
                ("Qux.corge _ _", "Qux.grault_waldo_grault _ _", "3 of 6", False),
                ("foo_bar x y z w", "bar_foo x y z w", "1 of 6, a token swap within one name", True),
                ("by rw [bar_of_ne_one hy.lt]\n  congr\n  exact qux_of_top hy",
                 "by rw [foo_bar_eq_baz hy.lt]\n  exact qux_of_top hy", "5 of 15", True),
                ("by rw [bar_of_ne_one hy.lt]\n  congr\n  exact qux_of_bot hy",
                 "by rw [foo_bar_eq_baz hy.lt]\n  exact qux_of_top hy", "6 of 15", False),
                ("rfl", "foo_ne_right h", "4 of 4", False)):
            self.assertEqual(bool(self.near(value, proof)), flagged, (proof, changed))

    def test_near_verbatim_floor_is_nine_characters_and_generic_shapes_are_exempt(self):
        # 9 characters, not generic: checked.  8 characters: not checked.
        self.assertEqual(len(self.near("foo_baz h", "foo_bar h")), 1)
        self.assertEqual(self.near("foo_bb h", "foo_ba h"), [])
        # A generic shape names nothing specific to the task, so a sibling one edit away is no copy of it.
        t = task("generic", premise_context="theorem s : P := by linarith\ntheorem r : P := by exact Eq.rfl\n"
                                            "theorem b : P := hfg_le_xz\ntheorem m : P := by continuity\n",
                 binders="(hfg_le_xy : P)")
        for proof in ("by positivity", "by exact Iff.rfl", "hfg_le_xy", "by measurability"):
            self.assertIsNotNone(generic_proof_shape(t, proof), proof)
            self.assertEqual(near_verbatim_matches(t, proof), [], proof)
            self.assertEqual(answer_access_errors([t], [{"task_id": "generic", "proof": proof}]), [], proof)
        # The same edit against a proof that is not a generic shape is a copy.
        self.assertEqual(len(self.near("by exact Eq.rfl", "by exact Iff.rfl _")), 1)
        self.assertIsNone(near_verbatim_matches(task("none"), None))
        self.assertIsNone(screen_answer_access({"task_id": "x"}, "foo_ne_right h")["near_verbatim"])

    def test_proof_components_are_the_terms_that_can_stand_as_a_proof(self):
        source = ("def fooPreimage : A ≃ B where\n"
                  "  toFun x := x\n"
                  "  invFun x :=\n    ⟨fun a => a, funext fun ⟨_, h⟩ => foo_ne_left h⟩\n"
                  "  right_inv x :=\n    funext fun ⟨a, h⟩ =>\n      show f a = _ by\n        dsimp only\n"
                  "        rw [foo_ne_right h]\n\n"
                  "theorem t (y : ℝ) : P y := by simpa using qux_fooCast y m\n"
                  "theorem u : Q := (qux_foo_le g t _).2 fun y hy ↦ hy\n"
                  "theorem v : R := by\n  by_cases hq : q <;> simp [qux_def, hq]\n"
                  "theorem w : S := { s with le := le_refl, h := Qux.ext <| by norm_num }\n"
                  "-- header only: lemma waldo_left (h : P) : Q\n")
        components = [" ".join(source[a:b].split()) for a, b in proof_components(source)]
        self.assertEqual(components, [
            "x",
            "⟨fun a => a, funext fun ⟨_, h⟩ => foo_ne_left h⟩", "fun a => a", "a",
            "funext fun ⟨_, h⟩ => foo_ne_left h", "_", "h", "foo_ne_left h",
            "funext fun ⟨a, h⟩ => show f a = _ by dsimp only rw [foo_ne_right h]", "a", "h",
            "show f a = _ by dsimp only rw [foo_ne_right h]", "dsimp only", "dsimp only rw [foo_ne_right h]",
            "rw [foo_ne_right h]", "foo_ne_right h",
            "by simpa using qux_fooCast y m", "simpa using qux_fooCast y m", "qux_fooCast y m",
            "(qux_foo_le g t _).2 fun y hy ↦ hy", "hy",
            "by by_cases hq : q <;> simp [qux_def, hq]", "by_cases hq : q", "by_cases hq : q <;> simp [qux_def, hq]",
            "simp [qux_def, hq]", "qux_def", "hq",
            "{ s with le := le_refl, h := Qux.ext <| by norm_num }", "s with le := le_refl", "le_refl",
            "h := Qux.ext <| by norm_num", "Qux.ext <| by norm_num", "by norm_num", "norm_num"])
        # A copy at a component position is answer access: an anonymous-constructor component's fun body, a
        # rewrite rule inside a multi-tactic proof, the term of a single-tactic proof and a tactic after <;>.
        t = task("components", premise_context=source)
        for proof, line, snippet, edits in (("foo_ne_right h", 4, "foo_ne_left h", 1),
                                            ("foo_ne_left h", 9, "foo_ne_right h", 1),
                                            ("qux_barCast y m", 11, "qux_fooCast y m", 1),
                                            ("by simp [qux_def.ext, hq]", 14, "simp [qux_def, hq]", 2)):
            matches = [(m["premise_context_line"], m["snippet"], m["declaration_value"], m["token_edits"])
                       for m in near_verbatim_matches(t, proof)]
            self.assertIn((line, snippet, False, edits), matches, proof)
        # A header, a parenthesized argument and an application without its trailing arguments are not.
        self.assertEqual(near_verbatim_matches(t, "waldo_left h"), [])
        self.assertEqual(near_verbatim_matches(t, "qux_foo_le g t M"), [])
        self.assertEqual(self.near("(foo_ne_left h).symm.trans rfl", "foo_ne_right h"), [])
        # A comma makes parentheses a tuple, an anonymous constructor whose elements are components.
        self.assertEqual([m["snippet"] for m in self.near("(foo_ne_left h, rfl).1", "foo_ne_right h")], ["foo_ne_left h"])
        self.assertEqual(self.near("qux_foo₀ (fooHom : β →*₀ 𝕜) c d e f", "qux_bar₀ (fooHom : β →*₀ 𝕜)"), [])
        # Where fields, comments and placeholders: declaration values are the components' outer bounds.
        context = ("def fooPreimage : A ≃ B where\n"
                   "  toFun x := x\n"
                   "  invFun x :=\n    ⟨fun a => a, /- proof omitted -/⟩\n"
                   "  right_inv x :=\n    foo_ne_left h\n\n"
                   "theorem pos (h : p) : Q :=\n  /- proof omitted -/\n\n"
                   "@[simp]\ntheorem commented : R := by\n  -- the dual case\n  simp [qux_corge_left, /- see above -/ grault]\n")
        self.assertEqual([" ".join(context[a:b].split()) for a, b in declaration_values(context)],
                         ["x", "⟨fun a => a, /- proof omitted -/⟩", "foo_ne_left h",
                          "by -- the dual case simp [qux_corge_left, /- see above -/ grault]"])
        t = task("fields", premise_context=context)
        self.assertEqual([(m["premise_context_line"], m["token_edits"], m["declaration_value"])
                          for m in near_verbatim_matches(t, "foo_ne_right h")], [(6, 1, True)])
        self.assertEqual([(m["premise_context_line"], m["snippet"]) for m in near_verbatim_matches(t, "by simp [qux_corge_right, grault]")],
                         [(12, "by -- the dual case simp [qux_corge_left, /- see above -/ grault]"),
                          (14, "simp [qux_corge_left, /- see above -/ grault]")])

    def test_tactic_lines_of_a_block_are_components(self):
        value = "by\n    intro x\n    simp [qux_corge_left, grault]\n    ring"
        self.assertEqual([(m["premise_context_line"], m["snippet"]) for m in self.near(value, "by simp [qux_corge_right, grault]")],
                         [(4, "simp [qux_corge_left, grault]")])
        # A continuation line of one tactic is not a new tactic: the two-line tactic is one component.
        self.assertEqual([(m["premise_context_line"], m["snippet"]) for m in self.near(
            "by\n    simp [qux_corge_left,\n      grault]\n    ring\n    linarith\n    norm_num", "by simp [qux_corge_right, grault]")],
            [(3, "simp [qux_corge_left, grault]")])
        # have values and calc steps are terms after ":=".
        self.assertEqual([m["snippet"] for m in self.near("by\n    have h2 : Q := foo_ne_left h\n    exact h2", "foo_ne_right h")],
                         ["foo_ne_left h"])
        self.assertEqual([m["snippet"] for m in self.near("calc a = b := by rw [foo]\n    _ = c := foo_ne_left h", "foo_ne_right h")],
                         ["foo_ne_left h"])

    def test_matches_are_listed_closest_first_and_readiness_reports_the_closest(self):
        context = ("theorem far : P :=\n  ⟨Foo.bar_baz_left b c x z y, rfl⟩\n"
                   "theorem near : P :=\n  Foo.bar_baz_left c b x y z\n")
        t = task("order", premise_context=context)
        proof = "Foo.bar_baz_right c b x y z"
        matches = near_verbatim_matches(t, proof)
        self.assertEqual([(m["premise_context_line"], m["token_edits"]) for m in matches], [(4, 1), (2, 3)])
        result = build_readiness_report([t], [reference(t, proof=proof)], {"enforce_quotas": False})
        self.assertEqual(result["answer_access_screen"]["near_verbatim_matches"]["order"], matches[0])
        # Closest means the smallest share of changed name components, then the fewest token edits.
        context = ("theorem one_edit : P :=\n  Qux.quux_corge_right c b x y z\n"
                   "theorem two_edits : P :=\n  Foo.bar_baz_right c b x w v\n")
        self.assertEqual([(m["premise_context_line"], m["token_edits"], m["component_edits"])
                          for m in near_verbatim_matches(task("order", premise_context=context), proof)],
                         [(4, 2, 2), (2, 1, 3)])

    def test_near_verbatim_copies_block_the_gate_and_are_listed_with_their_snippet(self):
        t = task("dual", premise_context="theorem pos (h : p) : x = y :=\n  foo_ne_left h\n")
        refs = [reference(t, proof="foo_ne_right h")]
        result = build_readiness_report([t], refs, {"enforce_quotas": False})
        screen = result["answer_access_screen"]
        self.assertEqual((screen["flagged_task_ids"], screen["short_exact_match_task_ids"], screen["near_verbatim_task_ids"]),
                         ([], [], ["dual"]))
        self.assertEqual(screen["near_verbatim_matches"], {"dual": {
            "snippet": "foo_ne_left h", "premise_context_line": 2, "declaration_value": True, "token_edits": 1,
            "component_edits": 1, "reference_token_count": 2, "reference_component_count": 4}})
        # Neither the report nor its page holds the private proof's text.
        self.assertNotIn("foo_ne_right", json.dumps(result, ensure_ascii=False))
        self.assertFalse(result["tasks"]["dual"]["reference_proof_in_prompt"])
        self.assertFalse(result["gates"]["reference_proofs_absent_from_prompts"])
        with tempfile.TemporaryDirectory() as directory:
            page = Path(write_readiness_report(result, directory)["html"]).read_text()
        self.assertIn("is at most max(3, n/6 rounded up) token edits (insertions, deletions, substitutions or swaps of two "
                      "adjacent tokens, delimited by whitespace, brackets, commas and semicolons) from a reference proof of "
                      "n tokens and at least 9 characters that is not a generic shape, changing at most 1/3 of its name "
                      "components, rounded: dual (line 2, 1 token edit: foo_ne_left h).", page)
        self.assertNotIn("foo_ne_right", page)
        clear = build_readiness_report([t], [reference(t, proof="by simpa using congrArg f rfl")], {"enforce_quotas": False})
        self.assertTrue(clear["gates"]["reference_proofs_absent_from_prompts"])
        with tempfile.TemporaryDirectory() as folder:
            tasks = write_jsonl(Path(folder) / "tasks.jsonl", [t])
            refs_file = write_jsonl(Path(folder) / "refs.jsonl", [{"task_id": "dual", "proof": "foo_ne_right h"}])
            code, out, err = run_cli("validate-dataset", "--tasks", tasks, "--references", refs_file)
            self.assertEqual(code, 0, err)
            self.assertIn("dual: premise_context line 2 is a near-verbatim copy of the private reference proof "
                          "(1 token edit; answer access)", json.loads(out)["readiness_errors"])

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

    def test_committed_data_screen_matches_the_recorded_answer_access_amendments(self):
        paths = [ROOT / "data/public/tasks.jsonl", ROOT / "data/private/references.jsonl",
                 ROOT / "data/private/tasks-before-answer-access-and-audit-amendment.json",
                 ROOT / "data/private/tasks-before-near-verbatim-amendment.json"]
        if not all(path.exists() for path in paths):
            self.skipTest("Committed task manifest, private references or pre-amendment rows are unavailable")
        current, references = load_tasks(paths[0]), read_jsonl(paths[1])

        def before(path):
            saved = {t["task_id"]: t for t in load_tasks(path)}
            return [saved.get(t["task_id"], t) for t in current]

        # Amendment-log rows 417-424 replaced the copied predecessor proofs in these prompts; the copies
        # are whole declaration values, so they are also near-verbatim copies with no edit.
        exact = ["lib_algebra_groups_pilot_group_basic_02", "lib_algebra_groups_pilot_group_basic_06",
                 "lib_algebra_groups_pilot_group_basic_08", "lib_analysis_inequalities_main_convex_function_06",
                 "lib_analysis_inequalities_main_convex_function_08", "lib_analysis_inequalities_main_convex_function_10",
                 "lib_sets_logic_main_equiv_basic_04", "lib_sets_logic_main_equiv_basic_10"]
        # Rows 686-717 removed every hit of the widened screen from the saved rows of these 32 tasks: equiv_basic_09's
        # short exact answer, a proof component with no edit, and near-verbatim proofs or proof components.
        near = ["lib_algebra_groups_pilot_group_basic_04", "lib_algebra_groups_main_commute_basic_03",
                "lib_algebra_groups_main_commute_basic_04", "lib_algebra_groups_main_commute_basic_06",
                "lib_algebra_groups_main_commute_basic_10", "lib_algebra_groups_main_units_basic_03",
                "lib_algebra_groups_main_units_basic_07", "lib_algebra_groups_main_units_basic_08",
                "lib_algebra_groups_main_units_basic_10", "lib_number_theory_pilot_nat_basic_04",
                "lib_number_theory_main_gcd_basic_05", "lib_number_theory_main_gcd_basic_06",
                "lib_number_theory_main_gcd_basic_09", "lib_number_theory_main_numbertheory_divisors_02",
                "lib_number_theory_main_numbertheory_divisors_09", "lib_analysis_inequalities_pilot_field_basic_02",
                "lib_analysis_inequalities_pilot_field_basic_03", "lib_analysis_inequalities_pilot_field_basic_06",
                "lib_analysis_inequalities_pilot_field_basic_07", "lib_analysis_inequalities_main_pow_real_09",
                "lib_analysis_inequalities_main_convex_function_09", "lib_analysis_inequalities_main_convex_function_10",
                "lib_analysis_inequalities_main_log_basic_03", "lib_sets_logic_pilot_set_basic_02",
                "lib_sets_logic_pilot_set_basic_03", "lib_sets_logic_main_logic_relation_05",
                "lib_sets_logic_main_equiv_basic_02", "lib_sets_logic_main_equiv_basic_04",
                "lib_sets_logic_main_equiv_basic_05", "lib_sets_logic_main_equiv_basic_09",
                "lib_sets_logic_main_equiv_basic_10", "lib_linear_algebra_combinatorics_main_basis_defs_10"]
        # The saved rows are exactly the tasks the screen flags on them.
        self.assertEqual([t["task_id"] for t in load_tasks(paths[3])], near)
        short = ["lib_sets_logic_main_equiv_basic_09"]
        proofs = {r["task_id"]: r["proof"] for r in references}
        order = [t["task_id"] for t in current]
        for label, tasks, flagged, gated_short, copies in (("current", current, [], [], []),
                                                           ("before rows 686-717", before(paths[3]), [], short, near),
                                                           ("before rows 417-424", before(paths[2]), exact, [], exact)):
            self.assertEqual([error.split(":")[0] for error in answer_access_errors(tasks, references)],
                             [t for t in order if t in flagged + gated_short + copies], label)
            screen = build_readiness_report(tasks, [reference(t, proofs[t["task_id"]]) for t in tasks],
                                            {"enforce_quotas": False})["answer_access_screen"]
            self.assertEqual((screen["flagged_task_ids"], screen["short_exact_match_task_ids"],
                              screen["generic_short_exact_match_task_ids"], screen["near_verbatim_task_ids"],
                              screen["unchecked_task_ids"]), (flagged, gated_short, [], copies, []), label)


if __name__ == "__main__":
    unittest.main()
