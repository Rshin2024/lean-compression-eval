import csv
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from lean_compression.analysis import CONDITIONS, STRATUM_STATISTICS, analyze, write_report
from lean_compression.cli import dispatch, parser


PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE_ENGINE = "3c093c02b7d907faad45ede5ed175e3527aeaa9dbb9ca2141293075aaeeaa343"


def task(name, family=None, domain="algebra", stratum=None):
    value = {"task_id": name, "family_id": family or name, "domain": domain, "origin": "fresh", "split": "pilot"}
    if stratum is not None:
        value["stratum"] = stratum
    return value


def metric(total=8, source_total=4):
    return {"metric_status": "available", "W_S": 1, "W_B": source_total - 1,
            "U_S_decimal": "1", "U_B_decimal": str(total - 1), "D": 2}


def attempt(task_id, condition, index=0, total=8, status="success", **extra):
    return {"task_id": task_id, "model_snapshot": "model-frozen", "condition": condition, "sample_index": index,
            "validity_status": status, "metric_status": "available" if status == "success" else "unavailable",
            "metrics": metric(total) if status == "success" else None, **extra}


def run(tasks, attempts, references=None, **config):
    references = {t["task_id"]: metric() for t in tasks} if references is None else references
    return analyze(tasks, attempts, references,
                   {"samples_per_goal_condition": 1, "bootstrap_replicates": 200, "bootstrap_seed": 7, **config})


def analyze_cli(run_directory, output, version="v2", references=None):
    arguments = ["analyze", "--run", str(run_directory), "--version", version, "--output", str(output)]
    if references:
        arguments += ["--references", str(references)]
    return dispatch(parser().parse_args(arguments))


class ProvenanceTests(unittest.TestCase):
    def test_failures_from_another_engine_cannot_be_pooled(self):
        tasks = [task("a"), task("b")]
        attempts = [attempt(t["task_id"], c, engine_hash="engine-1") for t in tasks for c in CONDITIONS]
        report = run(tasks, attempts)
        self.assertEqual(report["provenance"]["engine_hash"], "engine-1")
        self.assertIsNone(report["provenance"]["evaluator_source_hash"])
        # A failure has no source-token-policy hash, so only the engine identity exposes it.
        attempts[0] = attempt("a", CONDITIONS[0], status="elaboration_failure", engine_hash="engine-0")
        with self.assertRaisesRegex(ValueError, "different engine_hash"):
            run(tasks, attempts)
        attempts[0] = attempt("a", CONDITIONS[0], engine_hash="engine-1")
        stale = {t["task_id"]: {**metric(), "engine_hash": "engine-0"} for t in tasks}
        with self.assertRaisesRegex(ValueError, "different engine_hash"):
            run(tasks, attempts, stale)
        for index, value in enumerate(attempts):
            value["evaluator_source_hash"] = f"harness-{index % 2}"
        with self.assertRaisesRegex(ValueError, "different evaluator_source_hash"):
            run(tasks, attempts)

    def test_mixed_engine_example_version_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            copy = Path(directory) / "run"
            shutil.copytree(PROJECT / "examples/pipeline-demo", copy)
            for path in (copy / "evaluations/v1").glob("*.json"):
                record = json.loads(path.read_text(encoding="utf-8"))
                if path.name != "policy.json" and not record["engine_hash"].startswith("275337fa"):
                    for key in ("extraction", "graph_hash", "source_token_policy_hash", "primitive_manifest",
                                "primitive_manifest_hash", "primitive_manifest_scope"):
                        record.pop(key, None)
                    record.update(validity_status="elaboration_failure", metric_status="unavailable", metrics=None)
                    path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different engine_hash"):
                analyze_cli(copy, Path(directory) / "report", version="v1")
            self.assertFalse((Path(directory) / "report/analysis.json").exists())

    def test_protocol_pins_seed_provenance_and_disagreement_is_refused(self):
        tasks = [task("a")]
        pins = {"lean_toolchain": "leanprover/lean4:v4.34.0-rc1", "mathlib_commit": "274ed6d6"}
        attempts = [attempt("a", c) for c in CONDITIONS]
        self.assertIsNone(run(tasks, attempts)["provenance"]["lean_toolchain"])
        provenance = run(tasks, attempts, environment=pins)["provenance"]
        self.assertEqual(provenance["lean_toolchain"], pins["lean_toolchain"])
        self.assertEqual(provenance["mathlib_commit"], pins["mathlib_commit"])
        attempts[0]["environment"] = dict(pins)
        self.assertEqual(run(tasks, attempts, environment=pins)["provenance"]["mathlib_commit"], "274ed6d6")
        del attempts[0]["environment"]
        attempts[1]["mathlib_commit"] = "different"
        with self.assertRaisesRegex(ValueError, "mathlib_commit differs from the frozen protocol"):
            run(tasks, attempts, environment=pins)

    def test_documented_example_still_reports_with_engine_and_pins(self):
        with tempfile.TemporaryDirectory() as directory:
            copy, output = Path(directory) / "run", Path(directory) / "report"
            shutil.copytree(PROJECT / "examples/pipeline-demo", copy)
            analyze_cli(copy, output, references=PROJECT / "examples/demo-reference-check")
            report = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
            environment = json.loads((copy / "manifest.json").read_text(encoding="utf-8"))["config"]["environment"]
            self.assertEqual(report["provenance"]["engine_hash"], EXAMPLE_ENGINE)
            self.assertEqual(report["provenance"]["lean_toolchain"], environment["lean_toolchain"])
            self.assertEqual(report["provenance"]["mathlib_commit"], environment["mathlib_commit"])
            model = report["models"]["reference_fixture"]
            self.assertEqual(model["stratum_breakdown"]["groups"]["stratum_class:easy_control"]["task_ids"],
                             ["smoke_ag_01"])
            # The original columns keep their order; columns added later may follow them.
            with (output / "correctness.csv").open(encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle))[:9], [
                    "model", "condition", "scheduled_attempts", "valid_attempts", "success_rate",
                    "first_attempt_success_rate", "four_sample_success_rate", "any_sample_success_rate",
                    "metric_coverage_among_valid"])
            with (output / "paired_tasks.csv").open(encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle))[:11], [
                    "model", "contrast", "task_id", "family_id", "domain", "origin", "B_measurable_samples",
                    "control_measurable_samples", "mean_log2_T0_difference", "mean_log2_U_difference",
                    "mean_log2_W_difference"])


class ReportIntervalTests(unittest.TestCase):
    INTERVAL_MARK = "stroke='#b34c20' stroke-width='4'"

    def html_row(self, report, contrast="compression_vs_standard"):
        with tempfile.TemporaryDirectory() as directory:
            text = Path(write_report(report, directory)["html"]).read_text(encoding="utf-8")
        self.assertEqual(text.count("<table"), text.count("</table>"))
        self.assertEqual(text.count("<tr>"), text.count("</tr>"))
        return text, re.search(f"<tr><td>{contrast}</td>.*?</tr>", text).group(0)

    def test_single_family_effect_is_not_shown_as_a_precise_interval(self):
        tasks = [task(f"t{i}") for i in range(5)]
        attempts = [attempt("t0", c) for c in CONDITIONS] + [
            attempt(f"t{i}", c, status="elaboration_failure") for i in range(1, 5) for c in CONDITIONS]
        report = run(tasks, attempts, bootstrap_replicates=1000)
        effect = report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        self.assertEqual(effect["confidence_interval"], [0, 0])
        self.assertGreater(effect["bootstrap_undefined"], 0)
        text, row = self.html_row(report)
        self.assertIn("<th>Families</th><th>Mean log2 T0 change [95% interval]</th>", text)
        self.assertIn("<td>1</td><td>0 [not estimable (&lt;2 families)]</td>", row)
        self.assertIn(f"<td>{effect['bootstrap_defined']} / 1000</td>", row)
        self.assertNotIn("[0, 0]", row)
        # The paired-effects plot draws no zero-width interval either.
        self.assertNotIn(self.INTERVAL_MARK, text)
        self.assertIn("compression_vs_standard · 1 paired tasks / 1 families · interval not estimable (&lt;2 families)"
                      "</text>", text)

    def test_support_counts_families_not_tasks(self):
        # Two paired tasks from one family give one supported family, whatever the task count.
        tasks = [task("t0", "f"), task("t1", "f"), task("t2", "g")]
        attempts = [attempt(t, c, total=16 if (t, c) == ("t0", CONDITIONS[1]) else 8)
                    for t in ("t0", "t1") for c in CONDITIONS] + [
            attempt("t2", c, status="elaboration_failure") for c in CONDITIONS]
        report = run(tasks, attempts)
        effect = report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        self.assertEqual((effect["task_count"], effect["family_count"], effect["estimate"]), (2, 1, .5))
        text, row = self.html_row(report)
        self.assertIn("<td>compression_vs_standard</td><td>2</td><td>1</td>"
                      "<td>0.5 [not estimable (&lt;2 families)]</td>", row)
        self.assertNotIn(self.INTERVAL_MARK, text)
        self.assertIn("2 paired tasks / 1 families · interval not estimable", text)

    def test_supported_interval_shows_level_bounds_and_singleton_strata(self):
        tasks = [task("a"), task("b"), task("c", domain="analysis")]
        attempts = [attempt(t["task_id"], c, total=16 if t["task_id"] == "a" and c == CONDITIONS[1] else 8)
                    for t in tasks for c in CONDITIONS]
        report = run(tasks, attempts, confidence_level=.9)
        effect = report["models"]["model-frozen"]["contrasts"]["compression_vs_standard"]["mean_log2_T0_difference"]
        low, high = effect["confidence_interval"]
        text, row = self.html_row(report)
        self.assertIn("Mean log2 T0 change [90% interval]", text)
        self.assertIn(f"<td>3</td><td>{effect['estimate']:.4g} [{low:.4g}, {high:.4g}]</td><td>200 / 200</td>", row)
        self.assertIn("Single-family bootstrap strata, which resampling cannot vary: analysis.", text)
        self.assertEqual(text.count(self.INTERVAL_MARK), 2)
        self.assertNotIn("interval not estimable", text)


class ResourceShapeTests(unittest.TestCase):
    def measurements(self, usage):
        value = attempt("a", CONDITIONS[0])
        value["generation"] = {"usage": usage}
        report = run([task("a")], [value])
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(Path(write_report(report, directory)["resources"]).is_file())
        return report["models"]["model-frozen"]["conditions"][CONDITIONS[0]]["resources"]["measurements"]

    def test_non_mapping_usage_is_an_invalid_observation(self):
        for usage in (1234, "1234 tokens", [{"input_tokens": 1}], "", True):
            result = self.measurements(usage)
            for name in ("input_tokens", "output_tokens", "reasoning_tokens"):
                self.assertEqual((result[name]["observed_count"], result[name]["invalid_count"]), (0, 1), (usage, name))
        result = self.measurements(None)
        self.assertEqual((result["input_tokens"]["missing_count"], result["input_tokens"]["invalid_count"]), (1, 0))

    def test_non_mapping_token_details_invalidate_only_reasoning_tokens(self):
        for usage in ({"input_tokens": 10, "output_tokens": 4, "output_tokens_details": 5},
                      {"prompt_tokens": 10, "completion_tokens": 4, "completion_tokens_details": "n/a"}):
            result = self.measurements(usage)
            self.assertEqual(result["input_tokens"]["sum"], 10)
            self.assertEqual(result["output_tokens"]["sum"], 4)
            self.assertEqual((result["reasoning_tokens"]["observed_count"],
                              result["reasoning_tokens"]["invalid_count"]), (0, 1))
        result = self.measurements({"reasoning_tokens": 3, "output_tokens_details": 5})
        self.assertEqual((result["reasoning_tokens"]["sum"], result["reasoning_tokens"]["invalid_count"]), (3, 0))

    def test_absent_or_empty_token_details_defer_to_completion_details(self):
        later = {"completion_tokens_details": {"reasoning_tokens": 3}}
        for usage, expected in (({"output_tokens_details": {}, **later}, (3, 0)),
                                ({"output_tokens_details": None, **later}, (3, 0)),
                                ({"output_tokens_details": {"reasoning_tokens": 1}, **later}, (1, 0)),
                                ({"output_tokens_details": "", **later}, (None, 1))):
            result = self.measurements(usage)["reasoning_tokens"]
            self.assertEqual((result["sum"], result["invalid_count"]), expected, usage)


class StratumTests(unittest.TestCase):
    def mixed(self, labelled=True):
        easy = [task(f"e{i}", f"easy{i}", stratum="provisional_easy" if labelled else None) for i in range(3)]
        hard = [task(f"h{i}", f"hard{i}", stratum="provisional_source_length_short" if labelled else None)
                for i in range(3)]
        # e0 alone compresses under B, so the easy-control T0 interval has width.
        attempts = [attempt(t["task_id"], c, total=16 if (t["task_id"], c) == ("e0", CONDITIONS[1]) else 8,
                            status="elaboration_failure"
                            if t["task_id"].startswith("h") and c == CONDITIONS[1] else "success")
                    for t in easy + hard for c in CONDITIONS]
        return run(easy + hard, attempts)

    def test_easy_controls_are_reported_separately(self):
        report = self.mixed()
        model = report["models"]["model-frozen"]
        distributions = model["distributions"]
        self.assertEqual(distributions["stratum_class:easy_control"]["composition"]["task_count"], 3)
        self.assertEqual(distributions["stratum:provisional_source_length_short"]["composition"]["task_count"], 3)
        rates = {group: distributions[group]["correctness_by_condition"][CONDITIONS[1]]["success_rate"]
                 for group in ("stratum_class:easy_control", "stratum_class:non_control")}
        self.assertEqual(rates, {"stratum_class:easy_control": 1, "stratum_class:non_control": 0})
        groups = model["stratum_breakdown"]["groups"]
        self.assertEqual(groups["stratum_class:easy_control"]["task_ids"], ["e0", "e1", "e2"])
        self.assertEqual(groups["stratum_class:non_control"]["contrasts"]["compression_vs_standard"]
                         ["success_rate_difference"]["estimate"], -1)
        self.assertEqual(groups["stratum_class:easy_control"]["contrasts"]["compression_vs_standard"]
                         ["success_rate_difference"]["estimate"], 0)
        self.assertEqual(groups["stratum_class:non_control"]["contrasts"]["compression_vs_concision"]
                         ["mean_log2_T0_difference"]["task_count"], 0)
        self.assertEqual(model["contrasts"]["compression_vs_standard"]["success_rate_difference"]["estimate"], -.5)
        # Labels add subgroup reporting only; every pooled statistic is unchanged.
        pooled = self.mixed(labelled=False)["models"]["model-frozen"]
        for key in ("conditions", "contrasts", "per_task", "paired_tasks", "compression_conclusions_ready"):
            self.assertEqual(model[key], pooled[key])

    def test_subgroup_covering_every_task_replays_the_pooled_bootstrap(self):
        # Families that cross domains are drawn together; nested families are drawn within each domain.
        for nested in (False, True):
            tasks = [task(f"t{i}", f"f{i // 2}", ("algebra", "analysis", "sets")[(i // 2) % 3] if nested
                          else ("algebra", "analysis")[i % 2]) for i in range(8)]
            attempts = [attempt(t["task_id"], c, total=8 * (1 + (i % 3) * (c == CONDITIONS[1])),
                                status="elaboration_failure" if i == 5 and c != CONDITIONS[0] else "success")
                        for i, t in enumerate(tasks) for c in CONDITIONS]
            report = run(tasks, attempts, bootstrap_replicates=500)
            self.assertEqual(report["bootstrap"]["stratified_by_domain"], nested)
            model = report["models"]["model-frozen"]
            everything = model["stratum_breakdown"]["groups"]["stratum_class:non_control"]
            self.assertEqual(everything["composition"]["task_count"], 8)
            for name, contrast in model["contrasts"].items():
                for statistic in STRATUM_STATISTICS:
                    with self.subTest(nested=nested, contrast=name, statistic=statistic):
                        self.assertEqual(everything["contrasts"][name][statistic], contrast[statistic])

    def test_stratum_tables_are_written_beside_unchanged_pooled_tables(self):
        report = self.mixed()
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(report, directory)
            with open(paths["correctness_by_stratum"], encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual({row["group"] for row in rows}, {
                "stratum_class:easy_control", "stratum_class:non_control",
                "stratum:provisional_easy", "stratum:provisional_source_length_short"})
            easy_b = next(row for row in rows if row["group"] == "stratum_class:easy_control"
                          and row["condition"] == CONDITIONS[1])
            self.assertEqual((int(easy_b["task_count"]), float(easy_b["success_rate"])), (3, 1))
            with open(paths["contrasts_by_stratum"], encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4 * len(STRATUM_STATISTICS) * 2)
            hard = next(row for row in rows if row["group"] == "stratum_class:non_control"
                        and row["contrast"] == "compression_vs_standard" and row["statistic"] == "success_rate_difference")
            self.assertEqual((float(hard["estimate"]), int(hard["family_count"])), (-1, 3))
            self.assertEqual((float(hard["interval_low"]), float(hard["interval_high"])), (-1, -1))
            groups = report["models"]["model-frozen"]["stratum_breakdown"]["groups"]
            for row in rows:
                bounds = groups[row["group"]]["contrasts"][row["contrast"]][row["statistic"]]["confidence_interval"]
                self.assertEqual([row["interval_low"], row["interval_high"]], ["" if b is None else str(b) for b in bounds])
            text = Path(paths["html"]).read_text(encoding="utf-8")
            self.assertIn("Easy controls and other strata (exploratory)", text)
            self.assertIn("easy-control labels: <code>easy, provisional_easy</code>", text)
            self.assertIn("<tr><td>non_control</td><td>3 / 3</td><td>mathematical_compression</td>"
                          "<td>0 / 3</td><td>0.0%</td>", text)
            # Success-rate difference, then the T0 change: no task of this class is paired under B.
            self.assertIn("<tr><td>non_control</td><td>compression_vs_standard</td><td>-1 [-1, -1]</td><td>0 / 0</td>"
                          "<td>undefined [not estimable (&lt;2 families)]</td><td>0 / 200</td></tr>", text)
            self.assertEqual(text.count("<table"), text.count("</table>"))
        with tempfile.TemporaryDirectory() as directory:
            text = Path(write_report(self.mixed(labelled=False), directory)["html"]).read_text(encoding="utf-8")
        self.assertIn("<tr><td>easy_control</td><td>0 / 0</td><td colspan='5'>No analyzed task belongs to this class."
                      "</td></tr>", text)

    def test_easy_control_labels_are_configurable_and_validated(self):
        tasks = [task("w", stratum="warmup"), task("x", stratum="provisional_easy")]
        attempts = [attempt(t["task_id"], c) for t in tasks for c in CONDITIONS]
        groups = run(tasks, attempts, analysis={"easy_control_strata": ["warmup"]})[
            "models"]["model-frozen"]["stratum_breakdown"]["groups"]
        self.assertEqual(groups["stratum_class:easy_control"]["task_ids"], ["w"])
        self.assertEqual(groups["stratum_class:non_control"]["task_ids"], ["x"])
        with self.assertRaisesRegex(ValueError, "easy_control_strata"):
            run(tasks, attempts, easy_control_strata=5)


if __name__ == "__main__":
    unittest.main()
