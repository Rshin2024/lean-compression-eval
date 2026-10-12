"""Lean heartbeats: engine readings, runner resource evidence and the analysis summary.

The engine fixtures run the pinned Lean and skip only when that runtime is unavailable. Lean's
frontend elaborates in a task on a pooled thread and the engine kernel-checks each replayed
declaration in a fresh task, so a counter read on the engine's own thread misses both; the
heavier proofs below fail such a reading. They also fail an engine that swaps the two phases'
readings, and the matcher statement one that meters the trusted statement's auxiliaries.
"""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lean_compression.analysis import _resource_summary
from lean_compression.runner import evaluate_proof, execute_engine

try:
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_heartbeats, run from the repository root
    from tests.lean_runtime import pinned_runtime

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "lean" / "Evaluator.lean"
REAL_POPEN = subprocess.Popen
AXIOMS = ["propext", "Classical.choice", "Quot.sound"]
REQUEST = {"imports": ["Init"], "target_name": "evaluation_target", "binders": "(P : Prop) (h : P)",
           "proposition": "P ∧ P", "proof": "⟨h, h⟩", "context": "", "forbidden_declarations": [],
           "permitted_axioms": AXIOMS, "max_heartbeats": 2000000}
OMEGA = {"binders": "(x y z : Nat) (h1 : x + y = 10) (h2 : x = y + 2) (h3 : z < x)", "proposition": "z < 7",
         "proof": "by omega"}
KERNEL = {"binders": "", "proposition": "(List.replicate 3000 1).length = 3000", "proof": "by decide +kernel"}
MATCHER = {"binders": "(n : Nat)", "proposition": "(match n with | 0 => 0 | k + 1 => k) ≤ n", "proof": "by cases n <;> simp"}


def units(raw):
    return raw / 1000


class EngineHeartbeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment, cls.lean = pinned_runtime()
        cls.process_env = {**os.environ, "LEAN_PATH": os.pathsep.join(cls.environment["lean_search_paths"]),
                           "PATH": str(Path(cls.lean).parent) + os.pathsep + os.environ.get("PATH", "")}

    def evaluate(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            request, result = Path(directory) / "request.json", Path(directory) / "result.json"
            request.write_text(json.dumps({**REQUEST, "mode": "validate", **changes}))
            completed = subprocess.run([self.lean, "--run", str(ENGINE), str(request), str(result)],
                                       env=self.process_env, text=True, capture_output=True, timeout=120)
            self.assertTrue(result.exists(), completed.stdout + completed.stderr)
            return json.loads(result.read_text())

    def test_heavier_proofs_record_more_heartbeats(self):
        trivial, omega, kernel = (self.evaluate(**case) for case in ({}, OMEGA, KERNEL))
        for result in (trivial, omega, kernel):
            self.assertEqual(result["validity_status"], "success", result)
            raw, scaled = result["heartbeats"]["raw"], result["heartbeats"]["max_heartbeats_units"]
            self.assertEqual(raw["total"], raw["elaboration"] + raw["kernel_replay"])
            for phase in ("elaboration", "kernel_replay", "total"):
                self.assertAlmostEqual(scaled[phase], units(raw[phase]))
        trivial, omega, kernel = (r["heartbeats"]["raw"] for r in (trivial, omega, kernel))
        # Deltas, not thread totals: a trivial proof needs few allocations in either phase.
        self.assertLess(trivial["total"], 100_000, trivial)
        # omega's search happens during elaboration, on the frontend's thread.
        self.assertGreater(omega["elaboration"], 10 * trivial["elaboration"], (omega, trivial))
        # Each phase has its own reading: elaborating a trivial proof costs far more than replaying
        # its term, and omega's search more than the replay of the proof it built.
        self.assertLess(10 * trivial["kernel_replay"], trivial["elaboration"], trivial)
        self.assertGreater(omega["elaboration"], omega["kernel_replay"], omega)
        # Both the elaborator's check and the replay of the decide lemma reduce a 3000-element list.
        self.assertGreater(kernel["elaboration"], 1_000_000, kernel)
        self.assertGreater(kernel["kernel_replay"], 1_000_000, kernel)
        self.assertGreater(kernel["kernel_replay"], 100 * trivial["kernel_replay"], (kernel, trivial))

    def test_kernel_replay_meters_only_the_candidates_declarations(self):
        # Both forks elaborate the statement's matcher. The trusted one is kernel-checked unmetered
        # before replay; the candidate's evaluation_target.match_1 is replayed and counted.
        result = self.evaluate(**MATCHER)
        self.assertEqual(result["validity_status"], "success", result)
        self.assertIn("evaluation_target.match_1", result["candidate_declarations"])
        # Deterministic on the pinned toolchain; metering the trusted matcher as well gives 3094.
        self.assertEqual(result["heartbeats"]["raw"]["kernel_replay"], 2646, result["heartbeats"])

    @unittest.skipUnless(platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists(),
                         "the development sandbox is macOS-only")
    def test_runner_records_engine_heartbeats_for_both_runs(self):
        config = {"validation": {"isolation": "macos_sandbox", "max_heartbeats": 2000000, "wall_seconds": 60,
                                 "permitted_axioms": AXIOMS},
                  "extraction": {"wall_seconds": 60}, "environment": {"boundary_policy": "expand_mathlib_and_evaluator_v1"}}
        task = {"task_id": "heartbeats", "imports": ["Init"], "target_name": "evaluation_target",
                "binders": KERNEL["binders"], "proposition": KERNEL["proposition"]}
        result = evaluate_proof(task, KERNEL["proof"], self.environment, ENGINE, config)
        self.assertEqual(result["metric_status"], "available", result)
        for stage in ("validation", "extraction"):
            resources = result[stage]["resources"]
            self.assertEqual(resources["heartbeats_used"], result[stage]["heartbeats"]["max_heartbeats_units"]["total"])
            self.assertGreater(resources["heartbeats_used"], 2000, resources)
            self.assertIsNone(resources["heartbeats_used_reason"])
        summary = _resource_summary([result])["measurements"]["validation_heartbeats"]
        self.assertEqual(summary["median"], result["validation"]["resources"]["heartbeats_used"])


class RunnerHeartbeatTests(unittest.TestCase):
    def execute(self, output):
        def popen(cmd, *args, **kwargs):
            # The engine's result is written where Lean would write it; a real process exits cleanly.
            Path(cmd[-1]).write_text(json.dumps(output))
            return REAL_POPEN([sys.executable, "-c", ""], stdout=kwargs["stdout"], stderr=kwargs["stderr"])
        environment = {"lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
                       "lean_search_paths": ["/opt/lean/lib/lean"]}
        with patch("platform.system", return_value="Darwin"), patch("subprocess.Popen", popen):
            return execute_engine(REQUEST, environment, ENGINE, {"isolation": "macos_sandbox"},
                                  mode="validate", wall_seconds=30)["resources"]

    def test_reported_total_is_recorded_in_max_heartbeats_units(self):
        raw = {"elaboration": 1252407, "kernel_replay": 1237821, "total": 2490228}
        resources = self.execute({"validity_status": "success", "heartbeats": {
            "raw": raw, "max_heartbeats_units": {phase: units(value) for phase, value in raw.items()}}})
        self.assertEqual(resources["heartbeats_used"], 2490.228)
        self.assertIsNone(resources["heartbeats_used_reason"])
        self.assertEqual(resources["heartbeats_limit"], 2000000)
        self.assertIn("1000 raw", resources["heartbeats_unit"])
        self.assertIn("kernel replay", resources["heartbeats_scope"])
        # A successful check can report more than max_heartbeats in either phase; the limit is no denominator.
        self.assertIn("own kernel checks", resources["heartbeats_scope"])
        self.assertIn("not bounded by or comparable with heartbeats_limit", resources["heartbeats_scope"])

    def test_unreported_heartbeats_keep_a_reason(self):
        # A failed check, and a success from an engine that predates heartbeat reporting.
        for output in ({"validity_status": "disallowed_axiom", "failure_reason": "sorryAx"},
                       {"validity_status": "success"}, {"validity_status": "success", "heartbeats": None}):
            with self.subTest(output=output):
                resources = self.execute(output)
                self.assertIsNone(resources["heartbeats_used"])
                self.assertEqual(resources["heartbeats_used_reason"], "not_reported_by_engine")


class HeartbeatSummaryTests(unittest.TestCase):
    def test_validation_heartbeats_are_summarized_beside_wall_time(self):
        def record(heartbeats, elapsed=2.0):
            return {"validation": {"resources": {"elapsed_seconds": elapsed, "heartbeats_used": heartbeats,
                                                 "heartbeats_limit": 2000000}},
                    "extraction": {"resources": {"heartbeats_used": 999.0}}}
        records = [record(4.903), record(195.615), record(None), record(True), {"validation_status": "pending"}]
        summary = _resource_summary(records)["measurements"]
        heartbeats = summary["validation_heartbeats"]
        self.assertEqual((heartbeats["observed_count"], heartbeats["missing_count"], heartbeats["invalid_count"]),
                         (2, 3, 1))
        self.assertEqual(heartbeats["unit"], "maxHeartbeats")
        self.assertAlmostEqual(heartbeats["median"], (4.903 + 195.615) / 2)
        self.assertAlmostEqual(heartbeats["sum"], 200.518)
        self.assertEqual(heartbeats["max"], 195.615)
        self.assertIn("successful checks", heartbeats["description"])
        self.assertIn("not bounded by or comparable with validation.max_heartbeats", heartbeats["description"])
        # Wall time keeps its own measurement.
        self.assertEqual(summary["validation_elapsed_seconds"]["observed_count"], 4)


if __name__ == "__main__":
    unittest.main()
