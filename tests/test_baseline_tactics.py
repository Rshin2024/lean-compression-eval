"""Each frozen tactic-portfolio alternative is its own Lean check.

The 2026-10-12 container baseline checked `by first | rfl | assumption | (simp_all; done) | omega | aesop`
as one proof. Aesop is not among the minimal imports of 18 of its 250 tasks, so `aesop` was an unknown
tactic, the whole portfolio a parse failure, and rfl, assumption, simp_all and omega never ran.
tactic_portfolio_tactics now lists the alternatives; `baseline` checks each as `by <tactic>`, in order,
until one succeeds, all sharing tactic_portfolio_total_wall_seconds. The stubbed classes replace the
engine process; the Lean class runs the real command under the macOS development sandbox and skips
without the pinned runtime.
"""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import platform
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lean_compression import cli
from lean_compression.integrity import portfolio_tactics
from lean_compression.io import digest, file_hash, read_json, read_jsonl, write_json
from lean_compression.store import create_run, record_generation

try:
    from lean_runtime import manifest_path, pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_baseline_tactics, run from the repository root
    from tests.lean_runtime import manifest_path, pinned_runtime

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEVELOPMENT = read_json(PROJECT / "configs/development.json")
TACTICS = ["rfl", "assumption", "(simp_all; done)", "omega", "aesop"]
LEGACY = "by first | rfl | assumption | (simp_all; done) | omega | aesop"
LEGACY_CONFIG = {**{key: value for key, value in DEVELOPMENT.items() if key != "tactic_portfolio_tactics"},
                 "tactic_portfolio": LEGACY}
# Init does not load Aesop; omega proves the goal.
FIXTURE = {"split": "pilot", "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "",
           "premise_context": "", "forbidden_declarations": [], "binders": "(x y : Nat) (h1 : x + y = 10) (h2 : x = y + 2)",
           "proposition": "x = 6"}
TASKS = [{**FIXTURE, "task_id": name, "family_id": name, "domain": domain, "target_name": name, "proposition": goal}
         for name, domain, goal in (("solved", "number_theory", "x = 6"), ("unsolved", "sets_logic", "y = 4"),
                                    ("spent", "algebra_groups", "x - y = 2"))]
# The stubbed engine's validation outcomes by (task, proof): status and elapsed seconds, where "limit"
# runs to the check's wall limit and None records no elapsed time. Others are rejected in a second.
OUTCOMES = {("solved", "by rfl"): ("parse_failure", 2.0), ("solved", "by assumption"): ("elaboration_failure", 3.0),
            ("solved", "by (simp_all; done)"): ("elaboration_failure", 5.0), ("solved", "by omega"): ("success", 1.5),
            ("unsolved", "by aesop"): ("parse_failure", 1.0),
            ("spent", "by rfl"): ("elaboration_failure", 10.0), ("spent", "by assumption"): ("verification_timeout", "limit"),
            ("solved", LEGACY): ("success", 4.0), ("unsolved", LEGACY): ("parse_failure", 2.5)}


class FakeEngine:
    """Plays execute_engine from OUTCOMES and any overrides; a success's extraction leaves it unmeasured."""

    def __init__(self, overrides=None):
        self.outcomes, self.checks = {**OUTCOMES, **(overrides or {})}, []

    def __call__(self, request, environment, engine, policy, *, mode, wall_seconds):
        if mode == "extract":
            return {"validity_status": "success", "metric_status": "unavailable", "metric_error": "stubbed extraction"}
        self.checks.append((request["target_name"], request["proof"], wall_seconds))
        status, elapsed = self.outcomes.get((request["target_name"], request["proof"]), ("elaboration_failure", 1.0))
        resources = {"wall_seconds_limit": wall_seconds}
        if elapsed is not None:
            resources["elapsed_seconds"] = wall_seconds + 0.25 if elapsed == "limit" else elapsed
        if status == "success":
            return {"validity_status": status, "metric_status": "not_requested", "kernel_replay": True,
                    "candidate_declarations": [request["target_name"]], "resources": resources}
        return {"validity_status": status, "metric_status": "unavailable", "failure_reason": f"{request['proof']}: {status}",
                "resources": resources}


def quiet(argv, engine=None):
    """cli.main with the engine process played by `engine`: (parsed stdout or exit code, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            patch("lean_compression.cli.load_environment", read_json), \
            patch("lean_compression.runner.execute_engine", engine or FakeEngine()):
        try:
            cli.main([str(part) for part in argv])
        except SystemExit as stopped:
            return stopped.code, err.getvalue()
    return json.loads(out.getvalue()), err.getvalue()


def frozen_run(root, config):
    """A development run of TASKS, one sample per condition, every attempt valid under the checked engine."""
    config = {**config, "samples_per_goal_condition": 1, "analysis": {**config["analysis"], "bootstrap_replicates": 20}}
    env = {"environment_hash": "fixture", "lean_toolchain": config["environment"]["lean_toolchain"],
           "mathlib_commit": config["environment"]["mathlib_commit"], "hash_mode": "development_metadata_only"}
    manifest = create_run(root / "run", TASKS, config, env, split="pilot", model="fixture", development=True)
    for slot in manifest["slots"]:
        generation = record_generation(root / "run", slot, "by omega", generation_status="completed")
        write_json(root / "run/evaluations/v1" / f"{slot['attempt_id']}.json", {
            "attempt_id": slot["attempt_id"], "engine_hash": file_hash(ENGINE), "validity_status": "success",
            "generation": generation, "generation_hash": digest(generation), "validation": {"validity_status": "success"},
            "metric_status": "available", "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "D": 2,
                                                      "U_S_decimal": "1", "U_B_decimal": "7"}})
    # The inputs that froze the run are the ones its baseline must use.
    for name in ("tasks", "config", "environment"):
        write_json(root / f"{name}.json", manifest[name])
    return manifest


def baseline_argv(root, output="baseline"):
    return ["baseline", "--tasks", root / "tasks.json", "--environment", root / "environment.json",
            "--config", root / "config.json", "--engine", ENGINE, "--output", root / output]


def analyze(root, baseline, output="report"):
    return quiet(["analyze", "--run", root / "run", "--version", "v1", "--output", root / output,
                  "--baseline", root / baseline])


def records(directory):
    return {record["task_id"]: record for path in directory.glob("*.json") if path.name != "policy.json"
            for record in [read_json(path)]}


def summary(root, output="report"):
    return read_json(root / output / "analysis.json")["automation_baseline"]


class PortfolioGateTests(unittest.TestCase):
    """A configuration names one portfolio, unambiguously."""

    def test_the_tactic_list_or_else_a_legacy_string_names_the_portfolio(self):
        self.assertEqual(portfolio_tactics(DEVELOPMENT), TACTICS)
        self.assertIsNone(portfolio_tactics(LEGACY_CONFIG))
        cases = [("names both", {**DEVELOPMENT, "tactic_portfolio": LEGACY}),
                 ("names both", {**DEVELOPMENT, "tactic_portfolio": None}),
                 ("names no tactic portfolio", {key: value for key, value in LEGACY_CONFIG.items() if key != "tactic_portfolio"}),
                 ("names no tactic portfolio", {**LEGACY_CONFIG, "tactic_portfolio": " "}),
                 *(("non-empty list of distinct, non-blank tactics", {**DEVELOPMENT, "tactic_portfolio_tactics": tactics})
                   for tactics in ([], LEGACY, ["rfl", ""], ["rfl", 5], ["rfl", ["omega"]], ["rfl", "omega", "rfl"]))]
        for message, config in cases:
            with self.subTest(message=message, tactics=config.get("tactic_portfolio_tactics")), \
                    self.assertRaisesRegex(ValueError, message):
                portfolio_tactics(config)

    def test_baseline_refuses_an_ambiguous_or_malformed_portfolio_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "tasks.json", TASKS)
            write_json(root / "environment.json", {"environment_hash": "fixture"})
            for message, config in (("names both", {**DEVELOPMENT, "tactic_portfolio": LEGACY}),
                                    ("distinct, non-blank", {**DEVELOPMENT, "tactic_portfolio_tactics": ["rfl", "rfl"]})):
                with self.subTest(message):
                    write_json(root / "config.json", config, immutable=False)
                    engine = FakeEngine()
                    code, log = quiet(baseline_argv(root), engine)
                    self.assertEqual(code, 2)
                    self.assertIn(message, log)
                    self.assertEqual(engine.checks, [])
                    self.assertFalse((root / "baseline").exists())


class SeparateChecksTests(unittest.TestCase):
    """The real command and analyze with a stubbed engine process: solved by omega, unsolved, budget spent."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.manifest = frozen_run(self.root, DEVELOPMENT)

    def baseline(self, overrides=None, output="baseline"):
        engine = FakeEngine(overrides)
        result, log = quiet(baseline_argv(self.root, output), engine)
        checks = {}
        for target, proof, limit in engine.checks:
            checks.setdefault(target, []).append((proof, limit))
        return result, log, checks

    def test_alternatives_run_in_order_until_one_succeeds_within_the_shared_budget(self):
        result, log, checks = self.baseline()
        self.assertEqual((result["new_checks"], result["verified"], result["checker_incidents"]), (3, 1, 0), log)
        # Each check gets what the elapsed times before it left; a parse failure does not stop the next, but a
        # timeout at the wall-clock limit spends the budget (spent).
        self.assertEqual(checks, {"solved": [("by rfl", 60), ("by assumption", 58.0), ("by (simp_all; done)", 55.0),
                                             ("by omega", 50.0)],
                                  "unsolved": [("by " + tactic, 60 - index) for index, tactic in enumerate(TACTICS)],
                                  "spent": [("by rfl", 60), ("by assumption", 50.0)]})
        found = records(self.root / "baseline")
        outcome = lambda name: [(attempt["tactic"], attempt["validity_status"], attempt["elapsed_seconds"],
                                 attempt["wall_seconds_limit"]) for attempt in found[name]["portfolio_alternatives"]]
        self.assertEqual(outcome("solved"), [("rfl", "parse_failure", 2.0, 60), ("assumption", "elaboration_failure", 3.0, 58.0),
                                             ("(simp_all; done)", "elaboration_failure", 5.0, 55.0), ("omega", "success", 1.5, 50.0)])
        self.assertEqual(outcome("spent"), [("rfl", "elaboration_failure", 10.0, 60), ("assumption", "verification_timeout", 50.25, 50.0)])
        # The record is its deciding check: the success, or else the last alternative attempted.
        self.assertEqual({name: (record["validity_status"], record["validation"]["validity_status"], record["proof"],
                                 record["record_kind"], record["portfolio_tactics"]) for name, record in found.items()},
                         {"solved": ("success", "success", "by omega", "trusted_tactic_baseline", TACTICS),
                          "unsolved": ("parse_failure", "parse_failure", "by aesop", "trusted_tactic_baseline", TACTICS),
                          "spent": ("verification_timeout", "verification_timeout", "by assumption", "trusted_tactic_baseline", TACTICS)})
        self.assertEqual((found["solved"]["source_hash"], found["solved"]["metric_unavailable_reason"]),
                         (digest("by omega"), "stubbed extraction"))
        policy = read_json(self.root / "baseline/policy.json")
        self.assertEqual((policy["portfolio_tactics"], policy["portfolio_validation_wall_seconds"]), (TACTICS, 60))
        self.assertIn("solved: success / unavailable (rfl: parse_failure, assumption: elaboration_failure, "
                      "(simp_all; done): elaboration_failure, omega: success)", log)
        self.assertNotIn("Legacy", log)
        # A rerun builds the same policy and finds every task recorded.
        result, log, checks = self.baseline()
        self.assertEqual((result["new_checks"], checks), (0, {}), log)

    def test_a_checker_incident_in_any_alternative_leaves_the_task_pending(self):
        result, log, _ = self.baseline({("solved", "by assumption"): ("infrastructure_error", 3.0),
                                        ("unsolved", "by rfl"): ("elaboration_failure", None)})
        self.assertEqual((result["new_checks"], result["checker_incidents"]), (1, 2), log)
        self.assertIn("solved: infrastructure_error (checker incident; task left pending)", log)
        incidents = {row["task_id"]: row for row in read_jsonl(self.root / "baseline/incidents.jsonl")}
        self.assertEqual([(attempt["tactic"], attempt["validity_status"]) for attempt in
                          incidents["solved"]["portfolio_alternatives"]], [("rfl", "parse_failure"), ("assumption", "infrastructure_error")])
        self.assertEqual(incidents["solved"]["proof"], "by assumption")
        # A rejection whose elapsed time was not recorded cannot be charged to the budget.
        self.assertEqual((incidents["unsolved"]["validity_status"], incidents["unsolved"]["failure_reason"]),
                         ("infrastructure_error", "portfolio_elapsed_unrecorded"))
        self.assertEqual(set(records(self.root / "baseline")), {"spent"})
        # A rerun checks each pending task's whole portfolio again under the whole budget.
        result, log, checks = self.baseline()
        self.assertEqual((result["new_checks"], result["checker_incidents"]), (2, 0), log)
        self.assertEqual(checks["solved"][:2], [("by rfl", 60), ("by assumption", 58.0)])
        self.assertEqual(records(self.root / "baseline")["solved"]["proof"], "by omega")

    def test_an_engine_changed_between_alternatives_stops_the_run(self):
        engine_hash = file_hash(ENGINE)
        def lean(task, proof, environment, engine, config):
            # The first check ran under an edited engine, restored before the second.
            return {"validity_status": "elaboration_failure", "metric_status": "unavailable",
                    "engine_hash": "0" * 64 if proof == "by rfl" else engine_hash,
                    "validation": {"validity_status": "elaboration_failure", "resources": {
                        "elapsed_seconds": 1.0, "wall_seconds_limit": config["validation"]["wall_seconds"]}}}
        with patch("lean_compression.runner.evaluate_proof", side_effect=lean) as checked:
            code, log = quiet(baseline_argv(self.root))
        self.assertEqual((code, checked.call_count), (2, 2))
        self.assertIn("Engine changed during this run", log)
        self.assertEqual(records(self.root / "baseline"), {})
        self.assertEqual(list((self.root / "baseline").glob("*.claim")), [])

    def test_the_report_shows_each_alternative(self):
        self.baseline()
        result, log = analyze(self.root, "baseline")
        self.assertIsInstance(result, dict, log)
        section = summary(self.root)
        self.assertEqual((section["status"], section["portfolio_semantics"], section["portfolio"]),
                         ("complete", "separate_checks", TACTICS))
        self.assertEqual((section["policy"]["portfolio_tactics"], section["policy"]["portfolio_validation_wall_seconds"]),
                         (TACTICS, 60))
        self.assertEqual(section["solved_by_tactic"], {"rfl": 0, "assumption": 0, "(simp_all; done)": 0, "omega": 1, "aesop": 0})
        self.assertEqual({name: (row["outcome"], row["solved_by"], row["validity_status"], [a["tactic"] for a in row["alternatives"]])
                          for name, row in section["per_task"].items()},
                         {"solved": ("solved", "omega", "success", TACTICS[:4]),
                          "unsolved": ("unsolved", None, "parse_failure", TACTICS),
                          "spent": ("unsolved", None, "verification_timeout", TACTICS[:2])})
        self.assertIn("checked as a separate proof", section["interpretation"][-1])
        with (self.root / "report/automation_baseline.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({row["task_id"]: row["portfolio_solved_by"] for row in rows}, {"solved": "omega", "unsolved": "", "spent": ""})
        page = (self.root / "report/report.html").read_text(encoding="utf-8")
        for text in ("(validation budget 60 s)", "<code>omega</code> 1", "Validity status of the last alternative attempted",
                     "rfl: parse_failure (2.0 s); assumption: elaboration_failure (3.0 s)",
                     "rfl: elaboration_failure (10.0 s); assumption: verification_timeout (50.2 s)"):
            self.assertIn(text, page)

    def test_records_that_break_the_portfolio_rule_are_refused(self):
        self.baseline()
        def skip_aesop(record):
            # Stopped after omega with budget left, otherwise consistent.
            *kept, _ = record["portfolio_alternatives"]
            record.update(portfolio_alternatives=kept, validity_status=kept[-1]["validity_status"], proof="by omega")
            record["validation"].update(validity_status=kept[-1]["validity_status"])
            record["validation"]["resources"].update(wall_seconds_limit=kept[-1]["wall_seconds_limit"],
                                                     elapsed_seconds=kept[-1]["elapsed_seconds"])
        def alternative(index, **changes):
            return lambda record: record["portfolio_alternatives"][index].update(changes)
        provenance, order, budget = ("incompatible environment, engine or portfolio provenance",
                                     "does not follow the frozen portfolio order", "not checked under the frozen portfolio budget")
        cases = [(provenance, "solved", lambda record: record.pop("portfolio_tactics")),
                 (provenance, "solved", lambda record: record.update(portfolio_tactics=TACTICS[::-1])),
                 (order, "solved", lambda record: record["portfolio_alternatives"].reverse()),
                 (order, "solved", lambda record: record.pop("portfolio_alternatives")),
                 (order, "solved", alternative(0, validity_status="success")),
                 (order, "unsolved", alternative(0, validity_status="infrastructure_error")),
                 (order, "unsolved", lambda record: record.update(validity_status="elaboration_failure")),
                 (order, "unsolved", lambda record: record.update(proof="by omega")),
                 (order, "unsolved", skip_aesop),
                 (budget, "solved", alternative(1, wall_seconds_limit=60)),
                 (budget, "solved", alternative(0, elapsed_seconds=-1)),
                 (budget, "solved", lambda record: record["validation"]["resources"].update(wall_seconds_limit=60)),
                 (budget, "spent", alternative(0, elapsed_seconds=60))]
        for index, (message, task_id, change) in enumerate(cases):
            with self.subTest(message=message, index=index):
                directory = self.root / f"tampered{index}"
                shutil.copytree(self.root / "baseline", directory)
                path, = [path for path in directory.glob("*.json") if read_json(path).get("task_id") == task_id]
                record = copy.deepcopy(read_json(path))
                change(record)
                path.unlink()
                write_json(path, record)
                code, log = analyze(self.root, directory.name, f"report{index}")
                self.assertEqual(code, 2)
                self.assertIn(message, log)
                self.assertFalse((self.root / f"report{index}").exists())
        for index, tactics in enumerate((None, TACTICS[::-1])):
            with self.subTest(policy_tactics=tactics):
                directory = self.root / f"policy{index}"
                shutil.copytree(self.root / "baseline", directory)
                policy = read_json(directory / "policy.json")
                policy.pop("portfolio_tactics")
                (directory / "policy.json").unlink()
                write_json(directory / "policy.json", policy if tactics is None else {**policy, "portfolio_tactics": tactics})
                code, log = analyze(self.root, directory.name, f"report-policy{index}")
                self.assertEqual(code, 2)
                self.assertIn("does not record the frozen tactic_portfolio_tactics", log)

    def test_legacy_single_proof_baselines_still_load_labelled_as_such(self):
        root = self.root / "legacy"
        root.mkdir()
        frozen_run(root, LEGACY_CONFIG)
        engine = FakeEngine()
        result, log = quiet(baseline_argv(root), engine)
        self.assertEqual((result["new_checks"], [proof for _, proof, _ in engine.checks]), (3, [LEGACY] * 3), log)
        self.assertIn("Legacy tactic_portfolio string: checking it as one proof per task", log)
        found = records(root / "baseline")
        self.assertEqual({name: (record["proof"], record["validity_status"], record["validation"]["resources"]["wall_seconds_limit"],
                                 "portfolio_alternatives" in record) for name, record in found.items()},
                         {"solved": (LEGACY, "success", 60, False), "unsolved": (LEGACY, "parse_failure", 60, False),
                          "spent": (LEGACY, "elaboration_failure", 60, False)})
        self.assertNotIn("portfolio_tactics", read_json(root / "baseline/policy.json"))
        result, log = analyze(root, "baseline")
        self.assertIsInstance(result, dict, log)
        section = summary(root)
        self.assertEqual((section["status"], section["portfolio_semantics"], section["portfolio"], section["solved_by_tactic"]),
                         ("complete", "legacy_single_proof", LEGACY, None))
        self.assertNotIn("portfolio_tactics", section["policy"])
        self.assertEqual({name: (row["outcome"], row["solved_by"], row["alternatives"]) for name, row in section["per_task"].items()},
                         {"solved": ("solved", None, None), "unsolved": ("unsolved", None, None), "spent": ("unsolved", None, None)})
        self.assertIn("Legacy portfolio", section["interpretation"][-1])
        page = (root / "report/report.html").read_text(encoding="utf-8")
        self.assertIn(f"Legacy portfolio: <code>{LEGACY}</code>, checked as one proof per task (validation budget 60 s)", page)
        # The same legacy records cannot pass as a baseline of the listed alternatives, nor the reverse.
        code, log = analyze(self.root, root / "baseline", "report-legacy-as-separate")
        self.assertEqual(code, 2)
        self.assertIn("configuration differs from the run", log)

    def test_a_run_frozen_with_both_portfolios_matches_no_baseline(self):
        root = self.root / "both"
        root.mkdir()
        # freeze now refuses such a configuration (tests/test_round5_baseline.py); older code froze it.
        with patch("lean_compression.store.frozen_portfolio"):
            manifest = frozen_run(root, {**DEVELOPMENT, "tactic_portfolio": LEGACY})
        write_json(root / "baseline/policy.json", {
            "kind": "baseline", "engine_hash": file_hash(ENGINE), "environment_hash": "fixture",
            "config_hash": manifest["config_hash"], "portfolio_validation_wall_seconds": 60, "portfolio_tactics": TACTICS})
        code, log = analyze(root, "baseline")
        self.assertEqual(code, 2)
        self.assertIn("names both tactic_portfolio_tactics and the legacy tactic_portfolio", log)


class LeanPortfolioTests(unittest.TestCase):
    """The real `baseline` command with configs/development.json settings under the pinned local runtime."""

    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Needs the macOS development sandbox")
        cls.manifest = manifest_path()
        pinned_runtime(cls.manifest)

    def baseline(self, root, tasks, config, name):
        (root / f"{name}.jsonl").write_text("".join(json.dumps(task, ensure_ascii=False) + "\n" for task in tasks),
                                            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["baseline", "--tasks", str(root / f"{name}.jsonl"), "--config", str(config),
                      "--environment", str(self.manifest), "--engine", str(ENGINE), "--output", str(root / name)])
        return records(root / name)

    def test_alternatives_after_an_unknown_aesop_still_run(self):
        """Fixture: Aesop is not imported and omega proves the goal; the 2026-10-12 tasks lacked Aesop the same way."""
        task = {**FIXTURE, "task_id": "no_aesop", "family_id": "no_aesop", "domain": "number_theory", "target_name": "no_aesop"}
        real = {task["task_id"]: task for task in map(json.loads, (PROJECT / "data/public/tasks.jsonl").read_text(
            encoding="utf-8").splitlines())}["lib_number_theory_pilot_nat_basic_02"]
        self.assertNotIn("Aesop", " ".join(real["imports"]))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "legacy.json", LEGACY_CONFIG)
            # Five checks of a Mathlib task share the budget: a generous one keeps this test independent of machine load.
            write_json(root / "separate.json", {**DEVELOPMENT, "tactic_portfolio_total_wall_seconds": 300})
            legacy = self.baseline(root, [task], root / "legacy.json", "legacy")["no_aesop"]
            found = self.baseline(root, [task, real], root / "separate.json", "separate")
        # Before: one proof, rejected at its first unknown tactic before any alternative ran.
        self.assertEqual((legacy["validity_status"], legacy["proof"]), ("parse_failure", LEGACY))
        self.assertIn("unknown tactic", legacy["validation"]["failure_reason"])
        solved, unsolved = found["no_aesop"], found["lib_number_theory_pilot_nat_basic_02"]
        self.assertEqual((solved["validity_status"], solved["proof"], solved["metric_status"]), ("success", "by omega", "available"))
        self.assertTrue(solved["validation"]["kernel_replay"])
        self.assertEqual([(attempt["tactic"], attempt["validity_status"]) for attempt in solved["portfolio_alternatives"]],
                         [("rfl", "elaboration_failure"), ("assumption", "elaboration_failure"),
                          ("(simp_all; done)", "elaboration_failure"), ("omega", "success")])
        # The real task: every alternative ran under its own imports; only aesop is unknown there.
        attempts = unsolved["portfolio_alternatives"]
        self.assertEqual([(attempt["tactic"], attempt["validity_status"]) for attempt in attempts],
                         [*((tactic, "elaboration_failure") for tactic in TACTICS[:4]), ("aesop", "parse_failure")])
        self.assertIn("unknown tactic", attempts[-1]["failure_reason"])
        self.assertEqual((unsolved["validity_status"], unsolved["proof"]), ("parse_failure", "by aesop"))
        for record in found.values():
            remaining = 300
            for attempt in record["portfolio_alternatives"]:
                self.assertEqual(attempt["wall_seconds_limit"], remaining)
                self.assertGreater(attempt["elapsed_seconds"], 0)
                remaining -= attempt["elapsed_seconds"]
            self.assertEqual(record["validation"]["resources"]["wall_seconds_limit"], record["portfolio_alternatives"][-1]["wall_seconds_limit"])


if __name__ == "__main__":
    unittest.main()
