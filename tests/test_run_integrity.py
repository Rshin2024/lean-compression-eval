"""Run-integrity gates: stored generations, the implemented policy, the baseline budget, environment records, CLI paths."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.integrity import require_implemented_policy, verified_generation
from lean_compression.io import digest, file_hash, measurement_hash, read_json, verification_request_hash
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run, generation_path, load_run, record_generation, records

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEMO_RUN = PROJECT / "examples/pipeline-demo"
LOCAL_ENV = PROJECT / "configs/environment.local.json"
# Restated rather than read from integrity.IMPLEMENTED_POLICY, so dropping a gate fails these tests.
IMPLEMENTED = (("environment", "boundary_policy", "expand_mathlib_and_evaluator_v1"),
               ("environment", "source_token_policy", "original_syntax_v1"),
               ("extraction", "unknown_dependency", "error"),
               ("extraction", "dependency_cycle", "metrics_unavailable"))


def task(number: int) -> dict:
    return {"task_id": f"tiny_{number}", "family_id": f"family_{number}", "domain": "sets_logic", "split": "pilot",
            "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "",
            "premise_context": "", "forbidden_declarations": [], "target_name": f"tiny_target_{number}",
            "binders": "(P : Prop) (h : P)" if number == 1 else "(P Q : Prop) (hp : P) (hq : Q)",
            "proposition": "P ∧ P" if number == 1 else "P ∧ Q"}


def stub_engine(seen):
    def execute(request, environment, engine, policy, *, mode, wall_seconds):
        seen.append((mode, wall_seconds))
        result = {"validity_status": "success", "process_returncode": 0, "resources": {"wall_seconds_limit": wall_seconds}}
        if mode == "validate":
            return {**result, "metric_status": "not_requested"}
        return {**result, "metric_status": "unavailable", "metric_error": "stubbed extraction"}
    return execute


class RunIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.config = read_json(PROJECT / "configs/pilot.json")
        self.config["validation"]["isolation"] = "macos_sandbox"
        self.env = {"environment_hash": "fixture", "lean_toolchain": self.config["environment"]["lean_toolchain"],
                    "mathlib_commit": self.config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
        self.run = self.temp / "run"
        self.demo_config = read_json(PROJECT / "examples/demo-config.json")

    def freeze(self, config=None):
        return create_run(self.run, [task(1)], config or self.config, self.env, split="pilot",
                          model="reference_fixture", development=True)

    def evaluate(self):
        result = {"validity_status": "success", "metric_status": "unavailable", "metrics": None,
                  "engine_hash": file_hash(ENGINE)}
        with patch("lean_compression.runner.evaluate_proof", return_value=result) as checked:
            outcome = evaluate_run(self.run, ENGINE)
        return outcome, checked

    def run_cli(self, *argv, debug="0"):
        err = io.StringIO()
        # The engine is stubbed, so committed environment records need not match this host's Lean;
        # test_environment_integrity covers load_environment's host checks.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err), \
                patch("lean_compression.cli.load_environment", read_json), \
                patch.dict("os.environ", {"LEAN_COMPRESSION_DEBUG": debug}):
            try:
                cli.main([str(value) for value in argv])
            except SystemExit as stop:
                return stop.code, err.getvalue()
        return 0, err.getvalue()

    def write(self, name, value, lines=False):
        path = self.temp / name
        path.write_text("".join(json.dumps(v) + "\n" for v in value) if lines else json.dumps(value))
        return path

    # I42: a stored generation must belong to its slot and match its content hash.
    def test_edited_generation_is_rejected_by_evaluate_and_records(self):
        slot = self.freeze()["slots"][0]
        record_generation(self.run, slot, "by exact h", generation_status="completed")
        path = generation_path(self.run, slot["attempt_id"])
        path.write_text(json.dumps({**read_json(path), "raw_response": "by trivial_edit"}))
        with self.assertRaisesRegex(ValueError, "raw_response_hash"):
            self.evaluate()
        self.assertFalse((self.run / "evaluations/v1" / path.name).exists())
        with self.assertRaisesRegex(ValueError, "raw_response_hash"):
            records(self.run)

    def test_misplaced_generation_is_rejected(self):
        slots = self.freeze()["slots"]
        first = slots[0]
        other = next(s for s in slots if s["condition"] != first["condition"])
        record_generation(self.run, first, "by exact h", generation_status="completed")
        shutil.copy(generation_path(self.run, first["attempt_id"]), generation_path(self.run, other["attempt_id"]))
        with self.assertRaisesRegex(ValueError, "attempt_id"):
            self.evaluate()
        with self.assertRaisesRegex(ValueError, "attempt_id"):
            records(self.run)
        copied = read_json(generation_path(self.run, other["attempt_id"]))
        with self.assertRaisesRegex(ValueError, "prompt_hash"):
            verified_generation({**copied, "attempt_id": other["attempt_id"]}, other)

    def test_reimport_compares_stored_content_not_its_hash_field(self):
        slot = self.freeze()["slots"][0]
        stored = record_generation(self.run, slot, "by exact h", generation_status="completed")
        self.assertEqual(record_generation(self.run, slot, "by exact h", generation_status="completed"), stored)
        path = generation_path(self.run, slot["attempt_id"])
        path.write_text(json.dumps({**stored, "raw_response": "by edited_after_import"}))
        with self.assertRaisesRegex(ValueError, "raw_response_hash"):
            record_generation(self.run, slot, "by exact h", generation_status="completed")
        self.assertEqual(read_json(path)["raw_response"], "by edited_after_import")

    def test_intact_generations_are_used_and_committed_generations_pass(self):
        slots = self.freeze()["slots"]
        record_generation(self.run, slots[0], "by exact h", generation_status="completed")
        record_generation(self.run, slots[1], "", generation_status="transport_error")
        outcome, checked = self.evaluate()
        self.assertEqual(outcome["new_evaluations"], 2)
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(len(records(self.run)), len(slots))
        manifest = load_run(DEMO_RUN)
        for slot in manifest["slots"]:
            verified_generation(read_json(generation_path(DEMO_RUN, slot["attempt_id"])), slot)
        for version in ("v1", "v2"):
            self.assertEqual({r["validity_status"] for r in records(DEMO_RUN, version)}, {"success"})

    def test_stored_non_text_response_is_accepted_only_for_non_completed_generations(self):
        slots = self.freeze()["slots"]
        # Imports made before this gate stored any JSON raw_response, such as null for a refusal.
        record_generation(self.run, slots[0], None, generation_status="refusal")
        outcome, checked = self.evaluate()
        self.assertEqual((outcome["new_evaluations"], checked.call_count), (1, 0))
        rows = {r["attempt_id"]: r for r in records(self.run)}
        self.assertEqual(rows[slots[0]["attempt_id"]]["validity_status"], "refusal")
        record_generation(self.run, slots[1], None, generation_status="completed")
        with self.assertRaisesRegex(ValueError, "no raw_response text"):
            self.evaluate()
        with self.assertRaisesRegex(ValueError, "no raw_response text"):
            records(self.run)
        stored = {"attempt_id": slots[2]["attempt_id"], "prompt_hash": slots[2]["prompt_hash"],
                  "raw_response": ["by", "exact h"], "raw_response_hash": digest(["by", "exact h"])}
        with self.assertRaisesRegex(ValueError, "no raw_response text"):
            verified_generation(stored, slots[2])
        with self.assertRaisesRegex(ValueError, "raw_response_hash"):
            verified_generation({k: v for k, v in stored.items() if k != "raw_response"}, slots[2])

    def test_generation_changed_after_evaluation_is_refused(self):
        slots = self.freeze()["slots"]
        first = slots[0]
        other = next(s for s in slots if s["condition"] != first["condition"])
        for slot in (first, other):
            record_generation(self.run, slot, "by exact ⟨h, h⟩", generation_status="completed")
        self.evaluate()
        path = generation_path(self.run, first["attempt_id"])
        evaluation = self.run / "evaluations/v1" / path.name
        stored, evaluated = path.read_bytes(), evaluation.read_bytes()
        unhashed = {**read_json(path), "raw_response": "by trivial_edit"}
        edited = {**unhashed, "raw_response_hash": digest("by trivial_edit")}
        for name, generation, changes in (
                ("edited", edited, {}), ("edited with its embedded copy", edited, {"generation": edited}),
                ("edited everywhere but raw_response_hash", unhashed, {"generation": unhashed, "generation_hash": digest(unhashed)}),
                ("misplaced", read_json(generation_path(self.run, other["attempt_id"])), {}), ("missing", None, {})):
            output = self.temp / f"report-{name}"
            with self.subTest(name):
                if generation is None:
                    path.unlink()
                else:
                    path.write_text(json.dumps(generation))
                evaluation.write_text(json.dumps({**read_json(evaluation), **changes}))
                with self.assertRaisesRegex(ValueError, first["attempt_id"]):
                    records(self.run)
                code, message = self.run_cli("analyze", "--run", self.run, "--output", output)
                self.assertEqual(code, 2)
                self.assertIn(first["attempt_id"], message)
                self.assertFalse(output.exists())
            path.write_bytes(stored)
            evaluation.write_bytes(evaluated)
        self.assertEqual(len(records(self.run)), len(slots))

    # N:spec-and-gaps:9: policy labels must name the one implemented policy.
    def test_shipped_and_committed_configs_name_the_implemented_policy(self):
        for path in ("configs/development.json", "configs/pilot.json", "examples/demo-config.json"):
            require_implemented_policy(read_json(PROJECT / path))
        require_implemented_policy(load_run(DEMO_RUN)["config"])

    def test_unimplemented_policy_fails_closed_at_freeze_and_evaluate(self):
        for section, key, value in IMPLEMENTED:
            for changed in (value + "_other", None):
                config = copy.deepcopy(self.config)
                config[section][key] = changed
                if changed is None:
                    del config[section][key]
                with self.subTest(field=key, value=changed), self.assertRaisesRegex(ValueError, f"{section}.{key}"):
                    self.freeze(config)
                self.assertFalse((self.run / "manifest.json").exists())
        config = copy.deepcopy(self.config)
        config["environment"]["boundary_policy"] = "expand_all_packages_v9"
        with patch("lean_compression.store.require_implemented_policy"):
            slot = self.freeze(config)["slots"][0]
        record_generation(self.run, slot, "by exact h", generation_status="completed")
        with self.assertRaisesRegex(ValueError, "boundary_policy"):
            self.evaluate()
        self.assertFalse((self.run / "evaluations").exists())

    def test_unimplemented_policy_fails_closed_in_reference_checks_and_index(self):
        tasks = self.write("tasks.jsonl", [task(1)], lines=True)
        refs = self.write("refs.jsonl", [{"task_id": "tiny_1", "proof": "⟨h, h⟩"}], lines=True)
        for section, key, value in IMPLEMENTED:
            config = copy.deepcopy(self.demo_config)
            config[section][key] = value + "_other"
            path = self.write("config.json", config)
            for command, extra in (("verify-references", ["--references", refs]), ("baseline", [])):
                output = self.temp / f"{command}-{key}"
                with self.subTest(command=command, field=key), \
                        patch("lean_compression.runner.execute_engine") as engine:
                    code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", LOCAL_ENV,
                                                 "--config", path, "--output", output, "--engine", ENGINE)
                    self.assertEqual(code, 2)
                    self.assertIn(f"{section}.{key}", message)
                    engine.assert_not_called()
                    self.assertFalse(output.exists())
            index = self.temp / f"index-{key}.json"
            code, message = self.run_cli("index-closures", "--references", PROJECT / "examples/demo-reference-check",
                                         "--environment", PROJECT / "configs/environment.hashed.json",
                                         "--config", path, "--output", index)
            self.assertEqual((code, f"{section}.{key}" in message, index.exists()), (2, True, False))
        index = self.temp / "index.json"
        self.assertEqual(self.run_cli("index-closures", "--references", PROJECT / "examples/demo-reference-check",
                                      "--environment", PROJECT / "configs/environment.hashed.json",
                                      "--config", PROJECT / "examples/demo-config.json", "--output", index)[0], 0)
        self.assertTrue(index.exists())

    # I38: the frozen portfolio budget bounds the baseline's validation check only.
    def test_baseline_applies_the_frozen_portfolio_budget_to_validation(self):
        tasks = self.write("tasks.jsonl", [task(1)], lines=True)
        refs = self.write("refs.jsonl", [{"task_id": "tiny_1", "proof": "⟨h, h⟩"}], lines=True)
        config = {**self.demo_config, "tactic_portfolio_total_wall_seconds": 300}
        path = self.write("config.json", config)
        for command, extra, expected in (("baseline", [], 300), ("verify-references", ["--references", refs], 60)):
            seen, output = [], self.temp / command
            with self.subTest(command=command), patch("lean_compression.runner.execute_engine", stub_engine(seen)):
                code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", LOCAL_ENV,
                                             "--config", path, "--output", output, "--engine", ENGINE)
                self.assertEqual(code, 0, message)
                self.assertEqual(seen, [("validate", expected), ("extract", config["extraction"]["wall_seconds"])])
                policy = read_json(output / "policy.json")
                self.assertEqual(policy["config_hash"], digest(config))
                self.assertEqual(policy.get("portfolio_validation_wall_seconds"), 300 if command == "baseline" else None)
                # A rerun builds the same policy, recorded budget included, so the directory resumes.
                code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", LOCAL_ENV,
                                             "--config", path, "--output", output, "--engine", ENGINE)
                self.assertEqual((code, len(seen)), (0, 2), message)

    def test_baseline_requires_a_positive_portfolio_budget(self):
        tasks = self.write("tasks.jsonl", [task(1)], lines=True)
        for budget in (None, 0, -5, True, "60"):
            config = {**self.demo_config, "tactic_portfolio_total_wall_seconds": budget}
            if budget is None:
                del config["tactic_portfolio_total_wall_seconds"]
            output = self.temp / f"baseline-{budget}"
            with self.subTest(budget=budget), patch("lean_compression.runner.execute_engine") as engine:
                code, message = self.run_cli("baseline", "--tasks", tasks, "--environment", LOCAL_ENV,
                                             "--config", self.write("config.json", config), "--output", output)
                self.assertEqual(code, 2)
                self.assertIn("tactic_portfolio_total_wall_seconds", message)
                engine.assert_not_called()
                self.assertFalse(output.exists())

    # I37: Lean runs only under an intact environment record that describes the runtime running it.
    def test_cli_evaluate_verifies_the_frozen_environment_before_lean_runs(self):
        slot = self.freeze()["slots"][0]
        record_generation(self.run, slot, "by exact ⟨h, h⟩", generation_status="completed")
        result = {"validity_status": "success", "metric_status": "unavailable", "metrics": None,
                  "engine_hash": file_hash(ENGINE)}
        with patch("lean_compression.runner.evaluate_proof", return_value=result) as checked:
            # The frozen fixture record does not match its environment_hash, like an edited record.
            code, message = self.run_cli("evaluate", "--run", self.run, "--engine", ENGINE)
            self.assertEqual((code, "does not match its environment_hash" in message), (2, True))
            checked.assert_not_called()
            self.assertFalse((self.run / "evaluations").exists())
            with patch("lean_compression.environment.verify_environment") as verify:
                self.assertEqual(self.run_cli("evaluate", "--run", self.run, "--engine", ENGINE)[0], 0)
            verify.assert_called_once_with(self.env)
            self.assertEqual(checked.call_count, 1)

    def test_reference_checks_need_a_record_from_the_runtime_that_runs_lean(self):
        tasks = self.write("tasks.jsonl", [task(1)], lines=True)
        refs = self.write("refs.jsonl", [{"task_id": "tiny_1", "proof": "⟨h, h⟩"}], lines=True)
        digest_id = "sha256:" + "1" * 64  # Docker runs also need an immutable image, tested in test_runtime_gates.
        image = self.write("image.json", {**read_json(LOCAL_ENV), "runtime_backend": "docker", "docker_image": digest_id})
        validation = self.demo_config["validation"]
        docker = self.write("docker.json", {**self.demo_config, "validation": {**validation, "isolation": "docker",
                                                                              "docker_image": digest_id}})
        native = self.write("native.json", self.demo_config)
        # The runner defaults to Docker when validation.isolation is absent.
        unset = self.write("unset.json", {**self.demo_config, "validation": {k: v for k, v in validation.items() if k != "isolation"}})
        for environment, config, expected in ((LOCAL_ENV, docker, 2), (LOCAL_ENV, unset, 2), (image, native, 2), (image, docker, 0)):
            for command, extra in (("verify-references", ["--references", refs]), ("baseline", [])):
                seen, output = [], self.temp / f"{command}-{environment.stem}-{config.stem}"
                with self.subTest(command=command, environment=environment.name, config=config.name), \
                        patch("lean_compression.runner.execute_engine", stub_engine(seen)):
                    code, message = self.run_cli(command, "--tasks", tasks, *extra, "--environment", environment,
                                                 "--config", config, "--output", output, "--engine", ENGINE)
                    self.assertEqual(code, expected, message)
                    self.assertEqual((bool(seen), output.exists()), (expected == 0, expected == 0))
                    if expected:
                        self.assertIn("does not match validation.isolation", message)

    # I47: clean CLI errors and checked evidence directories.
    def test_os_and_key_errors_exit_cleanly_naming_the_key_or_path(self):
        for error, expected in ((KeyError("task_id"), "Missing required field 'task_id' in an input record; "
                                                      "set LEAN_COMPRESSION_DEBUG=1 for a traceback"),
                                (NotADirectoryError(20, "Not a directory", "refs.jsonl/policy.json"),
                                 "[Errno 20] Not a directory: 'refs.jsonl/policy.json'"),
                                (IsADirectoryError(21, "Is a directory", "data/public"), "[Errno 21] Is a directory: 'data/public'"),
                                (PermissionError(13, "Permission denied", "run/manifest.json"),
                                 "[Errno 13] Permission denied: 'run/manifest.json'")):
            with self.subTest(error=type(error).__name__), patch("lean_compression.cli.dispatch", side_effect=error):
                self.assertEqual(self.run_cli("doctor"), (2, expected + "\n"))
                # A code bug can also raise these; LEAN_COMPRESSION_DEBUG=1 keeps the exception and its traceback.
                with self.assertRaises(type(error)):
                    self.run_cli("doctor", debug="1")
        # run_cli re-raises anything but SystemExit, so a real IsADirectoryError must exit cleanly.
        # (The JSONL/task readers reject directories with their own ValueError; read_json does not.)
        code, message = self.run_cli("index-closures", "--references", self.temp, "--environment", self.temp,
                                     "--config", self.temp, "--output", self.temp / "index.json")
        self.assertEqual((code, message), (2, f"[Errno 21] Is a directory: '{self.temp}'\n"))

    def test_analyze_references_must_be_a_reference_directory(self):
        (self.temp / "no-policy").mkdir()
        not_directory = "--references must be an existing verify-references output directory"
        for path, expected in ((PROJECT / "examples/demo-reference.jsonl", not_directory), (self.temp / "missing", not_directory),
                               (self.temp / "no-policy", "--references has no policy.json, so it is not a verify-references output directory")):
            output = self.temp / f"report-{path.name}"
            with self.subTest(path=path.name):
                code, message = self.run_cli("analyze", "--run", DEMO_RUN, "--version", "v2", "--references", path,
                                             "--output", output)
                self.assertEqual((code, message), (2, f"{expected}: {path}\n"))
                self.assertFalse(output.exists())
        output = self.temp / "report"
        code, message = self.run_cli("analyze", "--run", DEMO_RUN, "--version", "v2",
                                     "--references", PROJECT / "examples/demo-reference-check", "--output", output)
        self.assertEqual(code, 0, message)
        self.assertTrue((output / "analysis.json").exists())

    def test_freeze_reference_checks_must_name_existing_records(self):
        tasks = self.write("tasks.jsonl", [task(1), task(2)], lines=True)
        config = self.write("config.json", self.demo_config)
        (self.temp / "empty").mkdir()
        (self.temp / "policy-only").mkdir()
        self.write("policy-only/policy.json", {"kind": "verify-references"})
        (self.temp / "checks").mkdir()
        environment_hash = read_json(LOCAL_ENV)["environment_hash"]
        records = {item["task_id"]: {"task_id": item["task_id"], "record_kind": "verified_reference", "proof": "h",
                                     "validity_status": "success", "metric_status": "available",
                                     "environment_hash": environment_hash, "engine_hash": file_hash(ENGINE),
                                     "verification_request_hash": verification_request_hash(item)}
                   for item in (task(1), task(2))}
        # A complete verify-references directory create_run accepts: checked in the freeze's environment and
        # measurement settings, by the engine freeze is given.
        self.write("checks/policy.json", {"kind": "verify-references", "environment_hash": environment_hash,
                                          "engine_hash": file_hash(ENGINE),
                                          "measurement_hash": measurement_hash(self.demo_config),
                                          "config_hash": digest(self.demo_config)})
        for task_id, record in records.items():
            self.write(f"checks/{digest(task_id)}.json", record)
        # A JSON file that is not a record object is not a reference check.
        self.write("checks/notes.json", ["not", "a", "record"])

        def freeze(name, *extra):
            run = self.temp / name
            code, message = self.run_cli("freeze", "--development", "--tasks", tasks, "--environment", LOCAL_ENV,
                                         "--config", config, "--split", "pilot", "--model", "reference_fixture",
                                         "--engine", ENGINE, "--run", run, *extra)
            return code, message, run / "manifest.json"
        not_directory = "Reference checks directory not found: {}"
        no_policy = "Reference checks must be a verify-references directory with its policy.json"
        for name, path, expected in (("missing", self.temp / "no-such-dir", not_directory), ("file", tasks, not_directory),
                                     ("empty", self.temp / "empty", no_policy), ("policy", self.temp / "policy-only", no_policy)):
            with self.subTest(reference_checks=name):
                code, message, manifest = freeze(f"run-{name}", "--reference-checks", path)
                self.assertEqual((code, message), (2, expected.format(path) + "\n"))
                self.assertFalse(manifest.exists())
        code, message, manifest = freeze("run-checks", "--reference-checks", self.temp / "checks")
        self.assertEqual(code, 0, message)
        self.assertEqual(read_json(manifest)["reference_records_hash"], digest(records))
        code, message, manifest = freeze("run-none")
        self.assertEqual(code, 0, message)
        self.assertIsNone(read_json(manifest)["reference_records_hash"])


if __name__ == "__main__":
    unittest.main()
