"""Offline checks of version separation and conservative supervision."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import supervise_verification_v2 as controller


class SupervisionTests(unittest.TestCase):
    def test_v1_records_cannot_count_as_v2(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            ident = "a" * 64
            path = run / "evaluations/v1"
            path.mkdir(parents=True)
            (path / f"{ident}.json").write_text("{}")
            runs = [({"model": "example"}, run, {"slots": [{"attempt_id": ident}]})]
            self.assertEqual(controller.complete_counts(runs), {"example": 0})
            path = run / "evaluations/v2_threads1"
            path.mkdir(parents=True)
            (path / f"{ident}.json").write_text("{}")
            self.assertEqual(controller.complete_counts(runs), {"example": 1})

    def test_unknown_success_output_is_held(self):
        for summary in ({}, None, {"new_evaluations": 4}, {"new_evaluations": True, "checker_incidents": 0}):
            self.assertTrue(controller.failed_result({"exit_code": 0, "summary": summary}))

    def test_incidents_and_retained_claims_are_held(self):
        for row in ({"exit_code": 1, "summary": {"new_evaluations": 0, "checker_incidents": 0}},
                    {"exit_code": 0, "summary": {"new_evaluations": 2, "checker_incidents": 1}},
                    {"exit_code": 0, "summary": {"new_evaluations": 0, "checker_incidents": 0, "kept_claims": ["a"]}}):
            self.assertTrue(controller.failed_result(row))
        self.assertFalse(controller.failed_result({"exit_code": 0, "summary": {"new_evaluations": 4, "checker_incidents": 0}}))

    def test_changed_verifier_cannot_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "verification-launch-v2.json").write_text(json.dumps({
                "ready": True, "version": "v2_threads1", "source_files": {
                    name: "f" * 64 for name in ["verify_runtime_v2.py", "supervise_verification_v2.py", "run_full_evaluation.py"]}}))
            (work / "verify_runtime_v2.py").write_text("modified")
            with patch.object(controller, "WORK", work), self.assertRaisesRegex(ValueError, "Changed verification source"):
                controller.validate_launch()

    def test_live_dashboard_never_merges_verification_versions(self):
        spec = importlib.util.spec_from_file_location("live", Path(__file__).parent / "live/server.py")
        live = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(live)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            models = [m for m, _ in live.MODELS]
            now = live.time.time()
            (work / "status.json").write_text(json.dumps({"updated_at": now, "generated": {m: 200 for m in models},
                "checked": {m: 190 for m in models}, "cloud": {"updated_at": now, "checked": {m: 195 for m in models}}}))
            (work / "verification-status.json").write_text(json.dumps({"updated_at": now, "version": "v2_threads1",
                "state": "running", "checked": {m: 7 for m in models}, "saved": {m: 3 for m in models}}))
            with patch.object(live, "WORK", work):
                status = live.sanitized_status()
            self.assertEqual(status["generated_total"], 1600)
            self.assertEqual(status["checked_total"], 56)
            self.assertEqual(status["saved_total"], 24)


if __name__ == "__main__":
    unittest.main()
