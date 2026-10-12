import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.analysis import CONDITIONS, analyze, write_report


def task(name, family=None, domain="algebra", origin="fresh"):
    return {"task_id": name, "family_id": family or name, "domain": domain,
            "origin": origin, "split": "pilot"}


def metric(total=8, source_total=4, body_zero=False):
    return {"metric_status": "available", "W_S": 1, "W_B": source_total - 1,
            "U_S_decimal": str(total if body_zero else 1),
            "U_B_decimal": "0" if body_zero else str(total - 1), "D": 2}


def attempt(task_id, condition, index, total=8, source_total=4, status="success", available=True):
    return {"task_id": task_id, "model_snapshot": "model-frozen", "condition": condition,
            "sample_index": index, "validity_status": status,
            "metric_status": "available" if available else "unavailable",
            "metrics": metric(total, source_total) if available else {"reason": "extractor_timeout"}}


def run(tasks, attempts, **config):
    return analyze(tasks, attempts, {t["task_id"]: metric() for t in tasks},
                   {"bootstrap_replicates": 100, "bootstrap_seed": 42, **config})


class AnalysisTests(unittest.TestCase):
    def test_equal_task_weighting_and_exact_decomposition(self):
        tasks = [task("t1"), task("t2")]
        attempts = []
        for t in tasks:
            for condition in CONDITIONS:
                for index in range(4):
                    # One B success has a +2 log effect; four B successes on
                    # the other task have effect zero. Task average is +1,
                    # not the sample-weighted +0.4 and not a maximum sample.
                    valid = not (t["task_id"] == "t1" and condition == CONDITIONS[1] and index > 0)
                    total = 32 if t["task_id"] == "t1" and condition == CONDITIONS[1] else 8
                    attempts.append(attempt(t["task_id"], condition, index, total,
                                            status="success" if valid else "elaboration_failure"))
        result = run(tasks, attempts)["models"]["model-frozen"]
        contrast = result["contrasts"]["compression_vs_standard"]
        self.assertEqual(contrast["mean_log2_T0_difference"]["estimate"], 1)
        self.assertEqual(contrast["mean_log2_T0_difference"]["geometric_ratio"], 2)
        self.assertEqual(contrast["mean_log2_U_difference"]["estimate"], 1)
        self.assertEqual(contrast["mean_log2_W_difference"]["estimate"], 0)
        self.assertAlmostEqual(contrast["decomposition_residual"], 0)
        self.assertEqual(result["conditions"][CONDITIONS[1]]["success_rate"], 5 / 8)
        self.assertEqual(result["conditions"][CONDITIONS[1]]["four_sample_success_rate"], 1)
        self.assertEqual(result["conditions"][CONDITIONS[1]]["attainment"]["2"]["rate"], 1 / 8)

    def test_missing_slots_and_unmeasurable_success_stay_in_denominator(self):
        tasks = [task("t1")]
        attempts = [attempt("t1", CONDITIONS[0], 0),
                    attempt("t1", CONDITIONS[1], 0, available=False),
                    attempt("t1", CONDITIONS[2], 0, status="timeout")]
        result = run(tasks, attempts)["models"]["model-frozen"]
        self.assertEqual(len(result["missing_scheduled_records"]), 9)
        b = result["conditions"][CONDITIONS[1]]
        self.assertEqual(b["success_rate"], .25)
        self.assertEqual(b["first_attempt_success_rate"], 1)
        self.assertEqual(b["metric_coverage_among_valid"], 0)
        self.assertEqual(b["attainment"]["0"]["rate"], 0)
        self.assertEqual(b["missing_metric_reasons"], {"extractor_timeout": 1})
        estimate = result["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        self.assertIsNone(estimate["estimate"])
        self.assertEqual(estimate["bootstrap_undefined"], 100)
        self.assertFalse(result["compression_conclusions_ready"])

    def test_contrast_selection_and_three_arm_intersection(self):
        tasks = [task("ab"), task("bc"), task("abc")]
        attempts = []
        present = {"ab": CONDITIONS[:2], "bc": CONDITIONS[1:], "abc": CONDITIONS}
        for t in tasks:
            for condition in present[t["task_id"]]:
                attempts.append(attempt(t["task_id"], condition, 0))
        result = run(tasks, attempts)["models"]["model-frozen"]
        self.assertEqual(result["common_intersection"]["task_ids"], ["abc"])
        self.assertEqual(result["contrasts"]["compression_vs_standard"]["paired_task_ids"], ["ab", "abc"])
        self.assertEqual(result["contrasts"]["compression_vs_concision"]["paired_task_ids"], ["abc", "bc"])

    def test_cluster_bootstrap_preserves_families_and_task_weighting(self):
        tasks = [task("a1", "a"), task("a2", "a"), task("a3", "a"), task("b", "b")]
        attempts = []
        for t in tasks:
            for condition in CONDITIONS:
                total = 16 if t["family_id"] == "a" and condition == CONDITIONS[1] else 8
                for index in range(4):
                    attempts.append(attempt(t["task_id"], condition, index, total))
        report = run(tasks, attempts, bootstrap_replicates=1000)
        effect = report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        self.assertEqual(effect["estimate"], .75)
        self.assertEqual(effect["confidence_interval"], [0, 1])
        self.assertEqual(effect["family_count"], 2)
        self.assertEqual(report, run(tasks, attempts, bootstrap_replicates=1000))

    def test_sparse_support_reports_undefined_resamples(self):
        tasks = [task("a"), task("b")]
        attempts = [attempt("a", condition, 0) for condition in CONDITIONS]
        report = run(tasks, attempts, bootstrap_replicates=1000)
        effect = report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        self.assertGreater(effect["bootstrap_undefined"], 200)
        self.assertLess(effect["bootstrap_undefined"], 300)
        self.assertEqual(effect["minimum_bootstrap_task_multiplicity"], 0)

    def test_cross_domain_family_disables_stratification(self):
        tasks = [task("a", "same", "algebra"), task("b", "same", "analysis")]
        report = run(tasks, [attempt("a", CONDITIONS[0], 0)])
        self.assertFalse(report["bootstrap"]["stratified_by_domain"])
        self.assertEqual(report["bootstrap"]["cross_domain_families"], ["same"])

    def test_missing_reference_is_not_zero_attainment(self):
        report = analyze([task("a")], [attempt("a", CONDITIONS[0], 0)], {}, {"bootstrap_replicates": 0})
        self.assertEqual(report["missing_reference_task_ids"], ["a"])
        result = report["models"]["model-frozen"]["conditions"][CONDITIONS[0]]["attainment"]["0"]
        self.assertIsNone(result["rate"])
        self.assertEqual(result["reason"], "missing_reference_metrics")

    def test_duplicate_schedule_identity_is_rejected(self):
        a = attempt("a", CONDITIONS[0], 0)
        with self.assertRaisesRegex(ValueError, "duplicate scheduled"):
            run([task("a")], [a, a])

    def test_attainment_uses_exact_counts_at_large_thresholds(self):
        # These values have identical floating-point logs; one is below the
        # reference and must not attain 1x even after rounding.
        a = attempt("a", CONDITIONS[0], 0)
        a["metrics"]["U_B_decimal"] = str(2 ** 2000 - 2)
        reference = metric()
        reference["U_B_decimal"] = str(2 ** 2000 - 1)
        report = analyze([task("a")], [a], {"a": reference}, {"bootstrap_replicates": 0})
        result = report["models"]["model-frozen"]["conditions"][CONDITIONS[0]]["attainment"]["0"]
        self.assertEqual(result["rate"], 0)

    def test_signature_mismatch_including_reference_is_visible(self):
        a = attempt("a", CONDITIONS[0], 0)
        a["metrics"]["W_S"] = 2
        result = run([task("a")], [a])["models"]["model-frozen"]
        self.assertEqual(result["fixed_signature_mismatch_task_ids"], ["a"])
        self.assertFalse(result["compression_conclusions_ready"])

    def test_reachable_primitive_manifests_can_differ_but_boundary_cannot(self):
        attempts = [attempt("a", condition, 0) for condition in CONDITIONS]
        for index, a in enumerate(attempts):
            a["primitive_manifest_hash"] = f"closure{index}"
            a["primitive_manifest_scope"] = "reachable closure; boundary policy defines shared corpus"
            a["boundary_policy_hash"] = "policy"
            a["environment_hash"] = "env"
        report = run([task("a")], attempts)
        self.assertEqual(report["provenance"]["reachable_primitive_manifest_count"], 3)
        attempts[0]["boundary_policy_hash"] = "different"
        with self.assertRaisesRegex(ValueError, "different boundary_policy_hash"):
            run([task("a")], attempts)

    def test_fixture_report_visibly_carries_provenance(self):
        report = run([task("a")], [attempt("a", condition, 0) for condition in CONDITIONS])
        report["run_provenance"] = {"development": True, "label": "Development fixture; not LLM evidence",
                                    "model_snapshot": "reference_fixture", "run_hash": "frozen123"}
        with tempfile.TemporaryDirectory() as directory:
            files = write_report(report, directory)
            text = Path(files["html"]).read_text()
            self.assertIn("Development fixture; not LLM evidence", text)
            self.assertIn("frozen123", text)
            self.assertIn("Source: frozen scheduled attempt", text)
            self.assertEqual(text.count("<svg "), 4)
            self.assertIn("Paired task compression effects", text)
            self.assertIn("Conditional task mean log2 T0", text)

    def test_incomplete_materialized_slots_prevent_conclusions(self):
        attempts = [attempt("a", condition, index) for condition in CONDITIONS for index in range(4)]
        attempts[0]["validity_status"] = "not_generated"
        result = run([task("a")], attempts)["models"]["model-frozen"]
        self.assertEqual(result["missing_scheduled_records"], [])
        self.assertEqual(len(result["incomplete_scheduled_outcomes"]), 1)
        self.assertFalse(result["compression_conclusions_ready"])

    def test_resource_use_includes_failures_and_missing_is_not_zero(self):
        good = attempt("a", CONDITIONS[0], 0)
        good["validation"] = {"resources": {"elapsed_seconds": 2, "peak_memory_bytes": 10 * 1024 * 1024}}
        good["extraction"] = {"resources": {"elapsed_seconds": 3, "memory_mib_requested": 4096}}
        good["generation"] = {"generation_elapsed_seconds": 5, "usage": {
            "input_tokens": 100, "output_tokens": 40, "output_tokens_details": {"reasoning_tokens": 10}}}
        failed = attempt("a", CONDITIONS[0], 1, status="verification_timeout")
        failed["validation"] = {"resources": {"elapsed_seconds": 8, "peak_memory_mib": 15}}
        failed["generation"] = {"generation_elapsed_seconds": 7, "usage": {
            "input_tokens": 100, "output_tokens": 4, "output_tokens_details": {"reasoning_tokens": 0}}}
        third = attempt("a", CONDITIONS[0], 2, status="format_failure")
        third["validation"] = {"resources": {"elapsed_seconds": None, "memory_mib_requested": 4096}}
        result = run([task("a")], [good, failed, third])
        resources = result["models"]["model-frozen"]["conditions"][CONDITIONS[0]]["resources"]["measurements"]
        self.assertEqual(resources["validation_elapsed_seconds"]["observed_count"], 2)
        self.assertEqual(resources["validation_elapsed_seconds"]["missing_count"], 2)
        self.assertEqual(resources["validation_elapsed_seconds"]["median"], 5)
        self.assertAlmostEqual(resources["validation_elapsed_seconds"]["p95"], 7.7)
        self.assertEqual(resources["validation_elapsed_seconds"]["sum"], 10)
        self.assertEqual(resources["extraction_elapsed_seconds"]["observed_count"], 1)
        self.assertIsNone(resources["extraction_peak_memory_mib"]["median"])
        self.assertEqual(resources["extraction_peak_memory_mib"]["missing_count"], 4)
        self.assertEqual(resources["validation_peak_memory_mib"]["max"], 15)
        self.assertIsNone(resources["validation_peak_memory_mib"]["sum"])
        self.assertEqual(resources["input_tokens"]["sum"], 200)
        self.assertEqual(resources["output_tokens"]["sum"], 44)
        self.assertEqual(resources["reasoning_tokens"]["sum"], 10)
        self.assertEqual(resources["reasoning_tokens"]["median"], 5)
        self.assertEqual(resources["generation_elapsed_seconds"]["median"], 6)
        self.assertIn("retries/backoff", resources["generation_elapsed_seconds"]["description"])
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(result, directory)
            self.assertTrue(Path(paths["resources"]).is_file())
            self.assertIn("validation_elapsed_seconds", Path(paths["resources"]).read_text())
            self.assertIn("Resource use: all scheduled attempts", Path(paths["html"]).read_text())

    def test_absent_usage_and_invalid_measurements_are_explicit(self):
        a = attempt("a", CONDITIONS[0], 0)
        a["validation"] = {"resources": {"elapsed_seconds": -1}}
        a["generation"] = {"created_at": "2026-10-11T01:00:00Z", "usage": {"output_tokens": 0}}
        result = run([task("a")], [a])["models"]["model-frozen"]["conditions"][CONDITIONS[0]]["resources"]["measurements"]
        self.assertEqual(result["validation_elapsed_seconds"]["invalid_count"], 1)
        self.assertIsNone(result["validation_elapsed_seconds"]["sum"])
        self.assertIsNone(result["input_tokens"]["sum"])
        self.assertEqual(result["output_tokens"]["sum"], 0)
        self.assertEqual(result["output_tokens"]["observed_count"], 1)
        self.assertEqual(result["generation_elapsed_seconds"]["missing_count"], 4)
        self.assertIsNone(result["generation_elapsed_seconds"]["median"])

    def test_provider_and_model_mismatch_block_conclusions_but_not_denominators(self):
        for blocked in ("provider_failure", "model_version_mismatch"):
            attempts = [attempt("a", condition, index) for condition in CONDITIONS for index in range(4)]
            attempts[0]["validity_status"] = blocked
            result = run([task("a")], attempts)
            model = result["models"]["model-frozen"]
            self.assertFalse(model["run_complete"])
            self.assertFalse(model["correctness_conclusions_ready"])
            self.assertFalse(model["compression_conclusions_ready"])
            self.assertEqual(model["conditions"][CONDITIONS[0]]["scheduled_attempts"], 4)
            self.assertEqual(model["conditions"][CONDITIONS[0]]["success_rate"], .75)
            with tempfile.TemporaryDirectory() as directory:
                paths = write_report(result, directory)
                self.assertIn("not classified as incorrect mathematical proofs", Path(paths["html"]).read_text())
        for actual_failure in ("refusal", "truncated_output"):
            attempts = [attempt("a", condition, index) for condition in CONDITIONS for index in range(4)]
            attempts[0]["validity_status"] = actual_failure
            model = run([task("a")], attempts)["models"]["model-frozen"]
            self.assertTrue(model["run_complete"])
            self.assertTrue(model["correctness_conclusions_ready"])

    def test_zero_body_and_large_counts_are_json_safe(self):
        a = attempt("a", CONDITIONS[0], 0)
        a["metrics"] = metric(2 ** 2000, body_zero=True)
        report = run([task("a")], [a])
        dist = report["models"]["model-frozen"]["distributions"]["all"]["conditions"][CONDITIONS[0]]
        self.assertEqual(dist["U_B"]["zero_count"], 1)
        self.assertEqual(dist["U_B"]["positive_log2"]["count"], 0)
        json.dumps(report, allow_nan=False)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(report, directory)
            self.assertTrue(Path(paths["html"]).is_file())
            self.assertEqual(json.loads(Path(paths["json"]).read_text()), report)


if __name__ == "__main__":
    unittest.main()
