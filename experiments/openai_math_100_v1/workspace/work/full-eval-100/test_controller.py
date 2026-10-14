"""Offline launch/schedule safety tests; never issues API calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("controller", Path(__file__).with_name("run_full_evaluation.py"))
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)


class ControllerSafety(unittest.TestCase):
    def fixture(self):
        tasks = [{"task_id": f"OM100-{i:03d}", "model_generation_enabled": True,
                  "proposition": "True", "context": "", "binders": "", "premise_context": ""}
                 for i in range(1, 101)]
        manifests = {}
        entries = []
        for model in sorted(c.MODELS):
            entry = {"model": model, "directory": f"om100-{model}-high", "run_hash": "a" * 64}
            manifest = {"run_hash": entry["run_hash"], "model_snapshot": model,
                        "evaluator_source_hash": "source", "engine_hash": "engine", "tasks": copy.deepcopy(tasks),
                        "reference_anchors": {t["task_id"]: "ref" for t in tasks},
                        "config": {"experiment": "openai_math_100_v1", "conditions": c.CONDITIONS,
                                   "samples_per_goal_condition": 4, "cost_tracking_required": True, "pricing": {"known": True},
                                   "generation": {"reasoning_setting": "high", "max_output_tokens": 8192,
                                                  "response_adapter": "single_outer_wrapper_v2"}},
                        "slots": [{"attempt_id": f"{i:064x}"} for i in range(1200)]}
            entries.append(entry)
            manifests[entry["directory"]] = manifest
        plan = {"ready": True, "experiment": "openai_math_100_v1", "source_hash": "source", "engine_hash": "engine",
                "preparation_audit": {"all_100_ready": True}, "runs": entries}
        return plan, manifests

    def validate(self, plan, manifests):
        with patch.object(c, "evaluator_hash", return_value="source"), patch.object(c, "file_hash", return_value="engine"), \
             patch.object(c, "load_run", side_effect=lambda p: manifests[p.name]):
            return c.validate_plan(plan)

    def test_complete_cohort(self):
        plan, manifests = self.fixture()
        self.assertEqual(len(self.validate(plan, manifests)), 8)

    def test_changed_question_for_one_model_refused(self):
        plan, manifests = self.fixture()
        next(iter(manifests.values()))["tasks"][0]["proposition"] = "False"
        with self.assertRaisesRegex(ValueError, "identical questions"):
            self.validate(plan, manifests)

    def test_disabled_question_refused(self):
        plan, manifests = self.fixture()
        next(iter(manifests.values()))["tasks"][0]["model_generation_enabled"] = False
        with self.assertRaisesRegex(ValueError, "approved"):
            self.validate(plan, manifests)

    def test_changed_effort_refused(self):
        plan, manifests = self.fixture()
        next(iter(manifests.values()))["config"]["generation"]["reasoning_setting"] = "medium"
        with self.assertRaisesRegex(ValueError, "protocol"):
            self.validate(plan, manifests)

    def test_missing_anchor_refused(self):
        plan, manifests = self.fixture()
        next(iter(manifests.values()))["reference_anchors"].pop("OM100-100")
        with self.assertRaisesRegex(ValueError, "anchors"):
            self.validate(plan, manifests)

    def test_shard_resume_does_not_recall_existing(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / "generations").mkdir()
            manifest = {"slots": [{"attempt_id": f"a{i}"} for i in range(8)]}
            for i in (0, 2, 4, 6):
                (run / "generations" / f"a{i}.json").write_text("{}")
            self.assertTrue(c.shard_is_complete(manifest, run, 0, 2))
            self.assertFalse(c.shard_is_complete(manifest, run, 1, 2))

    def test_safe_retry_requires_explicit_transient_nonoutcome(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            path = run / "transport.jsonl"
            for status in (400, 401, 403, 404, 422, None):
                path.write_text(json.dumps({"event": "no_model_outcome", "last_error": {"http_status": status}}))
                self.assertFalse(c.retry_is_safe(run))
            path.write_text(json.dumps({"event": "no_model_outcome", "last_error": {"http_status": 429}}))
            self.assertTrue(c.retry_is_safe(run))
            (run / "inflight").mkdir()
            (run / "inflight/unknown.json").write_text("{}")
            self.assertFalse(c.retry_is_safe(run))

    def test_other_live_shard_does_not_block_safe_retry(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            manifest = {"slots": [{"attempt_id": "stopped"}, {"attempt_id": "live"}]}
            (run / "inflight").mkdir()
            (run / "inflight/live.json").write_text("{}")
            events = [{"attempt_id": "stopped", "event": "no_model_outcome", "last_error": {"http_status": 429}},
                      {"attempt_id": "live", "event": "request_started"}]
            (run / "transport.jsonl").write_text("\n".join(json.dumps(e) for e in events))
            self.assertTrue(c.retry_is_safe(run, manifest, 0, 2))
            self.assertFalse(c.retry_is_safe(run, manifest, 1, 2))

    def test_stray_record_cannot_count_as_completed(self):
        with tempfile.TemporaryDirectory() as root:
            run = Path(root)
            (run / "generations").mkdir()
            (run / "generations" / ("f" * 64 + ".json")).write_text("{}")
            with self.assertRaisesRegex(ValueError, "Unscheduled"):
                c.counts([({"model": "test"}, run, {"slots": [{"attempt_id": "a" * 64}]})], "generations")

    def test_successful_bounded_batches_continue_without_repeating_slots(self):
        class EndTest(BaseException):
            pass
        class Finished:
            returncode = 0
            def poll(self):
                return 0
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            run = work / "one-run"
            run.mkdir()
            (run / "generations").mkdir()
            manifest = {"slots": [{"attempt_id": f"{i:064x}"} for i in range(80)]}
            entry = {"model": "test", "directory": "one-run"}
            sent = []
            sleeps = []
            def launch(args, **kwargs):
                shard = int(args[args.index("--shard-index") + 1])
                count = int(args[args.index("--shard-count") + 1])
                limit = int(args[args.index("--limit") + 1])
                pending = [s for s in manifest["slots"][shard::count]
                           if not (run / "generations" / (s["attempt_id"] + ".json")).exists()]
                for slot in pending[:limit]:
                    sent.append(slot["attempt_id"])
                    (run / "generations" / (slot["attempt_id"] + ".json")).write_text("{}")
                return Finished()
            def sleep(_):
                sleeps.append(1)
                if len(sleeps) == 2:
                    raise EndTest()
            with patch.object(c, "WORK", work), patch.object(c, "access_check"), patch.object(c, "launch_cloud"), \
                 patch.object(c, "sync"), patch.object(c.subprocess, "Popen", side_effect=launch), \
                 patch.object(c.time, "sleep", side_effect=sleep):
                with self.assertRaises(EndTest):
                    c.local_main({"generation_workers_per_model": 8}, [(entry, run, manifest)])
            self.assertEqual(len(sent), 64)
            self.assertEqual(len(set(sent)), 64)
            state = json.loads((work / "status.json").read_text())
            self.assertEqual(state["blocked_shards"], {})


if __name__ == "__main__":
    unittest.main()
