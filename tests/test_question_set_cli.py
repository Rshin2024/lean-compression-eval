"""Selection is an explicit local action, distinct from a runnable evaluation."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.cli import dispatch, parser, require_valid


class QuestionSetCliTests(unittest.TestCase):
    def test_adoption_records_portable_manifest_bound_active_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "data" / "questions"
            active = root / "configs" / "active.json"
            bundle.mkdir(parents=True)
            (bundle / "manifest.json").write_text('{"fixture": true}\n')
            status = {"status": "selected_pending_lean_preparation", "model_generation_enabled": False}
            with patch("lean_compression.question_sets.adopt_question_set",
                       return_value={"selection": [{"task_id": "OM100-001"}], "status": status}) as adopt:
                result = dispatch(parser().parse_args([
                    "select-questions", "--candidates", str(root / "candidates.jsonl"),
                    "--catalogue", str(root / "catalogue.html"), "--output", str(bundle),
                    "--active-config", str(active), "--verify-upstream",
                ]))
            self.assertTrue(adopt.call_args.kwargs["verify_upstream"])
            self.assertEqual(result["selected_questions"], 1)
            pointer = json.loads(active.read_text())
            self.assertEqual(pointer["question_set"], "../data/questions")
            self.assertEqual(pointer["manifest_sha256"], hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest())
            args = parser().parse_args(["question-set", "--config", str(active)])
            with patch("lean_compression.question_sets.load_question_set",
                       return_value={"selection": [{}], "status": status}):
                self.assertFalse(dispatch(args)["status"]["model_generation_enabled"])
                args.require_ready = True
                with self.assertRaisesRegex(ValueError, "standalone Lean contexts"):
                    dispatch(args)
            (bundle / "manifest.json").write_text('{"fixture": false}\n')
            args.require_ready = False
            with self.assertRaisesRegex(ValueError, "manifest changed"):
                dispatch(args)

    def test_selection_is_not_accepted_as_executable_tasks(self):
        with self.assertRaisesRegex(ValueError, "selected-question catalogue"):
            require_valid([{"record_kind": "selected_question_v1", "task_id": "OM100-001"}])


if __name__ == "__main__":
    unittest.main()
