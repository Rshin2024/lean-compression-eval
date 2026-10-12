import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import os
import io

from lean_compression.io import digest, read_json, write_json
from lean_compression.prompts import CONDITIONS, adapt_response, messages
from lean_compression.providers import parse_response, response_payload, import_responses, generate_run
from lean_compression.runner import evaluate_proof
from lean_compression.store import create_run, load_run, record_generation, records, schedule


PROJECT = Path(__file__).resolve().parents[1]


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(PROJECT / "configs/pilot.json")
        self.config["validation"]["isolation"] = "macos_sandbox"
        self.env = {"environment_hash": "fixture", "lean_toolchain": self.config["environment"]["lean_toolchain"],
                    "mathlib_commit": self.config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
        self.tasks = [{"task_id": "one", "family_id": "family", "domain": "sets_logic", "split": "pilot",
                       "origin": "fresh", "target_name": "testTarget", "binders": "(P : Prop) (h : P)",
                       "proposition": "P", "imports": ["Mathlib.Logic.Basic"], "premise_context": "", "context": ""}]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"

    def freeze(self):
        return create_run(self.run, self.tasks, self.config, self.env, split="pilot", model="fixture", development=True)

    def test_prompts_only_differ_in_condition_and_have_no_private_reference(self):
        public = {**self.tasks[0], "proof": "SECRET_REFERENCE"}
        rendered = [messages(public, condition, "pin") for condition in CONDITIONS]
        normalized = [r[1]["content"].replace(CONDITIONS[c], "CONDITION") for c, r in zip(CONDITIONS, rendered)]
        self.assertEqual(len(set(normalized)), 1)
        self.assertNotIn("SECRET_REFERENCE", json.dumps(rendered))

    def test_all_slots_stable_and_randomized(self):
        first = schedule(self.tasks, self.config, self.env, "fixture")
        self.assertEqual(first, schedule(self.tasks, self.config, self.env, "fixture"))
        self.assertEqual(len(first), 12)
        self.assertEqual(len({s["attempt_id"] for s in first}), 12)
        changed = copy.deepcopy(self.config)
        changed["generation"]["temperature"] = 0.6
        self.assertTrue({s["attempt_id"] for s in first}.isdisjoint(
            s["attempt_id"] for s in schedule(self.tasks, changed, self.env, "fixture")))

    def test_immutable_manifest_and_generation_resume(self):
        run = self.freeze()
        self.assertEqual(load_run(self.run)["run_hash"], run["run_hash"])
        slot = run["slots"][0]
        one = record_generation(self.run, slot, "by exact h", generation_status="completed")
        two = record_generation(self.run, slot, "by exact h", generation_status="completed")
        self.assertEqual(one, two)
        with self.assertRaises(FileExistsError):
            record_generation(self.run, slot, "by sorry")
        rows = records(self.run)
        self.assertEqual(len(rows), 12)
        self.assertEqual(sum(r["validity_status"] == "not_generated" for r in rows), 11)
        run["config"]["generation"]["temperature"] = 1
        write_json(self.run / "manifest.json", run, immutable=False)
        with self.assertRaisesRegex(ValueError, "modified"):
            load_run(self.run)

    def test_copied_evaluation_identity_is_rejected(self):
        run = self.freeze()
        slot = run["slots"][0]
        write_json(self.run / "evaluations/v1" / f"{slot['attempt_id']}.json",
                   {"attempt_id": "different", "validity_status": "success"})
        with self.assertRaisesRegex(ValueError, "identity"):
            records(self.run)

    def test_reference_environment_and_missing_snapshot_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "snapshot"):
            create_run(self.run, self.tasks, self.config, self.env, split="pilot", model="fixture")
        wrong = {**self.env, "mathlib_commit": "wrong"}
        with self.assertRaisesRegex(ValueError, "mathlib_commit"):
            create_run(self.run, self.tasks, self.config, wrong, split="pilot", model="fixture", development=True)

    def test_no_response_cherry_picking_or_incomplete_success(self):
        response = {"status": "incomplete", "model": "fixture", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "first"},
                                                {"type": "output_text", "text": "second"}]}]}
        parsed = parse_response(response)
        self.assertEqual(parsed["raw_response"], "first\nsecond")
        self.assertEqual(parsed["generation_status"], "truncated_output")
        raw = "```lean\nby exact h\n```"
        self.assertEqual(adapt_response(raw), raw)
        self.assertEqual(adapt_response(raw, "single_fence_v1"), "by exact h")
        self.assertEqual(adapt_response(raw + "\n" + raw, "single_fence_v1"), raw + "\n" + raw)

    def test_provider_never_inherits_history_or_tools(self):
        slot = schedule(self.tasks, self.config, self.env, "fixture")[0]
        payload = response_payload(slot, self.config["generation"])
        self.assertEqual(payload["tools"], [])
        self.assertNotIn("previous_response_id", payload)
        self.assertNotIn("conversation", payload)
        self.assertFalse(payload["store"])

    def test_import_requires_scheduled_prompt_and_model(self):
        run = self.freeze()
        slot = run["slots"][0]
        with self.assertRaisesRegex(ValueError, "prompt"):
            import_responses(self.run, [{"attempt_id": slot["attempt_id"], "raw_response": "by exact h"}])
        import_responses(self.run, [{"attempt_id": slot["attempt_id"], "prompt_hash": slot["prompt_hash"],
                                    "model_snapshot": "fixture", "raw_response": "by exact h"}])

    def test_valid_proof_stays_success_when_extraction_times_out(self):
        with patch("lean_compression.runner.execute_engine", side_effect=[
            {"validity_status": "success", "axioms": []},
            {"validity_status": "extractor_timeout", "metric_status": "unavailable"}
        ]):
            result = evaluate_proof(self.tasks[0], "by exact h", self.env,
                                    PROJECT / "lean/Evaluator.lean", self.config)
        self.assertEqual(result["validity_status"], "success")
        self.assertEqual(result["metric_status"], "unavailable")
        self.assertIsNone(result["metrics"])

    def test_concurrent_completion_before_lock_does_not_repeat_api_call(self):
        image = "sha256:" + "0" * 64
        self.config["validation"].update(isolation="docker", docker_image=image)
        self.config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        self.env.update(runtime_backend="docker", docker_image=image,
                        source_exclusion_audit={"lean_sources": 0, "git_directories": 0})
        run = self.freeze()
        for slot in run["slots"]:
            record_generation(self.run, slot, "by exact h", generation_status="completed")
        raced = self.run / "generations" / f"{run['slots'][0]['attempt_id']}.json"
        original_exists = Path.exists
        first = True
        def stale_first_check(path):
            nonlocal first
            if path == raced and first:
                first = False
                return False
            return original_exists(path)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(Path, "exists", stale_first_check), \
             patch("lean_compression.providers.urllib.request.urlopen") as request:
            result = generate_run(self.run)
        request.assert_not_called()
        self.assertEqual(result["new_generation_records"], 0)

    def test_backend_model_change_stops_before_more_paid_requests(self):
        image = "sha256:" + "0" * 64
        self.config["validation"].update(isolation="docker", docker_image=image)
        self.config["generation"].update(snapshot_pinned=True, settings_resolved=True)
        self.env.update(runtime_backend="docker", docker_image=image,
                        source_exclusion_audit={"lean_sources": 0, "git_directories": 0})
        self.freeze()
        reply = io.BytesIO(json.dumps({"id": "mock-response", "model": "different-snapshot", "status": "completed",
                                       "output": [{"type": "message", "content": [{"type": "output_text", "text": "by exact h"}]}]}).encode())
        reply.headers = {}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unit-test-only"}), \
             patch("lean_compression.providers.subprocess.run", return_value=SimpleNamespace(returncode=0)), \
             patch("lean_compression.providers.urllib.request.urlopen", return_value=reply) as request:
            with self.assertRaisesRegex(RuntimeError, "different model snapshot"):
                generate_run(self.run)
        self.assertEqual(request.call_count, 1)
        recorded = list((self.run / "generations").glob("*.json"))
        self.assertEqual(len(recorded), 1)
        self.assertEqual(read_json(recorded[0])["generation_status"], "model_version_mismatch")


if __name__ == "__main__":
    unittest.main()
