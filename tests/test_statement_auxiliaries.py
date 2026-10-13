"""Statement auxiliaries that the statement's own type never mentions.

Elaborating a statement can realize declarations that its elaborated type does not reference: the
equation and matcher lemmas of a nested `by simp [f]`, the rest of the `f.eq_1` family when the
statement names `f.eq_1`, or lemmas a nested `split` realizes about the statement's own matcher.
Engine b10e7972 reported only the declarations the replayed statement types reach, so the runner's
`helper_count` charged the others to every response. Lean classes skip only when the pinned local
runtime is unavailable; the runner class also needs the macOS development sandbox.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import platform
import unittest
from unittest.mock import patch

from lean_compression.io import read_json
from lean_compression.runner import evaluate_proof

try:
    import test_lean_integration as lean_fixtures
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_statement_auxiliaries, run from the repository root
    from tests import test_lean_integration as lean_fixtures
    from tests.lean_runtime import pinned_runtime

PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
F = "def f : Nat → Nat\n  | 0 => 1\n  | n + 1 => f n\n"
F_LEMMAS = {"f.eq_1", "f.eq_2", "f.eq_def"} | {f"_private.EvaluatorCandidate.0.f.match_1.{suffix}"
                                              for suffix in ("eq_1", "eq_2", "splitter")}
SIMP_INSIDE = "(⟨f 0, by simp [f]⟩ : {n : Nat // n = 1}).val = 1"
MATCH = "match n with | 0 => 0 | k + 1 => k"
LET_REC = "let rec g : Nat → Nat\n  | 0 => 0\n  | n + 1 => g n\nrfl"
# A dotted target name elaborates the candidate's statement in `Shadow`, the trusted one outside it.
SHADOWED = {"context": "def two : Nat := 2\nnamespace Shadow\ndef two : Nat := 2\nend Shadow\n",
            "target_name": "Shadow.evaluation_target", "binders": "",
            "proposition": "(⟨two, by simp [two]⟩ : {n : Nat // n = 2}).val = 2"}


class StatementAuxiliaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the pinned-runtime setup and evaluator helper without re-exporting its tests.
        fixture = lean_fixtures.LeanIntegrationTests
        fixture.setUpClass()
        cls.lean = fixture.lean
        cls.process_env = fixture.process_env

    def evaluate_all(self, requests):
        # Independent fresh processes; assertions stay in the test thread.
        with ThreadPoolExecutor(4) as pool:
            return list(pool.map(lambda request: lean_fixtures.LeanIntegrationTests.evaluate(
                self, mode="validate", **request), requests))

    def helpers(self, result):
        self.assertEqual(result["validity_status"], "success", result)
        auxiliaries = result["statement_auxiliaries"]
        self.assertEqual(auxiliaries, sorted(set(auxiliaries)))
        self.assertNotIn(result["root"], auxiliaries)
        return set(result["candidate_declarations"]) - set(auxiliaries) - {result["root"]}

    def test_declarations_the_statement_realizes_are_statement_auxiliaries(self):
        cases = [{"context": F, "binders": "", "proposition": SIMP_INSIDE, "proof": "rfl"},
                 {"context": F, "binders": "", "proposition": "∀ h : f 0 = 1, h = f.eq_1", "proof": "fun _ => rfl"},
                 {"imports": ["Mathlib.Data.Nat.Factorial.Basic"], "binders": "",
                  "proposition": "(⟨Nat.factorial 3, by simp [Nat.factorial]⟩ : {n : Nat // n = 6}).val = 6", "proof": "rfl"},
                 {"binders": "(n : Nat)", "proposition": f"(⟨{MATCH}, by split <;> omega⟩ : {{m : Nat // m ≤ n}}).val ≤ n",
                  "proof": "by split <;> simp"}]
        results = self.evaluate_all(cases)
        # Every declaration besides the root comes from the statement.
        for case, result in zip(cases, results):
            with self.subTest(proposition=case["proposition"]):
                self.assertEqual(self.helpers(result), set())
        # b10e7972 listed at most f.eq_1, the only one that a statement type mentions.
        for result in results[:2]:
            self.assertLessEqual(F_LEMMAS, set(result["candidate_declarations"]) & set(result["statement_auxiliaries"]))
        self.assertLessEqual({"Nat.factorial.eq_1", "Nat.factorial.eq_def"},
                             set(results[2]["candidate_declarations"]) & set(results[2]["statement_auxiliaries"]))
        # Each fork's matcher lemmas; only the candidate's are candidate declarations.
        for fork in ("__compression_trusted_signature", "evaluation_target"):
            self.assertIn(f"_private.EvaluatorCandidate.0.{fork}.match_1.eq_1", results[3]["statement_auxiliaries"])
        self.assertIn("_private.EvaluatorCandidate.0.evaluation_target.match_1.eq_1", results[3]["candidate_declarations"])

    def test_declarations_the_proof_realizes_stay_helpers(self):
        cases = [{"context": F, "binders": "", "proposition": "f 0 = 1", "proof": "by simp [f]"},
                 {"binders": "(n : Nat)", "proposition": f"({MATCH}) ≤ n", "proof": "by split <;> omega"},
                 # The statement realizes them first; the proof reuses them.
                 {"context": F, "binders": "", "proposition": SIMP_INSIDE, "proof": "by simp [f]"},
                 {"context": F, "binders": "", "proposition": SIMP_INSIDE, "proof": LET_REC},
                 {**SHADOWED, "proof": "rfl"}, {**SHADOWED, "proof": "by\n  have _h := _root_.two.eq_1\n  rfl"}]
        unused, matcher, shared, mixed, trusted, dotted = self.evaluate_all(cases)
        self.assertEqual(unused["statement_auxiliaries"], [])
        self.assertEqual(self.helpers(unused), F_LEMMAS)
        self.assertEqual(matcher["statement_auxiliaries"], ["__compression_trusted_signature.match_1", "evaluation_target.match_1"])
        self.assertIn("_private.EvaluatorCandidate.0.evaluation_target.match_1.splitter", self.helpers(matcher))
        self.assertEqual(self.helpers(shared), set())
        helpers = self.helpers(mixed)
        self.assertTrue(helpers and all(name.startswith("evaluation_target.g") for name in helpers), helpers)
        # Only the trusted statement realizes `two.eq_1`, so it is listed until the proof realizes it too.
        self.assertEqual((self.helpers(trusted), trusted["statement_auxiliaries"]), (set(), ["Shadow.two.eq_1", "two.eq_1"]))
        self.assertEqual((self.helpers(dotted), dotted["statement_auxiliaries"]), ({"two.eq_1"}, ["Shadow.two.eq_1"]))


class HelperCountTests(unittest.TestCase):
    """The runner's supporting diagnostic, end to end under the macOS development sandbox."""

    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Needs the macOS development sandbox")
        cls.environment, _ = pinned_runtime()

    def test_statement_realizations_are_not_proof_helpers(self):
        config = read_json(PROJECT / "configs/development.json")
        task = {"task_id": "statement_auxiliaries", "imports": ["Init"], "target_name": "evaluation_target",
                "binders": "", "context": F, "forbidden_declarations": []}
        cases = [(SIMP_INSIDE, "rfl", 0), ("f 0 = 1", "rfl", 0), ("f 0 = 1", "by simp [f]", 6)]
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda case: evaluate_proof({**task, "proposition": case[0]}, case[1],
                                                                self.environment, ENGINE, config), cases))
        for (proposition, proof, count), result in zip(cases, results):
            with self.subTest(proposition=proposition, proof=proof):
                self.assertEqual(result["validity_status"], "success", result.get("validation", {}).get("failure_reason"))
                diagnostics = result["supporting_diagnostics"]
                self.assertEqual(diagnostics["helper_count"], count)
                self.assertIn("except statement auxiliaries", diagnostics["helper_count_scope"])


class UnknownStatementAuxiliariesTests(unittest.TestCase):
    """The engine reports null when the candidate's header does not elaborate alone; no Lean runs."""

    def test_unknown_statement_auxiliaries_stay_in_the_count(self):
        validation = {"validity_status": "success", "metric_status": "not_requested", "kernel_replay": True,
                      "candidate_declarations": ["f.eq_1", "evaluation_target"], "statement_auxiliaries": None,
                      "diagnostics": {}}
        environment = {"environment_hash": "fixture", "lean_binary": "/opt/lean/bin/lean", "toolchain_root": "/opt/lean",
                       "lean_search_paths": ["/opt/lean/lib/lean"]}
        task = {"task_id": "statement_auxiliaries", "target_name": "evaluation_target", "binders": "",
                "proposition": "f 0 = 1", "imports": ["Init"], "context": F, "forbidden_declarations": []}
        with patch("lean_compression.runner.execute_engine",
                   side_effect=[validation, {"validity_status": "extractor_timeout", "metric_status": "unavailable"}]):
            result = evaluate_proof(task, "rfl", environment, ENGINE, read_json(PROJECT / "configs/development.json"))
        self.assertEqual(result["supporting_diagnostics"]["helper_count"], 1)
        self.assertIn("including any statement auxiliaries", result["supporting_diagnostics"]["helper_count_scope"])


if __name__ == "__main__":
    unittest.main()
