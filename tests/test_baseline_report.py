"""analyze --baseline: the spec's trusted tactic-portfolio anchor, fail-closed and never silently partial."""
from contextlib import redirect_stderr
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.analysis import CONDITIONS, analyze as analyze_attempts, write_report
from lean_compression.baseline import NOT_SUPPLIED, load_baseline
from lean_compression.cli import dispatch, parser
from lean_compression.io import digest, read_json, task_input_hash, verification_request_hash, write_json
from lean_compression.store import create_run, load_run, record_generation

PROJECT = Path(__file__).resolve().parents[1]
ENGINE, OTHER = "e" * 64, "0" * 64
ABSENT = object()  # A policy key the fixture leaves out, as older baseline commands did.
CHECKED = ("kind", "engine_hash", "environment_hash", "config_hash", "portfolio_validation_wall_seconds")
# Expansion U per condition with W = 4, so log2 T0 = log2 U - 2; None is a failed proof.
TOTALS = {"a1": (8, 32, 8), "a2": (8, None, None), "n1": (None, 16, 8), "n2": (8, 8, 8)}


def checked(status, budget):
    return {"validity_status": status, "resources": {"wall_seconds_limit": budget}}


def baseline_record(task, manifest, engine, status="success", **changes):
    return {"record_kind": "trusted_tactic_baseline", "task_id": task["task_id"], "validity_status": status,
            "metric_status": "unavailable", "metrics": None,
            "validation": checked(status, manifest["config"]["tactic_portfolio_total_wall_seconds"]),
            "environment_hash": manifest["environment"]["environment_hash"], "engine_hash": engine,
            "proof": manifest["config"]["tactic_portfolio"], "task_hash": digest(task),
            "task_input_hash": task_input_hash(task), "verification_request_hash": verification_request_hash(task),
            **changes}


def write_baseline(directory, manifest, engine, records, **changes):
    policy = {"kind": "baseline", "engine_hash": engine, "environment_hash": manifest["environment"]["environment_hash"],
              "config_hash": manifest["config_hash"], "tasks_hash": "fixture", "references_hash": "fixture",
              "portfolio_validation_wall_seconds": manifest["config"]["tactic_portfolio_total_wall_seconds"], **changes}
    write_json(directory / "policy.json", {key: value for key, value in policy.items() if value is not ABSENT})
    for index, record in enumerate(records):
        write_json(directory / f"{index}-{digest(record['task_id'])}.json", record)
    return directory


def analyze(run, output, version, baseline=None):
    argv = ["analyze", "--run", str(run), "--version", version, "--output", str(output)]
    return dispatch(parser().parse_args(argv + (["--baseline", str(baseline)] if baseline else [])))


def frozen_run(run_dir, *, samples=1, budget=None, shared_families=False):
    """Four evaluated tasks with TOTALS; every sample after the first fails."""
    config = read_json(PROJECT / "configs" / "pilot.json")
    config["validation"]["isolation"] = "macos_sandbox"
    config["samples_per_goal_condition"] = samples
    config["analysis"]["bootstrap_replicates"] = 20
    if budget is not None:
        config["tactic_portfolio_total_wall_seconds"] = budget
    env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
    tasks = [{"task_id": name, "family_id": "family_" + (name[0] if shared_families else name), "domain": domain,
              "split": "pilot", "origin": "fresh", "target_name": "target_" + name, "binders": "(P : Prop) (h : P)",
              "proposition": "P", "imports": ["Init"], "premise_context": "", "context": ""}
             for name, domain in (("a1", "algebra"), ("a2", "algebra"), ("n1", "number_theory"),
                                  ("n2", "number_theory"))]
    manifest = create_run(run_dir, tasks, config, env, split="pilot", model="fixture", development=True)
    for slot in manifest["slots"]:
        total = TOTALS[slot["task_id"]][CONDITIONS.index(slot["condition"])] if slot["sample_index"] == 0 else None
        status = "elaboration_failure" if total is None else "success"
        # records() requires each evaluation to embed its stored generation, as evaluate writes it.
        generation = record_generation(run_dir, slot, "by exact h", generation_status="completed")
        write_json(run_dir / "evaluations" / "v1" / f"{slot['attempt_id']}.json", {
            "attempt_id": slot["attempt_id"], "engine_hash": ENGINE, "validity_status": status,
            "generation": generation, "generation_hash": digest(generation),
            "validation": {"validity_status": status}, "metric_status": "unavailable" if total is None else "available",
            "metrics": None if total is None else {"metric_status": "available", "W_S": 1, "W_B": 3, "D": 2,
                                                  "U_S_decimal": "1", "U_B_decimal": str(total - 1)}})
    return manifest, {task["task_id"]: task for task in tasks}


class DemoBaselineTests(unittest.TestCase):
    """The committed demo keeps analyzing; a baseline must match its frozen checker."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls.temp.name) / "run"
        shutil.copytree(PROJECT / "examples" / "pipeline-demo", cls.run_dir)
        cls.manifest = load_run(cls.run_dir)
        cls.task = cls.manifest["tasks"][0]
        cls.engine = read_json(cls.run_dir / "evaluations" / "v2" / "policy.json")["engine_hash"]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.temp.name))

    def test_absent_baseline_is_recorded_explicitly(self):
        analyze(self.run_dir, self.work / "report", "v2")
        self.assertEqual(read_json(self.work / "report" / "analysis.json")["automation_baseline"], NOT_SUPPLIED)
        self.assertFalse((self.work / "report" / "automation_baseline.csv").exists())
        page = (self.work / "report" / "report.html").read_text(encoding="utf-8")
        self.assertIn("No trusted tactic-portfolio baseline was supplied", page)

    def test_matching_baseline_is_reported(self):
        baseline = write_baseline(self.work / "baseline", self.manifest, self.engine,
                                  [baseline_record(self.task, self.manifest, self.engine)])
        analyze(self.run_dir, self.work / "report", "v2", baseline)
        summary = read_json(self.work / "report" / "analysis.json")["automation_baseline"]
        self.assertEqual((summary["status"], summary["overall"]["solve_rate"]), ("complete", 1))
        self.assertTrue(summary["per_task"][self.task["task_id"]]["portfolio_solved"])
        self.assertEqual(summary["portfolio"], self.manifest["config"]["tactic_portfolio"])
        self.assertEqual(summary["policy"], {key: read_json(baseline / "policy.json")[key] for key in CHECKED})
        solved = summary["models"]["reference_fixture"]["conditions"]["standard"]["solved"]
        self.assertEqual((solved["tasks"], solved["success_rate"], solved["measurable_tasks"]), (1, 1, 1))
        with (self.work / "report" / "automation_baseline.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(sorted(row["condition"] for row in rows), sorted(CONDITIONS))
        self.assertEqual({row["portfolio_outcome"] for row in rows}, {"solved"})
        page = (self.work / "report" / "report.html").read_text(encoding="utf-8")
        self.assertIn("Automation baseline: trusted tactic portfolio", page)
        self.assertIn("not a preregistered decision quantity", page)
        self.assertIn("(validation budget 60 s)", page)

    def test_baseline_command_output_is_accepted(self):
        # The baseline command's policy and records, from a stubbed checker, satisfy every analyze gate.
        inputs = {name: self.work / f"{name}.json" for name in ("tasks", "config", "environment")}
        for name, path in inputs.items():
            write_json(path, self.manifest[name])
        checker = lambda request, environment, engine, policy, *, mode, wall_seconds: {
            "validity_status": "success", "metric_status": "unavailable", "resources": {"wall_seconds_limit": wall_seconds}}
        argv = ["baseline", "--output", str(self.work / "baseline"),
                "--engine", str(PROJECT / "lean" / "versions" / "Evaluator-3c093c02.lean")]
        argv += [item for name, path in inputs.items() for item in (f"--{name}", str(path))]
        with patch("lean_compression.runner.execute_engine", checker), \
                patch("lean_compression.cli.load_environment", read_json), redirect_stderr(io.StringIO()):
            dispatch(parser().parse_args(argv))
        analyze(self.run_dir, self.work / "report", "v2", self.work / "baseline")
        summary = read_json(self.work / "report" / "analysis.json")["automation_baseline"]
        self.assertEqual((summary["status"], summary["overall"]["solved"]), ("complete", 1))
        self.assertEqual(summary["policy"]["portfolio_validation_wall_seconds"],
                         self.manifest["config"]["tactic_portfolio_total_wall_seconds"])

    def test_unchecked_policy_fields_are_not_reported(self):
        # A non-JSON value in a field no gate reads cannot break the report after its directory exists.
        baseline = write_baseline(self.work / "baseline", self.manifest, self.engine,
                                  [baseline_record(self.task, self.manifest, self.engine)])
        policy = read_json(baseline / "policy.json")
        (baseline / "policy.json").unlink()
        (baseline / "policy.json").write_text(json.dumps({**policy, "tasks_hash": float("nan")}), encoding="utf-8")
        analyze(self.run_dir, self.work / "report", "v2", baseline)
        summary = read_json(self.work / "report" / "analysis.json")["automation_baseline"]
        self.assertEqual(summary["policy"], {key: policy[key] for key in CHECKED})

    def test_incompatible_baselines_fail_closed(self):
        good = baseline_record(self.task, self.manifest, self.engine)
        cases = [("not produced by the baseline command", {"kind": "verify-references"}, [good]),
                 ("environment differs", {"environment_hash": OTHER}, [good]),
                 ("configuration differs", {"config_hash": OTHER}, [good]),
                 ("not produced by the baseline command", {"engine_hash": ""}, [{**good, "engine_hash": ""}]),
                 ("frozen tactic_portfolio_total_wall_seconds", {"portfolio_validation_wall_seconds": None}, [good]),
                 ("frozen tactic_portfolio_total_wall_seconds", {"portfolio_validation_wall_seconds": 30}, [good]),
                 ("into a new output directory", {"portfolio_validation_wall_seconds": ABSENT}, [good]),
                 ("engine differs", {"engine_hash": OTHER}, [{**good, "engine_hash": OTHER}]),
                 ("Not a trusted tactic baseline record", {}, [{**good, "record_kind": "verified_reference"}]),
                 ("Not a trusted tactic baseline record", {}, [{**good, "task_id": 5}]),
                 ("not checked under the frozen portfolio budget", {}, [{**good, "validation": checked("success", 30)}]),
                 ("not checked under the frozen portfolio budget", {}, [{**good, "validation": None}]),
                 ("incompatible environment, engine", {}, [{**good, "engine_hash": OTHER}]),
                 ("incompatible environment, engine", {}, [{**good, "environment_hash": OTHER}]),
                 ("portfolio provenance", {}, [{**good, "proof": "by simp"}]),
                 ("does not match its frozen task", {}, [{**good, "task_hash": OTHER, "task_input_hash": OTHER,
                                                          "verification_request_hash": OTHER}]),
                 ("Duplicate baseline records", {}, [good, good])]
        for index, (message, policy, records) in enumerate(cases):
            with self.subTest(message=message, index=index):
                baseline = write_baseline(self.work / f"baseline{index}", self.manifest, self.engine, records, **policy)
                with self.assertRaisesRegex(ValueError, message):
                    analyze(self.run_dir, self.work / f"report{index}", "v2", baseline)
                self.assertFalse((self.work / f"report{index}").exists())
        bare = self.work / "no-policy"
        write_json(bare / "record.json", good)
        with self.assertRaisesRegex(ValueError, "no policy.json"):
            analyze(self.run_dir, self.work / "report-bare", "v2", bare)
        with self.assertRaisesRegex(ValueError, "not produced by the baseline command"):
            analyze(self.run_dir, self.work / "report-references", "v2", PROJECT / "examples" / "demo-reference-check")
        policy = read_json(write_baseline(self.work / "template", self.manifest, self.engine, []) / "policy.json")
        files = [("not produced by the baseline command", {"policy.json": [policy]}),
                 ("Not a trusted tactic baseline record: record.json", {"policy.json": policy, "record.json": [good]}),
                 ("Malformed JSON in baseline directory: .*broken.json", {"policy.json": policy, "broken.json": "{"}),
                 ("Malformed JSON in baseline directory: .*policy.json", {"policy.json": "{"})]
        for index, (message, contents) in enumerate(files):
            with self.subTest(message=message):
                directory = self.work / f"files{index}"
                directory.mkdir()
                for name, value in contents.items():
                    (directory / name).write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    analyze(self.run_dir, self.work / f"report-files{index}", "v2", directory)
                self.assertFalse((self.work / f"report-files{index}").exists())


class CrossTabTests(unittest.TestCase):
    """Four tasks: portfolio solved, unsolved, unresolved and missing."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run_dir = self.root / "run"
        self.manifest, self.tasks = frozen_run(self.run_dir)

    def summary(self, name, records):
        analyze(self.run_dir, self.root / ("report-" + name), "v1",
                write_baseline(self.root / name, self.manifest, ENGINE, records))
        return read_json(self.root / ("report-" + name) / "analysis.json")["automation_baseline"]

    def test_cross_tab_keeps_missing_and_unresolved_tasks_visible(self):
        records = [baseline_record(self.tasks["a1"], self.manifest, ENGINE),
                   baseline_record(self.tasks["n1"], self.manifest, ENGINE, "elaboration_failure"),
                   baseline_record(self.tasks["a2"], self.manifest, ENGINE, "infrastructure_error"),
                   baseline_record({"task_id": "outside_run"}, self.manifest, ENGINE)]
        baseline = write_baseline(self.root / "baseline", self.manifest, ENGINE, records)
        analyze(self.run_dir, self.root / "report", "v1", baseline)
        summary = read_json(self.root / "report" / "analysis.json")["automation_baseline"]
        self.assertEqual((summary["status"], summary["complete"]), ("incomplete", False))
        self.assertEqual((summary["missing_task_ids"], summary["unresolved_task_ids"]), (["n2"], ["a2"]))
        self.assertEqual(summary["records_outside_run"], 1)
        self.assertEqual({key: summary["overall"][key] for key in ("tasks", "solved", "unsolved", "unresolved", "missing")},
                         {"tasks": 4, "solved": 1, "unsolved": 1, "unresolved": 1, "missing": 1})
        self.assertEqual(summary["overall"]["solve_rate"], .25)
        self.assertEqual({domain: row["solve_rate"] for domain, row in summary["by_domain"].items()},
                         {"algebra": .5, "number_theory": 0})
        self.assertEqual({task_id: row["portfolio_solved"] for task_id, row in summary["per_task"].items()},
                         {"a1": True, "a2": None, "n1": False, "n2": None})
        cross = summary["models"]["fixture"]
        success = {condition: [cross["conditions"][condition][group]["success_rate"]
                               for group in ("solved", "unsolved", "unknown")] for condition in CONDITIONS}
        self.assertEqual(success, {"standard": [1, 0, 1], "mathematical_compression": [1, 1, .5],
                                   "source_concision": [1, 1, .5]})
        compression = cross["conditions"]["mathematical_compression"]
        self.assertEqual([compression[group]["mean_task_log2_T0"] for group in ("solved", "unsolved", "unknown")], [3, 2, 1])
        self.assertEqual([compression[group]["mean_task_log2_U"] for group in ("solved", "unsolved", "unknown")], [5, 4, 3])
        self.assertEqual([compression[group]["mean_task_log2_W"] for group in ("solved", "unsolved", "unknown")], [2, 2, 2])
        self.assertEqual([compression[group]["measurable_tasks"] for group in ("solved", "unsolved", "unknown")], [1, 1, 1])
        changes = {name: [(cells[group]["paired_tasks"], cells[group]["mean_log2_T0_difference"])
                          for group in ("solved", "unsolved", "unknown")] for name, cells in cross["contrasts"].items()}
        self.assertEqual(changes, {"compression_vs_standard": [(1, 2), (0, None), (1, 0)],
                                   "compression_vs_concision": [(1, 2), (1, 1), (1, 0)]})
        with (self.root / "report" / "automation_baseline.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 12)
        self.assertEqual({row["portfolio_outcome"] for row in rows if row["task_id"] == "n2"}, {"missing"})
        page = (self.root / "report" / "report.html").read_text(encoding="utf-8")
        self.assertIn("Baseline incomplete:</strong> 1 tasks have no baseline record and 1 have unresolved", page)
        self.assertIn("1 baseline records for tasks outside this run were not used", page)

    def test_exhausted_budgets_are_unsolved_and_any_gap_leaves_the_section_incomplete(self):
        record = lambda name, status="success", **changes: baseline_record(
            self.tasks[name], self.manifest, ENGINE, status, **changes)
        resolved = [record("a1"), record("a2", "verification_timeout"), record("n1", "verification_resource_limit")]
        budget = self.manifest["config"]["tactic_portfolio_total_wall_seconds"]
        cases = {"complete": ([record("n2", "elaboration_failure")], [], []),
                 "missing": ([], ["n2"], []),
                 "unresolved": ([record("n2", "infrastructure_error")], [], ["n2"]),
                 # Only the Lean verdict makes a success: none recorded, or the engine changed after it.
                 "no-verdict": ([record("n2", validation={"resources": {"wall_seconds_limit": budget}})], [], ["n2"]),
                 "engine-changed": ([record("n2", "infrastructure_error", validation=checked("success", budget))], [], ["n2"])}
        for name, (extra, missing, unresolved) in cases.items():
            with self.subTest(name):
                summary = self.summary(name, resolved + extra)
                self.assertEqual((summary["missing_task_ids"], summary["unresolved_task_ids"]), (missing, unresolved))
                self.assertEqual((summary["status"], summary["complete"]),
                                 ("complete", True) if name == "complete" else ("incomplete", False))
                self.assertEqual({task_id: row["portfolio_solved"] for task_id, row in summary["per_task"].items()},
                                 {"a1": True, "a2": False, "n1": False, "n2": False if name == "complete" else None})
                self.assertEqual(summary["overall"]["solved"], 1)

    def test_engine_must_match_evaluated_records_and_version_policy(self):
        records = [baseline_record(task, self.manifest, ENGINE) for task in self.tasks.values()]
        baseline = write_baseline(self.root / "baseline-unevaluated", self.manifest, ENGINE, records)
        with self.assertRaisesRegex(ValueError, "Evaluation version v9 records no checker engine"):
            analyze(self.run_dir, self.root / "report", "v9", baseline)
        self.assertFalse((self.root / "report").exists())
        records = [baseline_record(task, self.manifest, OTHER) for task in self.tasks.values()]
        baseline = write_baseline(self.root / "baseline", self.manifest, OTHER, records)
        with self.assertRaisesRegex(ValueError, "engine differs from evaluated outputs"):
            analyze(self.run_dir, self.root / "report", "v1", baseline)
        self.assertFalse((self.root / "report").exists())
        records = [baseline_record(task, self.manifest, ENGINE) for task in self.tasks.values()]
        baseline = write_baseline(self.root / "baseline-same", self.manifest, ENGINE, records)
        write_json(self.run_dir / "evaluations" / "v1" / "policy.json", {"engine_hash": OTHER})
        with self.assertRaisesRegex(ValueError, "engine differs from evaluated outputs"):
            analyze(self.run_dir, self.root / "report", "v1", baseline)

    def test_run_without_a_frozen_portfolio_budget_matches_no_baseline(self):
        # The baseline command refuses such a configuration, so rerunning it cannot help.
        config = {key: value for key, value in self.manifest["config"].items()
                  if key != "tactic_portfolio_total_wall_seconds"}
        baseline = write_baseline(self.root / "baseline", self.manifest, ENGINE, [])
        with self.assertRaisesRegex(ValueError, "frozen configuration has no tactic_portfolio_total_wall_seconds"):
            load_baseline(baseline, {**self.manifest, "config": config}, [],
                          self.run_dir / "evaluations" / "v1" / "policy.json")

    def test_library_report_without_baseline_states_absence(self):
        report = analyze_attempts([{"task_id": "t", "family_id": "f", "domain": "d"}], [], {},
                                  {"bootstrap_replicates": 0, "model_snapshots": ["m"]})
        with tempfile.TemporaryDirectory() as directory:
            paths = write_report(report, directory)
            self.assertNotIn("automation_baseline", paths)
            self.assertFalse((Path(directory) / "automation_baseline.csv").exists())
            self.assertIn("No trusted tactic-portfolio baseline was supplied", Path(paths["html"]).read_text(encoding="utf-8"))


class FrozenBudgetTests(unittest.TestCase):
    """The portfolio budget (300 s) differs from validation.wall_seconds; families hold two tasks; two samples."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run_dir = self.root / "run"
        self.manifest, self.tasks = frozen_run(self.run_dir, samples=2, budget=300, shared_families=True)
        self.assertEqual(self.manifest["config"]["validation"]["wall_seconds"], 60)

    def records(self, **changes):
        return [baseline_record(self.tasks[name], self.manifest, ENGINE, status, **changes) for name, status in
                (("a1", "success"), ("a2", "elaboration_failure"), ("n1", "elaboration_failure"), ("n2", "success"))]

    def test_policy_and_records_must_use_the_frozen_portfolio_budget(self):
        cases = [("frozen tactic_portfolio_total_wall_seconds", {"portfolio_validation_wall_seconds": 60}, self.records()),
                 ("frozen tactic_portfolio_total_wall_seconds", {"portfolio_validation_wall_seconds": ABSENT}, self.records()),
                 ("not checked under the frozen portfolio budget", {}, self.records(validation=checked("success", 60)))]
        for index, (message, policy, records) in enumerate(cases):
            with self.subTest(message=message, index=index):
                baseline = write_baseline(self.root / f"baseline{index}", self.manifest, ENGINE, records, **policy)
                with self.assertRaisesRegex(ValueError, message):
                    analyze(self.run_dir, self.root / f"report{index}", "v1", baseline)
                self.assertFalse((self.root / f"report{index}").exists())

    def test_cross_tab_counts_families_and_any_sample_success(self):
        analyze(self.run_dir, self.root / "report", "v1",
                write_baseline(self.root / "baseline", self.manifest, ENGINE, self.records()))
        summary = read_json(self.root / "report" / "analysis.json")["automation_baseline"]
        self.assertEqual((summary["status"], summary["policy"]["portfolio_validation_wall_seconds"]), ("complete", 300))
        self.assertEqual({key: summary["overall"][key] for key in ("tasks", "families", "solved", "solve_rate")},
                         {"tasks": 4, "families": 2, "solved": 2, "solve_rate": .5})
        self.assertEqual({domain: (row["tasks"], row["families"]) for domain, row in summary["by_domain"].items()},
                         {"algebra": (2, 1), "number_theory": (2, 1)})
        # The second sample always fails: success rates halve while any-sample success does not.
        rates = {condition: [(cells[group]["success_rate"], cells[group]["any_sample_success_rate"])
                             for group in ("solved", "unsolved")]
                 for condition, cells in summary["models"]["fixture"]["conditions"].items()}
        self.assertEqual(rates, {condition: [(.5, 1), (.25, .5)] for condition in CONDITIONS})
        self.assertIn("(validation budget 300 s)", (self.root / "report" / "report.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
