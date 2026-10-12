"""Real Lean regressions for evaluator defects fixed after engine 83da0074.

These fixtures failed on that engine: kernel replay budgets, statement matchers,
doc-comment accounting, malformed requests, and validation or extraction costs
that grew faster than the proof. The restricted-syntax cases extend coverage beyond
run_tac. Test-only replay-limit overrides pin per-declaration replay and its limit
statuses. Skip only when the pinned local runtime is unavailable.
"""
from concurrent.futures import ThreadPoolExecutor
import time
import unittest

import test_lean_integration as lean_fixtures

MATCH_BINDERS = "(n : Nat)"
MATCH_PROPOSITION = "(match n with | 0 => 0 | k + 1 => k) ≤ n"
# Each conjunct's `decide +kernel` lemma needs about 1.2 million kernel heartbeats to replay.
TWO_LEMMAS = "(List.replicate 14000 1).length = 14000 ∧ (List.replicate 14001 1).length = 14001"


def literal_proof(last_exponent):
    power = f"(((2:Nat)^256)^256)^{last_exponent}"
    return f"by\n  have _big : {power} = {power} + 0 := by simp only [Nat.reducePow, Nat.add_zero]\n  trivial"


class LeanEngineFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the pinned-runtime setup and evaluator helper without re-exporting its tests.
        fixture = lean_fixtures.LeanIntegrationTests
        fixture.setUpClass()
        cls.lean = fixture.lean
        cls.process_env = fixture.process_env

    def evaluate(self, proof="by exact ⟨h, h⟩", **changes):
        return lean_fixtures.LeanIntegrationTests.evaluate(self, proof, **changes)

    def evaluate_all(self, requests):
        # Independent fresh processes; assertions stay in the test thread.
        with ThreadPoolExecutor(4) as pool:
            return list(pool.map(lambda request: self.evaluate(**request), requests))

    def body_tokens(self, result):
        source = result["source_tokens"]["source"].encode()
        return [source[s["start"]:s["end"]].decode() for s in result["source_tokens"]["body_spans"]]

    def root(self, result):
        return next(x for x in result["nodes"] if x["name"] == result["root"])

    def test_restricted_syntax_denylist_beyond_run_tac(self):
        cases = [("set_option", "by set_option maxHeartbeats 0 in exact ⟨h, h⟩"),
                 ("set_option", "set_option maxHeartbeats 0 in ⟨h, h⟩"),
                 ("native_decide", "by native_decide"),
                 ("unsafe", "unsafe ⟨h, h⟩"),
                 ("implemented_by", "let rec @[implemented_by id] f : Nat → Nat := fun n => n\n⟨h, h⟩"),
                 ("extern", "let rec @[extern \"f\"] f : Nat → Nat := fun n => n\n⟨h, h⟩"),
                 ("by_elab", "by_elab pure (Lean.mkConst ``True.intro)"),
                 ("unhygienic", "by unhygienic exact ⟨h, h⟩"),
                 ("Lean.Name", "(fun (_ : Lean.Name) => ⟨h, h⟩) Lean.Name.anonymous"),
                 ("IO.println", "(fun (_ : IO Unit) => ⟨h, h⟩) (IO.println \"x\")"),
                 ("System.FilePath", "(fun (_ : System.FilePath) => ⟨h, h⟩) \"x\"")]
        results = self.evaluate_all([{"proof": proof, "mode": "validate"} for _, proof in cases])
        for (word, proof), result in zip(cases, results):
            with self.subTest(proof=proof):
                self.assertEqual(result["validity_status"], "output_format_failure", result)
                self.assertTrue(result["failure_reason"].endswith(f": {word}"), result)

    def test_missing_or_non_string_proof_is_evaluator_error(self):
        results = self.evaluate_all([{"proof": proof, "mode": "validate"} for proof in (None, 17, ["by"], "")])
        for proof, result in zip((None, 17, ["by"]), results):
            with self.subTest(proof=proof):
                self.assertEqual(result["validity_status"], "evaluator_error", result)
        # An empty response is still the model's own parse failure.
        self.assertEqual(results[-1]["validity_status"], "parse_failure", results[-1])

    def test_doc_comments_are_not_counted(self):
        comments = ["", "/- documentation -/ ", "/-- documentation -/ ", "/-- " + "word " * 200 + "-/ "]
        results = self.evaluate_all([{"proof": f"let rec {c}f : Nat := 0\n⟨h, h⟩", "mode": "validate"}
                                     for c in comments])
        for comment, result in zip(comments, results):
            with self.subTest(comment=comment[:20]):
                self.assertEqual(result["validity_status"], "success", result)
                self.assertEqual(self.body_tokens(result),
                                 ["let", "rec", "f", ":", "Nat", ":=", "0", "⟨", "h", ",", "h", "⟩"])
                self.assertEqual(result["source_tokens"]["W_B"], 12)

    def test_statement_matchers_are_compared_and_replayed(self):
        cases = [(MATCH_BINDERS, MATCH_PROPOSITION, "by cases n <;> simp", "extract", ["match_1"]),
                 ("", "(fun ((a, b) : Nat × Nat) => a + b) (1, 2) = 3", "rfl", "validate", ["match_1"]),
                 ("(o : Option Nat)", "(if let some x := o then x else 0) = o.getD 0", "by cases o <;> rfl", "validate",
                  ["_sparseCasesOn_1", "match_1"]),
                 ("", "(⟨2, by omega⟩ : {n : Nat // n > 1}).val = 2", "rfl", "validate", ["_proof_1"])]
        results = self.evaluate_all([{"proof": proof, "binders": binders, "proposition": prop, "mode": mode}
                                     for binders, prop, proof, mode, _ in cases])
        for case, result in zip(cases, results):
            with self.subTest(proposition=case[1]):
                self.assertEqual(result["validity_status"], "success", result)
                self.assertTrue(result["kernel_replay"])
                # Both forks' header auxiliaries, sorted. None of these proofs makes a helper of its own,
                # so every other candidate declaration comes from the statement.
                self.assertEqual(result["statement_auxiliaries"], [f"{fork}.{suffix}" for fork in
                                 ("__compression_trusted_signature", "evaluation_target") for suffix in case[4]])
                self.assertEqual(set(result["candidate_declarations"]) - set(result["statement_auxiliaries"]),
                                 {"evaluation_target"})
        extracted = results[0]
        self.assertEqual(extracted["metric_status"], "available", extracted)
        self.assertIn("evaluation_target.match_1", extracted["candidate_declarations"])
        self.assertEqual(self.root(extracted)["signature"]["evaluation_target.match_1"], 1)
        self.assertNotIn("__compression_trusted_signature.match_1", {x["name"] for x in extracted["nodes"]})

    def test_statement_matchers_keep_soundness_gates(self):
        def helper(name):
            # An unused helper whose matcher is named after `name`.
            return (f"have _ : Nat → Bool := with_decl_name% {name} (fun (m : Nat) => "
                    "(match m with | 0 => true | 1 => true | _ + 2 => false : Bool))\nby cases n <;> simp")
        # The match pattern `two` is 3 in namespace `Shadow`, where a dotted target name elaborates the
        # candidate's header, and 2 for the trusted signature: the two statements' matchers differ.
        shadowed = {"context": "@[match_pattern] def two : Nat := 2\nnamespace Shadow\n"
                               "@[match_pattern] def two : Nat := 3\nend Shadow\n",
                    "proposition": "(match n with | two => 3 | k => k) = n", "proof": "by split <;> rfl"}
        requests = [
            # A proof valid only for the candidate's different matcher statement fails the comparison
            # itself; without it, replay against the trusted type would still reject the root.
            {**shadowed, "target_name": "Shadow.evaluation_target"},
            # The same proof is valid when both forks elaborate the statement inside `Shadow`.
            {**shadowed, "context": shadowed["context"] + "namespace Shadow\n"},
            # The trusted signature stays invisible to the candidate.
            {"proof": "__compression_trusted_signature n"},
            # A candidate matcher named like the trusted one cannot stand in for it; the same proof
            # with any other helper name is valid.
            {"proof": helper("__compression_trusted_signature")},
            {"proof": helper("unrelated_helper")}]
        results = self.evaluate_all([{"binders": MATCH_BINDERS, "proposition": MATCH_PROPOSITION,
                                      "mode": "validate", **request} for request in requests])
        self.assertEqual(results[0]["validity_status"], "target_mismatch", results[0])
        self.assertEqual(results[1]["validity_status"], "success", results[1])
        self.assertEqual(results[2]["validity_status"], "elaboration_failure", results[2])
        self.assertIn("__compression_trusted_signature", results[2]["failure_reason"])
        self.assertEqual(results[3]["validity_status"], "kernel_replay_failure", results[3])
        self.assertEqual(results[4]["validity_status"], "success", results[4])
        self.assertIn("unrelated_helper.match_1", results[4]["candidate_declarations"])
        self.assertNotIn("unrelated_helper.match_1", results[4]["statement_auxiliaries"])

    def test_replay_uses_lean_kernel_budget(self):
        # Both need over 2,000,000 kernel steps: one lemma, or two of about 1.2 million each.
        # 83da0074 replayed them under the raw max_heartbeats value, 1000 times below Lean.addDecl's
        # budget for the same option, and one counter shared by every replayed declaration.
        cases = [("(List.replicate 100000 1).length = 100000", "by decide +kernel", 2),
                 (TWO_LEMMAS, "⟨by decide +kernel, by decide +kernel⟩", 3)]
        results = self.evaluate_all([{"proof": proof, "binders": "", "proposition": proposition, "mode": "validate"}
                                     for proposition, proof, _ in cases])
        for (proposition, _, declarations), result in zip(cases, results):
            with self.subTest(proposition=proposition):
                self.assertEqual(result["validity_status"], "success", result)
                self.assertTrue(result["kernel_replay"])
                self.assertEqual(len(result["candidate_declarations"]), declarations, result)
                # The proof's `decide +kernel` lemmas are helpers, not statement auxiliaries.
                self.assertEqual(result["statement_auxiliaries"], [], result)

    def test_replay_limits_apply_per_declaration_and_keep_their_statuses(self):
        lemmas = {"binders": "", "proposition": TWO_LEMMAS, "proof": "⟨by decide +kernel, by decide +kernel⟩",
                  "mode": "validate"}
        matcher = {"binders": MATCH_BINDERS, "proposition": MATCH_PROPOSITION, "proof": "by cases n <;> simp",
                   "mode": "validate"}
        fresh, timeout, depth, trusted, malformed = self.evaluate_all([
            {**lemmas, "replay_max_heartbeats": 1500000}, {**lemmas, "replay_max_heartbeats": 1000000},
            {**lemmas, "replay_max_rec_depth": 1}, {**matcher, "replay_max_heartbeats": 1},
            {**lemmas, "replay_max_heartbeats": "1500000"}])
        # 1.5 million covers either lemma, not both: this needs a fresh kernel counter per declaration.
        self.assertEqual(fresh["validity_status"], "success", fresh)
        # Kernel limits while replaying the candidate keep their own statuses, not kernel_replay_failure.
        self.assertEqual(timeout["validity_status"], "verification_timeout", timeout)
        self.assertRegex(timeout["failure_reason"],
                         r"^kernel deterministic timeout replaying evaluation_target\._proof_[12]$")
        self.assertEqual(depth["validity_status"], "verification_resource_limit", depth)
        self.assertRegex(depth["failure_reason"], r"^kernel resource limit replaying evaluation_target\._proof_[12]$")
        # The trusted statement auxiliaries are checked first; their failure is the environment's.
        self.assertEqual(trusted["validity_status"], "environment_error", trusted)
        self.assertEqual(trusted["failure_reason"],
                         "kernel deterministic timeout replaying __compression_trusted_signature.match_1")
        self.assertEqual(malformed["validity_status"], "evaluator_error", malformed)

    def test_long_crlf_proof_span_mapping_is_linear(self):
        steps = 1000
        proof = ("by\r\n" + "".join(f"  have h{i} : P ∧ P := ⟨h, h⟩\r\n" for i in range(steps))
                 + "  exact ⟨h, h⟩\r\n")
        started = time.monotonic()
        result = self.evaluate(proof, mode="validate", verify_histogram_cache=False)
        elapsed = time.monotonic() - started
        self.assertEqual(result["validity_status"], "success", result)
        tokens = self.body_tokens(result)
        self.assertEqual(len(tokens), 12 * steps + 7)
        self.assertEqual(tokens[:13], ["by", "have", "h0", ":", "P", "∧", "P", ":=", "⟨", "h", ",", "h", "⟩"])
        self.assertEqual(tokens[-18:], ["have", f"h{steps - 1}", ":", "P", "∧", "P", ":=", "⟨", "h", ",", "h", "⟩",
                                        "exact", "⟨", "h", ",", "h", "⟩"])
        # 83da0074 needed 163 s here, rescanning the source for every leaf.
        self.assertLess(elapsed, 45, elapsed)

    def test_shared_proof_terms_are_counted_exactly_within_budget(self):
        started = time.monotonic()
        result = self.evaluate("by ring", imports=["Mathlib.Tactic.Ring"], mode="validate",
                               verify_histogram_cache=False, binders="{R : Type} [CommRing R] (a b c d : R)",
                               proposition="(a + b + c + d)^6 = (a + b + c + d)^3 * (a + b + c + d)^3")
        elapsed = time.monotonic() - started
        self.assertEqual(result["validity_status"], "success", result)
        # Every occurrence counts, through all sharing; 83da0074 hit the 60 s validation cap here.
        self.assertEqual(result["diagnostics"]["proof_nodes"], 105243029)
        self.assertLess(elapsed, 45, elapsed)

    def test_literal_bytes_are_exact_decimal_lengths(self):
        boundary = {"proof": "rfl", "binders": "", "mode": "extract", "proposition":
                    "(9999999999999999999999999999999999999999 + 1 : Nat) = 10000000000000000000000000000000000000000"}
        large, huge = ({"proof": literal_proof(exponent), "binders": "", "proposition": "True", "mode": "extract",
                        "verify_histogram_cache": False} for exponent in (16, 256))
        started = time.monotonic()
        results = self.evaluate_all([boundary, large, huge])
        elapsed = time.monotonic() - started
        for result in results:
            self.assertEqual(result["validity_status"], "success", result)
            self.assertEqual(result["metric_status"], "available", result)
        # Values computed by 83da0074 through decimal conversion; the 2^(2^20) case took it about 40 s.
        self.assertEqual(self.root(results[0])["literal_bytes"], 246)
        self.assertEqual(self.root(results[1])["literal_bytes"], 6 * 315653 + 38)
        # The same six occurrences of 2^(2^24), which has 5,050,446 digits; 83da0074 hit the 60 s
        # validation cap converting it. The small literals grow by one digit in the last exponent.
        self.assertEqual(self.root(results[2])["literal_bytes"], 6 * 5050446 + 42)
        self.assertLess(elapsed, 45, elapsed)


if __name__ == "__main__":
    unittest.main()
