from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.io import read_json, write_json
from lean_compression.runner import evaluate_run
from lean_compression.store import create_run


PROJECT = Path(__file__).resolve().parents[1]


class WriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "evidence" / "policy.json"

    def entries(self):
        # Also shows that no temporary publish file is left behind.
        return sorted(path.name for path in self.path.parent.iterdir())

    def test_identical_rewrite_is_a_no_op(self):
        write_json(self.path, {"engine_hash": "first", "labels": ["α", 2]})
        original = self.path.read_bytes()
        write_json(self.path, {"labels": ["α", 2], "engine_hash": "first"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.entries(), ["policy.json"])

    def test_differing_rewrite_is_refused_and_bytes_are_unchanged(self):
        write_json(self.path, {"engine_hash": "first"})
        original = self.path.read_bytes()
        with self.assertRaisesRegex(FileExistsError, "immutable evidence"):
            write_json(self.path, {"engine_hash": "second"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.entries(), ["policy.json"])

    def test_file_published_after_the_existence_check_is_never_replaced(self):
        write_json(self.path, {"engine_hash": "first"})
        original = self.path.read_bytes()
        target, real_exists = self.path, Path.exists
        with patch.object(Path, "exists", lambda path: False if path == target else real_exists(path)):
            write_json(self.path, {"engine_hash": "first"})
            with self.assertRaises(FileExistsError):
                write_json(self.path, {"engine_hash": "second"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.entries(), ["policy.json"])

    def test_explicitly_mutable_write_replaces(self):
        write_json(self.path, {"version": 1})
        write_json(self.path, {"version": 2}, immutable=False)
        self.assertEqual(read_json(self.path), {"version": 2})
        self.assertEqual(self.entries(), ["policy.json"])


class EvaluationVersionTests(unittest.TestCase):
    def test_resuming_a_version_with_a_changed_engine_is_refused(self):
        config = read_json(PROJECT / "configs/pilot.json")
        config["validation"]["isolation"] = "macos_sandbox"
        environment = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
                       "mathlib_commit": config["environment"]["mathlib_commit"],
                       "hash_mode": "development_metadata_only"}
        task = {"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot",
                "origin": "fresh", "target_name": "testTarget", "binders": "(P : Prop) (h : P)",
                "proposition": "P", "imports": ["Mathlib.Logic.Basic"], "premise_context": "", "context": ""}
        with tempfile.TemporaryDirectory() as directory:
            run, engine = Path(directory) / "run", Path(directory) / "Evaluator.lean"
            shutil.copyfile(PROJECT / "lean/Evaluator.lean", engine)
            create_run(run, [task], config, environment, split="pilot", model="reference_fixture",
                       development=True)
            # No generations exist yet, so only the version policy is recorded.
            self.assertEqual(evaluate_run(run, engine)["new_evaluations"], 0)
            policy = run / "evaluations/v1/policy.json"
            original = policy.read_bytes()
            self.assertEqual(evaluate_run(run, engine)["new_evaluations"], 0)
            engine.write_text(engine.read_text() + "\n-- changed checker\n")
            with self.assertRaises((FileExistsError, ValueError, RuntimeError)):
                evaluate_run(run, engine)
            self.assertEqual(policy.read_bytes(), original)
            # A changed checker gets a new evaluation version instead.
            evaluate_run(run, engine, version="v2")
            self.assertNotEqual(read_json(run / "evaluations/v2/policy.json")["engine_hash"],
                                read_json(policy)["engine_hash"])


if __name__ == "__main__":
    unittest.main()
