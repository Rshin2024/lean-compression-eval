"""Offline transaction/provenance tests; no Lean, Docker, or model requests."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

WORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORK))
import verify_runtime_v2 as v
from lean_compression.io import digest, file_hash, read_json, verification_request_hash, write_json
from lean_compression.metrics import score_graph
from lean_compression.store import records as stock_records


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        self.task = {"task_id": "question", "target_name": "target", "imports": ["Init"], "context": "",
                     "binders": "(P : Prop) (h : P)", "proposition": "P", "forbidden_declarations": []}
        self.slot = {"attempt_id": "original-attempt", "prompt_hash": "prompt", "messages": [],
                     "model_snapshot": "fixture", "task_id": "question", "condition": "standard", "sample_index": 0,
                     "config_hash": "generation-config", "environment_hash": "generation-env"}
        self.config = {"generation": {"response_adapter": "single_fence_v1"},
                       "validation": {"wall_seconds": 180, "memory_mib": 8192, "cpu_quota": 2, "docker_image": "new-image"},
                       "extraction": {"wall_seconds": 900}, "environment": {"boundary_policy": "expand_mathlib_and_evaluator_v1"}}
        self.policy = {"source_manifest_sha256": "manifest-sha", "verification_config_hash": "verification-config",
                       "verification_environment_hash": "verification-env", "engine_hash": "engine", "version": v.VERSION}
        self.context = {"run": self.run, "manifest": {"run_hash": "source-run", "tasks": [self.task], "slots": [self.slot]},
                        "config": self.config, "policy": self.policy,
                        "environment": {"environment_hash": "verification-env", "docker_image": "new-image"},
                        "engine": Path("unused.lean"), "directory": self.run / "evaluations" / v.VERSION,
                        "references": {"question": {"metrics": {"W_S": 3, "U_S_decimal": "1"}}}}
        self.gen = {"attempt_id": self.slot["attempt_id"], "prompt_hash": "prompt", "raw_response": "```lean\nby exact h\n```",
                    "raw_response_hash": digest("```lean\nby exact h\n```"), "generation_status": "completed", "created_at": "original-time"}
        self.genpath = self.run / "generations/original-attempt.json"
        write_json(self.genpath, self.gen)
        self.output = self.context["directory"] / "original-attempt.json"
        self.patch_context = patch.object(v, "load_verification_context", return_value=self.context)
        self.patch_code = patch.object(v, "assert_code")
        self.patch_context.start(); self.patch_code.start()
        self.addCleanup(self.patch_context.stop); self.addCleanup(self.patch_code.stop)

    def result(self, status="success", measurable=True):
        def resources(phase):
            return {"wall_seconds_limit": self.config[phase]["wall_seconds"], "memory_mib_requested": 8192,
                    "cpu_quota": 2, "image": "new-image", "isolation": "docker", "network": "disabled", "credentials_inherited": False,
                    "memory_enforced": True}
        result = {"validity_status": status, "metric_status": "unavailable", "metrics": None,
                  "source_hash": digest("by exact h"), "task_hash": digest(self.task),
                  "verification_request_hash": verification_request_hash(self.task),
                  "environment_hash": "verification-env", "engine_hash": "engine",
                  "boundary_policy": self.config["environment"]["boundary_policy"],
                  "boundary_policy_hash": digest(self.config["environment"]["boundary_policy"]),
                  "validation": {"validity_status": status, "kernel_replay": status == "success", "resources": resources("validation")}}
        if status == "success" and measurable:
            graph = [{"name": "root", "primitive": False, "signature": {"atom": 1}, "body": {"atom": 2}},
                     {"name": "atom", "primitive": True}]
            result.update(metric_status="available", metrics=score_graph(graph, "root", 3, 2), graph_hash=digest(graph),
                          source_token_policy_hash=digest({"version": "original_syntax_v1", "engine_hash": "engine"}),
                          extraction={"validity_status": "success", "kernel_replay": True, "nodes": graph, "root": "root",
                                      "source_tokens": {"W_S": 3, "W_B": 2}, "resources": resources("extraction")})
        return result

    def evaluate(self, result=None):
        result = self.result() if result is None else result
        with patch.object(v, "evaluate_proof", return_value=result) as checker:
            summary = v.evaluate(self.run, limit=4)
        return summary, checker

    def test_success_keeps_original_generation_bytes_ids_and_both_environments(self):
        before = self.genpath.read_bytes()
        summary, checked = self.evaluate()
        self.assertEqual((summary["new_evaluations"], summary["checker_incidents"]), (1, 0))
        self.assertEqual(checked.call_args.args[1], "by exact h")
        record = read_json(self.output)
        self.assertEqual(self.genpath.read_bytes(), before)
        self.assertEqual(record["generation"], self.gen)
        self.assertEqual(record["generation_identity"]["environment_hash"], "generation-env")
        self.assertEqual(record["verification_result"]["environment_hash"], "verification-env")
        self.assertEqual(record["attempt_id"], "original-attempt")

    def test_successful_replay_is_not_repeated(self):
        self.evaluate()
        summary, checked = self.evaluate()
        checked.assert_not_called()
        self.assertEqual(summary["new_evaluations"], 0)

    def test_parse_failure_remains_a_failure(self):
        summary, _ = self.evaluate(self.result("parse_failure", False))
        self.assertEqual(summary["new_evaluations"], 1)
        self.assertEqual(read_json(self.output)["validity_status"], "parse_failure")

    def test_truncated_output_runs_no_checker_and_is_not_a_proof_success(self):
        self.gen["generation_status"] = "truncated_output"
        write_json(self.genpath, self.gen, immutable=False)
        summary, checked = self.evaluate()
        checked.assert_not_called()
        record = read_json(self.output)
        self.assertEqual(record["validity_status"], "truncated_output")
        self.assertFalse(record["checker_executed"])
        self.assertIsNone(record["verification_result"]["metrics"])

    def test_infrastructure_abort_stays_an_incident_without_terminal_record(self):
        summary, _ = self.evaluate(self.result("infrastructure_error", False))
        self.assertEqual((summary["new_evaluations"], summary["checker_incidents"]), (0, 1))
        self.assertFalse(self.output.exists())
        self.assertTrue((self.context["directory"] / "incidents.jsonl").exists())

    def test_signature_mismatch_stays_an_incident(self):
        self.context["references"]["question"]["metrics"]["U_S_decimal"] = "999"
        summary, _ = self.evaluate()
        self.assertEqual(summary["checker_incidents"], 1)
        self.assertFalse(self.output.exists())

    def test_claim_prevents_concurrent_duplicate_execution(self):
        with v.claim(self.output) as held:
            self.assertTrue(held)
            summary, checked = self.evaluate()
        checked.assert_not_called()
        self.assertEqual(summary["new_evaluations"], 0)

    def test_loader_rejects_generation_identity_and_verification_phase_tampering(self):
        self.evaluate()
        original = read_json(self.output)
        for field, value in (("attempt_id", "other"), ("source_run_hash", "other"),
                             ("verification_environment_hash", "generation-env"), ("verification_policy_hash", "other")):
            write_json(self.output, {**original, field: value}, immutable=False)
            with self.subTest(field=field), self.assertRaises(ValueError):
                v.load_records(self.run)
        write_json(self.output, original, immutable=False)
        self.assertEqual(len(v.load_records(self.run)), 1)

    def test_loader_validates_graph_scorer_and_resource_provenance(self):
        self.evaluate()
        original = read_json(self.output)
        changes = [lambda r: r["verification_result"]["extraction"]["nodes"].append({"name": "extra", "primitive": True}),
                   lambda r: r["verification_result"]["metrics"].update(scorer_hash="wrong"),
                   lambda r: r["verification_result"]["validation"]["resources"].update(wall_seconds_limit=999)]
        for change in changes:
            edited = copy.deepcopy(original); change(edited); write_json(self.output, edited, immutable=False)
            with self.assertRaises(ValueError):
                v.load_records(self.run)

    def test_loader_rejects_terminal_incomplete_checker_outcome(self):
        self.evaluate()
        record = read_json(self.output)
        record["verification_result"].update(validity_status="infrastructure_error", metric_status="unavailable", metrics=None)
        record.update(validity_status="infrastructure_error", metric_status="unavailable")
        write_json(self.output, record, immutable=False)
        with self.assertRaisesRegex(ValueError, "incidents"):
            v.load_records(self.run)

    def test_numeric_tamper_is_rejected_even_with_intact_graph_and_scorer_hash(self):
        self.evaluate()
        original = read_json(self.output)
        for key, value in (("U_B_decimal", "999"), ("log2_T0", 999.0), ("D", 99)):
            record = copy.deepcopy(original)
            record["verification_result"]["metrics"][key] = value
            write_json(self.output, record, immutable=False)
            with self.subTest(metric=key), self.assertRaisesRegex(ValueError, "Numeric metrics"):
                v.load_records(self.run)

    def test_edited_original_generation_is_not_accepted(self):
        self.evaluate()
        write_json(self.genpath, {**self.gen, "raw_response": "changed"}, immutable=False)
        with self.assertRaisesRegex(ValueError, "raw_response_hash"):
            v.load_records(self.run)

    def test_graph_free_loader_preserves_phase_metadata_and_links_full_record(self):
        self.evaluate()
        record = v.load_records(self.run, graphs=False)[0]
        self.assertNotIn("nodes", record["verification_result"]["extraction"])
        self.assertEqual(record["generation_identity"]["environment_hash"], "generation-env")
        self.assertEqual(record["verification_record"]["sha256"], file_hash(self.output))

    def test_missing_generation_and_unchecked_generation_remain_distinct(self):
        self.assertEqual(v.load_records(self.run)[0]["verification_result"]["validity_status"], "not_evaluated")
        self.genpath.unlink()
        self.assertEqual(v.load_records(self.run)[0]["verification_result"]["validity_status"], "not_generated")

    def test_existing_conflicting_policy_is_not_overwritten(self):
        write_json(self.context["directory"] / "policy.json", {"wrong": True})
        with self.assertRaises(FileExistsError):
            self.evaluate()

    def test_stock_records_refuses_to_mislabel_verification_as_generation_environment(self):
        self.evaluate()
        manifest = {"slots": [self.slot]}; manifest["run_hash"] = digest(manifest)
        write_json(self.run / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "environment_hash"):
            stock_records(self.run, v.VERSION)


if __name__ == "__main__":
    unittest.main()
