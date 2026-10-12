"""Real Lean stress case: alias-induced metric inflation is not better reasoning.

Both candidates prove exactly True, use one source token, and see the same
trusted alias library.  Only dependency expansion changes.  This intentionally
demonstrates why a larger T0 must not be interpreted as improved reasoning.
"""
from fractions import Fraction
import unittest

import test_lean_integration as lean_fixtures
from lean_compression.metrics import score_graph


CONTEXT = """theorem alias1 : True := True.intro
theorem alias2 : True := alias1
theorem alias3 : True := alias2
theorem alias4 : True := alias3
"""


class LeanMetricStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the existing pinned-runtime setup and evaluator helper without
        # inheriting or re-exporting its TestCase (which would duplicate tests).
        fixture = lean_fixtures.LeanIntegrationTests
        fixture.setUpClass()
        cls.lean = fixture.lean
        cls.process_env = fixture.process_env

    def test_alias_chain_inflates_expansion_without_source_savings(self):
        observations = {}
        for proof in ("True.intro", "alias4"):
            with self.subTest(proof=proof):
                checked = lean_fixtures.LeanIntegrationTests.evaluate(
                    self, proof, imports=["Init"], context=CONTEXT,
                    target_name="alias_stress_target", binders="",
                    proposition="True", permitted_axioms=[], mode="extract")
                self.assertEqual(checked["validity_status"], "success", checked)
                self.assertIs(checked["kernel_replay"], True)
                self.assertEqual(checked["axioms"], [])
                self.assertEqual(checked["metric_status"], "available", checked)
                tokens = checked["source_tokens"]
                metrics = score_graph(checked["nodes"], checked["root"],
                                      tokens["W_S"], tokens["W_B"])
                self.assertEqual(metrics["metric_status"], "available", metrics)
                self.assertEqual(tokens["W_B"], 1)
                source = tokens["source"].encode()
                body_tokens = [source[s["start"]:s["end"]].decode()
                               for s in tokens["body_spans"]]
                self.assertEqual(body_tokens, [proof])
                observations[proof] = metrics
        # Keep the measured values available to a targeted test runner without
        # printing a noisy report during the full suite.
        self.observed_metrics = observations
        direct, aliases = observations["True.intro"], observations["alias4"]
        self.assertEqual(direct["W_S"], aliases["W_S"])
        self.assertEqual(direct["W_B"], aliases["W_B"])
        self.assertEqual(direct["U_S_decimal"], aliases["U_S_decimal"])
        self.assertGreater(aliases["D"], direct["D"])
        self.assertGreater(int(aliases["U_B_decimal"]), int(direct["U_B_decimal"]))
        self.assertGreater(int(aliases["U_decimal"]), int(direct["U_decimal"]))
        ratios = {name: Fraction(int(m["T0"]["numerator_decimal"]),
                                 int(m["T0"]["denominator_decimal"]))
                  for name, m in observations.items()}
        self.assertGreater(
            ratios["alias4"], ratios["True.intro"],
            "Redundant aliases should expose numerator inflation without source savings; "
            "this is metric inflation, not evidence of better reasoning.")


if __name__ == "__main__":
    unittest.main()
