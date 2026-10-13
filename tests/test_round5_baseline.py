"""One tactic-portfolio check at freeze, baseline and analyze --baseline; the shared budget; each alternative's evidence.

Review 3 found that freeze accepted portfolio configurations that baseline and analyze --baseline always refuse, so a
frozen run could never get its baseline; that a null tactic_portfolio_tactics beside the legacy string was read as that
string; that the notes said a timed-out alternative does not stop the next, although one that reaches its wall-clock
limit spends the shared budget; and that records kept five summary fields of each alternative that did not decide.
The stubbed classes replace the engine process. RunnerTests runs the real runner under the macOS development sandbox
with a stand-in for Lean that is killed, reports a kernel heartbeat timeout or sleeps; it skips elsewhere.
"""
import contextlib
import io
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import Mock, patch

from lean_compression import cli
from lean_compression.baseline import NOTES, SEPARATE, STDERR_TAIL, load_baseline
from lean_compression.integrity import frozen_portfolio, portfolio_tactics
from lean_compression.io import digest, file_hash, read_json, write_json
from lean_compression.store import create_run, record_generation

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
DEVELOPMENT = read_json(PROJECT / "configs/development.json")
TACTICS = ["rfl", "assumption", "(simp_all; done)", "omega", "aesop"]
LEGACY = "by first | rfl | assumption | (simp_all; done) | omega | aesop"
LEGACY_CONFIG = {**{key: value for key, value in DEVELOPMENT.items() if key != "tactic_portfolio_tactics"},
                 "tactic_portfolio": LEGACY}
ENVIRONMENT = {"environment_hash": "fixture", "hash_mode": "development_metadata_only",
               "lean_toolchain": DEVELOPMENT["environment"]["lean_toolchain"],
               "mathlib_commit": DEVELOPMENT["environment"]["mathlib_commit"]}
FIXTURE = {"split": "pilot", "origin": "fresh", "stratum": "provisional_easy", "imports": ["Init"], "context": "",
           "premise_context": "", "forbidden_declarations": []}
TASKS = [{**FIXTURE, "task_id": name, "family_id": name, "domain": "sets_logic", "target_name": name, "binders": binders,
          "proposition": goal} for name, binders, goal in (("killed", "(p : Prop) (hp : p)", "p"),
                                                           ("slow", "(p q : Prop) (h : p ∨ q)", "p"),
                                                           ("heartbeats", "(n : Nat)", "n + 0 = n"))]
NO_BUDGET = "The run's frozen configuration has no tactic_portfolio_total_wall_seconds; no baseline can match it"


def without(config, key):
    return {name: value for name, value in config.items() if name != key}


# No baseline can be checked under these, so no run may be frozen with them.
UNUSABLE = {"both keys": {**DEVELOPMENT, "tactic_portfolio": LEGACY},
            "list beside a null string": {**DEVELOPMENT, "tactic_portfolio": None},
            "null list beside the string": {**LEGACY_CONFIG, "tactic_portfolio_tactics": None},
            "null list": {**DEVELOPMENT, "tactic_portfolio_tactics": None},
            "string as list": {**DEVELOPMENT, "tactic_portfolio_tactics": "omega"},
            "repeated tactic": {**DEVELOPMENT, "tactic_portfolio_tactics": ["rfl", "rfl"]},
            "no portfolio": without(DEVELOPMENT, "tactic_portfolio_tactics"),
            "no budget": without(DEVELOPMENT, "tactic_portfolio_total_wall_seconds"),
            "zero budget": {**DEVELOPMENT, "tactic_portfolio_total_wall_seconds": 0},
            "boolean budget": {**LEGACY_CONFIG, "tactic_portfolio_total_wall_seconds": True}}


def quiet(argv, engine=None):
    """cli.main with the engine process played by engine, if given: (exit code, parsed stdout or None, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        for context in (contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
                        patch("lean_compression.cli.load_environment", read_json),
                        *([patch("lean_compression.runner.execute_engine", engine)] if engine is not None else [])):
            stack.enter_context(context)
        try:
            cli.main([str(part) for part in argv])
        except SystemExit as stopped:
            return stopped.code, None, err.getvalue()
    return 0, json.loads(out.getvalue()), err.getvalue()


def freeze_argv(root, config, run):
    return ["freeze", "--development", "--tasks", root / "tasks.json", "--environment", root / "environment.json",
            "--config", config, "--split", "pilot", "--model", "fixture", "--engine", ENGINE, "--run", run]


def baseline_argv(root, config, output):
    return ["baseline", "--tasks", root / "tasks.json", "--environment", root / "environment.json",
            "--config", config, "--engine", ENGINE, "--output", output]


def records(directory):
    return {record["task_id"]: record for path in directory.glob("*.json") if path.name != "policy.json"
            for record in [read_json(path)]}


class PortfolioCheckTests(unittest.TestCase):
    """freeze refuses what baseline and analyze --baseline refuse, with the same message."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        write_json(self.root / "tasks.json", TASKS[:1])
        write_json(self.root / "environment.json", ENVIRONMENT)

    def test_freeze_refuses_what_baseline_and_analyze_refuse(self):
        for index, (name, config) in enumerate(UNUSABLE.items()):
            with self.subTest(name):
                with self.assertRaises(ValueError) as refused:
                    frozen_portfolio(config)
                message, path, engine = str(refused.exception), self.root / f"config{index}.json", Mock()
                write_json(path, config)
                code, _, log = quiet(freeze_argv(self.root, path, self.root / f"run{index}"), engine)
                self.assertEqual((code, log.strip()), (2, message))
                self.assertFalse((self.root / f"run{index}").exists())
                with self.assertRaises(ValueError) as library:
                    create_run(self.root / f"library{index}", TASKS[:1], config, ENVIRONMENT, split="pilot",
                               model="fixture", development=True)
                self.assertEqual(str(library.exception), message)
                # Before any check or output; no legacy notice either.
                code, _, log = quiet(baseline_argv(self.root, path, self.root / f"baseline{index}"), engine)
                self.assertEqual((code, log.strip()), (2, message))
                self.assertFalse((self.root / f"baseline{index}").exists())
                engine.assert_not_called()
                # analyze --baseline of a run that older code froze with it, whatever the directory holds.
                directory = self.root / f"analyze{index}"
                write_json(directory / "policy.json", {"kind": "baseline", "engine_hash": file_hash(ENGINE),
                                                       "environment_hash": "fixture", "config_hash": digest(config)})
                manifest = {"environment": ENVIRONMENT, "config": config, "config_hash": digest(config), "tasks": TASKS[:1]}
                with self.assertRaises(ValueError) as analyzed:
                    load_baseline(directory, manifest, [], directory / "no-version.json")
                self.assertEqual(str(analyzed.exception),
                                 NO_BUDGET if "tactic_portfolio_total_wall_seconds" not in config else message)

    def test_the_list_or_the_legacy_string_still_freezes(self):
        for name, config in (("list", DEVELOPMENT), ("legacy", LEGACY_CONFIG)):
            with self.subTest(name):
                write_json(self.root / f"{name}.json", config)
                code, result, log = quiet(freeze_argv(self.root, self.root / f"{name}.json", self.root / name))
                self.assertEqual(code, 0, log)
                self.assertEqual(read_json(self.root / name / "manifest.json")["config"], config)
                self.assertEqual(frozen_portfolio(config), None if name == "legacy" else TACTICS)

    def test_a_present_tactic_list_is_never_read_as_absent(self):
        # Review 3: a null list beside the legacy string was read as that string; the reverse was already refused.
        for config in ({**LEGACY_CONFIG, "tactic_portfolio_tactics": None}, {**DEVELOPMENT, "tactic_portfolio": None},
                       {**LEGACY_CONFIG, "tactic_portfolio_tactics": TACTICS}):
            with self.subTest(config=config.get("tactic_portfolio_tactics")), self.assertRaisesRegex(ValueError, "names both"):
                portfolio_tactics(config)
        with self.assertRaisesRegex(ValueError, "tactic_portfolio_tactics must be a non-empty list"):
            portfolio_tactics({**DEVELOPMENT, "tactic_portfolio_tactics": None})
        self.assertEqual((portfolio_tactics(DEVELOPMENT), portfolio_tactics(LEGACY_CONFIG)), (TACTICS, None))


# The stubbed checks by (task, proof): status, elapsed seconds ("limit": as the runner reports a check it killed at its
# wall-clock limit, once all of it had elapsed) and the process evidence. Others are rejected in a second.
KILLED = {"error": "Lean did not produce its result record", "process_returncode": -9, "stderr": "x" * 10000 + "Killed: 9"}
CHECKS = {("killed", "by rfl"): ("verification_resource_limit", 4.0, KILLED),
          ("killed", "by assumption"): ("success", 1.0, {}),
          ("slow", "by assumption"): ("verification_timeout", "limit", {}),
          ("heartbeats", "by rfl"): ("verification_timeout", 2.0, {"failure_reason": "kernel deterministic timeout"}),
          ("heartbeats", "by omega"): ("success", 1.0, {})}


def stub(checks):
    def execute(request, environment, engine, policy, *, mode, wall_seconds):
        if mode == "extract":
            return {"validity_status": "success", "metric_status": "unavailable", "metric_error": "stubbed extraction"}
        checks.append((request["target_name"], request["proof"], wall_seconds))
        status, elapsed, evidence = CHECKS.get((request["target_name"], request["proof"]), ("elaboration_failure", 1.0, {}))
        result = {"validity_status": status, "metric_status": "not_requested" if status == "success" else "unavailable",
                  "process_returncode": 0, "stdout": "", "stderr": "", "resources": {
                      "wall_seconds_limit": wall_seconds, "peak_memory_mib": 100.0,
                      "elapsed_seconds": wall_seconds + 0.25 if elapsed == "limit" else elapsed}, **evidence}
        if status == "success":
            result.update(kernel_replay=True, candidate_declarations=[request["target_name"]])
        return result
    return execute


class StubbedPortfolioTests(unittest.TestCase):
    """The real baseline and analyze commands, with the engine process stubbed, on a development run of TASKS."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        config = {**DEVELOPMENT, "samples_per_goal_condition": 1,
                  "analysis": {**DEVELOPMENT["analysis"], "bootstrap_replicates": 20}}
        manifest = create_run(self.root / "run", TASKS, config, ENVIRONMENT, split="pilot", model="fixture",
                              development=True)
        for slot in manifest["slots"]:
            generation = record_generation(self.root / "run", slot, "by assumption", generation_status="completed")
            write_json(self.root / "run/evaluations/v1" / f"{slot['attempt_id']}.json", {
                "attempt_id": slot["attempt_id"], "engine_hash": file_hash(ENGINE), "validity_status": "success",
                "generation": generation, "generation_hash": digest(generation), "validation": {"validity_status": "success"},
                "metric_status": "available", "metrics": {"metric_status": "available", "W_S": 1, "W_B": 3, "D": 2,
                                                          "U_S_decimal": "1", "U_B_decimal": "7"}})
        write_json(self.root / "tasks.json", TASKS)
        write_json(self.root / "environment.json", ENVIRONMENT)
        write_json(self.root / "config.json", config)
        self.checks = []
        code, result, log = quiet(baseline_argv(self.root, self.root / "config.json", self.root / "baseline"),
                                  stub(self.checks))
        self.assertEqual((code, result["new_checks"], result["checker_incidents"]), (0, 3, 0), log)
        self.records = records(self.root / "baseline")

    def test_only_an_alternative_that_reaches_its_wall_clock_limit_ends_the_portfolio(self):
        checks = {}
        for target, proof, limit in self.checks:
            checks.setdefault(target, []).append((proof, limit))
        # slow: assumption ran to its limit, so the budget is spent; heartbeats: rfl's kernel replay ran out of
        # heartbeats after 2 s, leaving the rest to the next.
        self.assertEqual(checks, {"killed": [("by rfl", 60), ("by assumption", 56.0)],
                                  "slow": [("by rfl", 60), ("by assumption", 59.0)],
                                  "heartbeats": [("by rfl", 60), ("by assumption", 58.0), ("by (simp_all; done)", 57.0),
                                                 ("by omega", 56.0)]})
        self.assertEqual({name: (record["validity_status"], record["proof"]) for name, record in self.records.items()},
                         {"killed": ("success", "by assumption"), "slow": ("verification_timeout", "by assumption"),
                          "heartbeats": ("success", "by omega")})
        code, _, log = quiet(["analyze", "--run", self.root / "run", "--version", "v1", "--output", self.root / "report",
                              "--baseline", self.root / "baseline"])
        self.assertEqual(code, 0, log)
        section = read_json(self.root / "report/analysis.json")["automation_baseline"]
        self.assertEqual((section["status"], {name: row["solved_by"] for name, row in section["per_task"].items()}),
                         ("complete", {"killed": "assumption", "slow": None, "heartbeats": "omega"}))
        # The note in analysis.json and report.html says what the run did.
        self.assertEqual(section["interpretation"][-1], NOTES[SEPARATE])
        self.assertIn("one that reaches its wall-clock limit has spent it, so no later alternative runs", NOTES[SEPARATE])
        self.assertNotIn("timed-out alternative does not stop the next", NOTES[SEPARATE])
        self.assertIn(NOTES[SEPARATE], (self.root / "report/report.html").read_text(encoding="utf-8"))

    def test_each_attempted_alternative_keeps_the_evidence_of_its_check(self):
        killed, deciding = self.records["killed"]["portfolio_alternatives"]
        self.assertEqual(killed, {"tactic": "rfl", "validity_status": "verification_resource_limit", "elapsed_seconds": 4.0,
                                  "wall_seconds_limit": 60, "failure_reason": None,
                                  "error": "Lean did not produce its result record", "process_returncode": -9,
                                  "stderr_tail": KILLED["stderr"][-STDERR_TAIL:],
                                  "resources": {"wall_seconds_limit": 60, "peak_memory_mib": 100.0, "elapsed_seconds": 4.0}})
        self.assertTrue(killed["stderr_tail"].endswith("Killed: 9") and len(killed["stderr_tail"]) == STDERR_TAIL)
        # The deciding check's evidence is also its record's validation.
        self.assertEqual({key: deciding[key] for key in ("tactic", "process_returncode", "error", "stderr_tail", "resources")},
                         {"tactic": "assumption", "process_returncode": 0, "error": None, "stderr_tail": "",
                          "resources": self.records["killed"]["validation"]["resources"]})
        timeout = self.records["heartbeats"]["portfolio_alternatives"][0]
        self.assertEqual((timeout["validity_status"], timeout["failure_reason"], timeout["elapsed_seconds"]),
                         ("verification_timeout", "kernel deterministic timeout", 2.0))


STAND_IN = """#!/bin/sh
# Stands in for `lean --run <engine> <request> <result>`, by the proof the request names.
case "$(cat "$3")" in
  *'"by rfl"'*) echo "stand-in lean: killed" >&2; kill -9 $$ ;;
  *'"by assumption"'*) printf '%s' '{"validity_status": "verification_timeout", "metric_status": "unavailable",
    "failure_reason": "kernel deterministic timeout replaying t"}' > "$4" ;;
  *'"by (simp_all; done)"'*) exec sleep 60 ;;
  *) printf '%s' '{"validity_status": "elaboration_failure", "metric_status": "unavailable"}' > "$4" ;;
esac
"""


class RunnerTests(unittest.TestCase):
    """The real runner and sandbox: a check stopped at its wall-clock limit has used all of what was left."""

    def test_a_killed_check_keeps_its_evidence_and_a_wall_clock_timeout_spends_the_budget(self):
        if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").exists():
            self.skipTest("Needs the macOS development sandbox")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lean = root / "toolchain/bin/lean"
            lean.parent.mkdir(parents=True)
            lean.write_text(STAND_IN, encoding="utf-8")
            lean.chmod(0o755)
            write_json(root / "tasks.json", TASKS[:1])
            write_json(root / "environment.json", {**ENVIRONMENT, "lean_binary": str(lean),
                                                   "toolchain_root": str(root / "toolchain"), "lean_search_paths": []})
            write_json(root / "config.json", {**DEVELOPMENT, "tactic_portfolio_total_wall_seconds": 4})
            code, result, log = quiet(baseline_argv(root, root / "config.json", root / "baseline"))
            self.assertEqual((code, result["new_checks"]), (0, 1), log)
            record = records(root / "baseline")["killed"]
        attempts = record["portfolio_alternatives"]
        # The killed check and the heartbeat timeout left budget; the sleeping check ran to its limit, so omega and
        # aesop never started.
        self.assertEqual([(attempt["tactic"], attempt["validity_status"]) for attempt in attempts],
                         [("rfl", "verification_resource_limit"), ("assumption", "verification_timeout"),
                          ("(simp_all; done)", "verification_timeout")])
        killed, last = attempts[0], attempts[-1]
        self.assertEqual((killed["process_returncode"], killed["error"], killed["stderr_tail"]),
                         (-9, "Lean did not produce its result record", "stand-in lean: killed\n"))
        self.assertEqual((killed["resources"]["elapsed_seconds"], killed["resources"]["isolation"]),
                         (killed["elapsed_seconds"], "macos_sandbox"))
        self.assertGreater(last["elapsed_seconds"], last["wall_seconds_limit"])
        self.assertEqual((record["validity_status"], record["proof"]), ("verification_timeout", "by (simp_all; done)"))


if __name__ == "__main__":
    unittest.main()
