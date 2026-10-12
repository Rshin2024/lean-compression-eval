import unittest

from lean_compression.analysis import CONDITIONS, analyze

STANDARD, COMPRESSION, CONCISION = CONDITIONS


def task(name, family=None, domain="algebra", split="pilot"):
    return {"task_id": name, "family_id": family or name, "domain": domain,
            "origin": "fresh", "split": split}


def metric(total=8, source_total=4):
    # W_S = U_S = 1 throughout, so every task keeps one fixed signature.
    return {"metric_status": "available", "W_S": 1, "W_B": source_total - 1,
            "U_S_decimal": "1", "U_B_decimal": str(total - 1), "D": 2}


def attempt(task_id, condition, index, total=8, source_total=4, status="success",
            available=True, model="model-frozen"):
    measured = status == "success" and available
    return {"task_id": task_id, "model_snapshot": model, "condition": condition,
            "sample_index": index, "validity_status": status,
            "metric_status": "available" if measured else "unavailable",
            "metrics": metric(total, source_total) if measured else None}


def arms(task_id, *samples):
    """Per condition, one entry per sample: a valid proof's expansion total U
    (source W = 4, so T0 = U / 4) or None for an elaboration failure."""
    return [attempt(task_id, condition, index, total) if total is not None else
            attempt(task_id, condition, index, status="elaboration_failure")
            for condition, totals in zip(CONDITIONS, samples) for index, total in enumerate(totals)]


def run(tasks, attempts, references=None, **config):
    references = {t["task_id"]: metric() for t in tasks} if references is None else references
    return analyze(tasks, attempts, references,
                   {"bootstrap_replicates": 1000, "bootstrap_seed": 42, **config})


class ContrastTests(unittest.TestCase):
    def test_correctness_and_attainment_differences_in_both_primary_contrasts(self):
        # A = standard, B = compression, C = concision. The reference T0 is
        # 8 / 4 = 2, so totals 8, 16, 24 and 32 reach 1x, 2x, 3x and 4x of it.
        #           success, first, any    attaining 1x, 2x, 4x
        #   t1  A   0, 0, 0                0, 0, 0
        #       B   .5, 1, 1               .5, .5, .25
        #       C   1, 1, 1                1, .25, 0
        #   t2  A   .25, 0, 1              .25, .25, 0
        #       B   .25, 1, 1              .25, .25, .25
        #       C   1, 1, 1                1, 0, 0
        tasks = [task("t1"), task("t2")]
        attempts = (arms("t1", [None] * 4, [32, 16, None, None], [8, 8, 8, 24]) +
                    arms("t2", [None, 16, None, None], [32, None, None, None], [8] * 4))
        contrasts = run(tasks, attempts)["models"]["model-frozen"]["contrasts"]
        # Each difference is B minus its control. With two families a replicate
        # is one family's difference or their mean, so the percentile interval
        # and the one-sided 95% bound come from the two per-task differences.
        expected = {
            "compression_vs_standard": {
                "success_rate_difference": (.25, [0, .5], 0),
                "first_attempt_difference": (1, [1, 1], 1),
                "any_sample_difference": (.5, [0, 1], 0),
                "attainment_0_difference": (.25, [0, .5], 0),
                "attainment_1_difference": (.25, [0, .5], 0),
                "attainment_2_difference": (.25, [.25, .25], .25)},
            "compression_vs_concision": {
                "success_rate_difference": (-.625, [-.75, -.5], -.75),
                "first_attempt_difference": (0, [0, 0], 0),
                "any_sample_difference": (0, [0, 0], 0),
                "attainment_0_difference": (-.625, [-.75, -.5], -.75),
                "attainment_1_difference": (.25, [.25, .25], .25),
                "attainment_2_difference": (.25, [.25, .25], .25)}}
        self.assertEqual(contrasts["compression_vs_standard"]["conditions"], [COMPRESSION, STANDARD])
        self.assertEqual(contrasts["compression_vs_concision"]["conditions"], [COMPRESSION, CONCISION])
        for name, labels in expected.items():
            for label, (estimate, interval, lower_bound) in labels.items():
                with self.subTest(contrast=name, label=label):
                    result = contrasts[name][label]
                    self.assertEqual(result["estimate"], estimate)
                    self.assertEqual(result["confidence_interval"], interval)
                    self.assertEqual(result["one_sided_lower_bound"], lower_bound)
                    self.assertEqual((result["task_count"], result["family_count"]), (2, 2))
                    self.assertEqual(result["bootstrap_defined"], 1000)

    def test_attainment_thresholds_are_one_two_and_four_times_the_reference(self):
        # Against reference T0 = 2 these proofs reach 1/2, 1, 2, 3 and 4 times it.
        attempts = [attempt("a", STANDARD, index, total) for index, total in enumerate((4, 8, 16, 24, 32))]
        report = run([task("a")], attempts, samples_per_goal_condition=5)
        attainment = report["models"]["model-frozen"]["conditions"][STANDARD]["attainment"]
        self.assertEqual({t: attainment[t]["rate"] for t in attainment}, {"0": 4 / 5, "1": 3 / 5, "2": 1 / 5})

    def test_decomposition_residual_is_zero_when_source_size_also_changes(self):
        # Compression doubles W (4 -> 8) and quadruples U (8 -> 32).
        attempts = ([attempt("a", COMPRESSION, index, 32, source_total=8) for index in range(4)] +
                    [attempt("a", condition, index) for condition in (STANDARD, CONCISION) for index in range(4)])
        contrast = run([task("a")], attempts)["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]
        self.assertEqual([contrast[label]["estimate"] for label in (
            "mean_log2_T0_difference", "mean_log2_U_difference", "mean_log2_W_difference")], [1, 2, 1])
        self.assertAlmostEqual(contrast["decomposition_residual"], 0)

    def test_split_and_model_filters_define_the_population(self):
        tasks = [task("p"), task("m", split="main")]
        attempts = (arms("p", [8] * 4, [8] * 4, [8] * 4) +
                    [attempt("m", condition, 0) for condition in CONDITIONS] +
                    [attempt("p", condition, 0, model="other-model") for condition in CONDITIONS])
        report = run(tasks, attempts, split="pilot", model_snapshots=["model-frozen"])
        self.assertEqual(report["population"]["task_count"], 1)
        self.assertEqual(list(report["models"]), ["model-frozen"])
        self.assertEqual(list(report["models"]["model-frozen"]["per_task"]), ["p"])
        self.assertEqual(report["ignored_out_of_population_attempts"], 6)


class BootstrapDesignTests(unittest.TestCase):
    def effect(self, report):
        return report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]

    def test_families_are_resampled_within_each_domain(self):
        # The log2 T0 effect is 0 in every algebra family and 2 in every
        # analysis family. Redrawing two families per domain keeps every
        # replicate at the estimate; pooled resampling would range over [0, 2].
        tasks = [task("a1", domain="algebra"), task("a2", domain="algebra"),
                 task("n1", domain="analysis"), task("n2", domain="analysis")]
        attempts = []
        for t in tasks:
            attempts += arms(t["task_id"], [8] * 4, [32 if t["domain"] == "analysis" else 8] * 4, [8] * 4)
        report = run(tasks, attempts)
        self.assertTrue(report["bootstrap"]["stratified_by_domain"])
        self.assertEqual(report["bootstrap"]["strata"], {"algebra": 2, "analysis": 2})
        self.assertEqual(self.effect(report)["estimate"], 1)
        self.assertEqual(self.effect(report)["confidence_interval"], [1, 1])

    def test_replicates_keep_equal_task_weights_in_unequal_families(self):
        # One family per domain, so every replicate redraws both families once.
        # Three tasks at effect 0 and one at 4 give the equal-task mean 1 in
        # every replicate; equal-family weighting would give 2.
        tasks = [task(f"large{i}", "large", "algebra") for i in range(3)] + [task("small", "small", "analysis")]
        attempts = []
        for t in tasks:
            attempts += arms(t["task_id"], [8] * 4, [128 if t["family_id"] == "small" else 8] * 4, [8] * 4)
        report = run(tasks, attempts)
        self.assertCountEqual(report["bootstrap"]["singleton_strata"], ["algebra", "analysis"])
        self.assertEqual(self.effect(report)["estimate"], 1)
        self.assertEqual(self.effect(report)["confidence_interval"], [1, 1])
        self.assertEqual(self.effect(report)["family_count"], 2)


class ConclusionReadinessTests(unittest.TestCase):
    def complete(self):
        # 2 tasks x 3 conditions x 4 samples, all valid and measurable.
        return (arms("t1", [8, 16, 16, 32], [32, 16, 16, 8], [8, 16, 16, 32]) +
                arms("t2", [8, 8, 16, 16], [16, 16, 8, 8], [8, 8, 16, 16]))

    def blocked(self, attempts, references=None):
        report = run([task("t1"), task("t2")], attempts, references)
        model = report["models"]["model-frozen"]
        self.assertFalse(model["compression_conclusions_ready"])
        self.assertEqual(model["incomplete_scheduled_outcomes"], [])
        return report, model

    def test_complete_consistent_run_is_ready_for_compression_conclusions(self):
        model = run([task("t1"), task("t2")], self.complete())["models"]["model-frozen"]
        self.assertTrue(model["compression_conclusions_ready"])
        self.assertEqual(model["fixed_signature_mismatch_task_ids"], [])
        self.assertEqual(model["incomplete_scheduled_outcomes"], [])

    def test_fixed_signature_mismatch_blocks_readiness(self):
        # Sample 0 of the compression arm for t1 (W_S) and for t2 (U_S).
        for index, field, value, task_id in ((4, "W_S", 2, "t1"), (16, "U_S_decimal", "2", "t2")):
            with self.subTest(field):
                attempts = self.complete()
                attempts[index]["metrics"][field] = value
                self.assertEqual(self.blocked(attempts)[1]["fixed_signature_mismatch_task_ids"], [task_id])

    def test_missing_reference_blocks_readiness(self):
        report, _ = self.blocked(self.complete(), {"t1": metric()})
        self.assertEqual(report["missing_reference_task_ids"], ["t2"])

    def test_valid_unmeasurable_attempt_blocks_readiness(self):
        attempts = self.complete()
        attempts[5] = attempt("t1", COMPRESSION, 1, available=False)
        model = self.blocked(attempts)[1]
        self.assertEqual(model["conditions"][COMPRESSION]["metric_coverage_among_valid"], 7 / 8)

    def test_missing_paired_support_blocks_readiness(self):
        attempts = [attempt(a["task_id"], a["condition"], a["sample_index"], status="elaboration_failure")
                    if a["condition"] == COMPRESSION else a for a in self.complete()]
        model = self.blocked(attempts)[1]
        self.assertEqual([model["contrasts"][name]["paired_population"]["task_count"]
                          for name in ("compression_vs_standard", "compression_vs_concision")], [0, 0])


if __name__ == "__main__":
    unittest.main()
