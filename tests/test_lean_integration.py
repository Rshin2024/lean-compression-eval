"""Real Lean fixtures. Skip only when the pinned local runtime is unavailable.

These are trusted evaluator fixtures, not arbitrary model responses. The production
runner provides the operating-system isolation required for model completions.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from lean_runtime import pinned_runtime
except ModuleNotFoundError:  # python3 -m unittest tests.test_lean_integration, run from the repository root
    from tests.lean_runtime import pinned_runtime

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = None  # A fixed manifest path; None follows LEAN_COMPRESSION_ENVIRONMENT when the fixtures start.


class LeanIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment, cls.lean = pinned_runtime(MANIFEST)
        cls.process_env = {**os.environ, "LEAN_PATH": os.pathsep.join(cls.environment["lean_search_paths"]),
                           "PATH": str(Path(cls.lean).parent) + os.pathsep + os.environ.get("PATH", "")}

    def evaluate(self, proof="by exact ⟨h, h⟩", **changes):
        request = {"imports": ["Init"], "target_name": "evaluation_target",
                   "binders": "(P : Prop) (h : P)", "proposition": "P ∧ P",
                   "proof": proof, "max_heartbeats": 2000000, "verify_histogram_cache": True}
        request.update(changes)
        with tempfile.TemporaryDirectory() as directory:
            inp, out = Path(directory) / "request.json", Path(directory) / "result.json"
            inp.write_text(json.dumps(request))
            completed = subprocess.run([self.lean, "--run", str(ROOT / "lean" / "Evaluator.lean"),
                                        str(inp), str(out)], env=self.process_env,
                                       text=True, capture_output=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(out.exists(), completed.stdout + completed.stderr)
            return json.loads(out.read_text())

    def test_import_specs_preserve_defaults_and_reject_mismatch(self):
        result = self.evaluate(import_specs=[{"module": "Init"}], mode="validate")
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["resolved_imports"], [{"module": "Init", "is_meta": False,
                                                     "is_exported": True, "import_all": False}])
        mismatch = self.evaluate(import_specs=[{"module": "Lean"}])
        self.assertEqual(mismatch["validity_status"], "environment_error", mismatch)
        malformed = self.evaluate(import_specs=[{"module": "Init", "is_meta": "yes"}])
        self.assertEqual(malformed["validity_status"], "environment_error", malformed)

    def test_real_pow_context_preserves_public_meta_import(self):
        modules = ["Mathlib.Analysis.SpecialFunctions.Pow.Complex", "Mathlib.Data.Nat.NthRoot.Defs", "Qq"]
        specs = [{"module": name, "is_meta": name == "Mathlib.Data.Nat.NthRoot.Defs",
                  "is_exported": True, "import_all": False} for name in modules]
        context = """@[expose] public section
noncomputable section
open Real Finset Set
namespace Real
noncomputable def rpow (x y : ℝ) := ((x : ℂ) ^ (y : ℂ)).re
noncomputable instance : Pow ℝ ℝ := ⟨rpow⟩
"""
        result = self.evaluate("rfl", imports=modules, import_specs=specs,
                               context=context, binders="(x y : ℝ)",
                               proposition="rpow x y = x ^ y", mode="validate",
                               forbidden_declarations=["Real.rpow_eq_pow"])
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["resolved_imports"], specs)

    def test_valid_proof_and_token_spans(self):
        result = self.evaluate()
        self.assertEqual(result["validity_status"], "success", result)
        self.assertTrue(result["kernel_replay"])
        self.assertEqual(result["axioms"], [])
        source = result["source_tokens"]
        self.assertEqual(source["W_B"], 7)
        encoded = source["source"].encode()
        self.assertEqual([encoded[s["start"]:s["end"]].decode() for s in source["body_spans"]],
                         ["by", "exact", "⟨", "h", ",", "h", "⟩"])

    def test_whitespace_comments_renaming_and_crlf(self):
        base = self.evaluate()
        changed = self.evaluate("by\r\n  /- commentary -/ exact ⟨renamed, renamed⟩ -- end\r\n",
                                binders="(P : Prop) (renamed : P)")
        self.assertEqual(changed["validity_status"], "success", changed)
        self.assertEqual(base["source_tokens"]["W_B"], changed["source_tokens"]["W_B"])
        self.assertEqual(base["source_tokens"]["W_S"], changed["source_tokens"]["W_S"])
        encoded = changed["source_tokens"]["source"].encode()
        spans = changed["source_tokens"]["body_spans"]
        self.assertEqual([encoded[x["start"]:x["end"]].decode() for x in spans],
                         ["by", "exact", "⟨", "renamed", ",", "renamed", "⟩"])

    def test_repeated_reference_multiplicity(self):
        result = self.evaluate("And.intro True.intro True.intro", binders="", proposition="True ∧ True")
        self.assertEqual(result["validity_status"], "success", result)
        root = next(x for x in result["nodes"] if x["name"] == result["root"])
        self.assertEqual(root["body"]["True.intro"], 2)
        self.assertEqual(root["body"]["And.intro"], 1)

    def test_internal_numeric_name_is_traversed_without_string_roundtrip(self):
        context = """open Lean Elab Term
run_cmd Lean.Elab.Command.liftCoreM <| Lean.addDecl (.thmDecl { name := Lean.Name.num `numeric 17, levelParams := [], type := Lean.mkConst ``True, value := Lean.mkConst ``True.intro })
syntax "numericProof" : term
elab_rules : term
  | `(numericProof) => return Lean.mkConst (Lean.Name.num `numeric 17)
"""
        result = self.evaluate("numericProof", imports=["Lean"], context=context,
                               binders="", proposition="True")
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["metric_status"], "available", result)
        nodes = {x["name"]: x for x in result["nodes"]}
        self.assertIn("numeric.17", nodes)
        self.assertEqual(nodes["numeric.17"]["body"], {"True.intro": 1})
        forbidden = self.evaluate("numericProof", imports=["Lean"], context=context,
                                  binders="", proposition="True", forbidden_declarations=["numeric.17"])
        self.assertEqual(forbidden["validity_status"], "forbidden_environment", forbidden)

    def test_non_roundtrippable_internal_name_and_forbidden_gate(self):
        # Name.toString is not a round-trip encoding: an embedded closing
        # guillemet prints as internal.a»b, whose String.toName is anonymous.
        context = """open Lean Elab Term
run_cmd Lean.Elab.Command.liftCoreM <| Lean.addDecl (.thmDecl { name := Lean.Name.str `internal "a»b", levelParams := [], type := Lean.mkConst ``True, value := Lean.mkConst ``True.intro })
syntax "unusualProof" : term
elab_rules : term
  | `(unusualProof) => return Lean.mkConst (Lean.Name.str `internal "a»b")
"""
        result = self.evaluate("unusualProof", imports=["Lean"], context=context,
                               binders="", proposition="True")
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["metric_status"], "available", result)
        self.assertIn("internal.a»b", {x["name"] for x in result["nodes"]})
        forbidden = self.evaluate("True.intro", imports=["Lean"], context=context,
                                  binders="", proposition="True", forbidden_declarations=["internal.a»b"])
        self.assertEqual(forbidden["validity_status"], "forbidden_environment", forbidden)

    def test_generated_auxiliary_declaration_and_cache(self):
        result = self.evaluate("by as_aux_lemma => exact ⟨h,h⟩")
        self.assertEqual(result["validity_status"], "success", result)
        self.assertGreater(len(result["candidate_declarations"]), 1)
        nodes = {x["name"]: x for x in result["nodes"]}
        for name in result["candidate_declarations"]:
            self.assertIn(name, nodes)
            self.assertFalse(nodes[name]["primitive"])
            self.assertIsNone(nodes[name]["source_counts"])
        self.assertTrue(result["diagnostics"]["cached_uncached_root_histogram_agree"])

    def test_theorem_and_opaque_bodies_are_visible(self):
        result = self.evaluate("support", binders="", proposition="True",
                               context="opaque hidden : True := True.intro\ntheorem support : True := hidden")
        self.assertEqual(result["validity_status"], "success", result)
        nodes = {x["name"]: x for x in result["nodes"]}
        self.assertEqual(nodes["support"]["body"], {"hidden": 1})
        self.assertEqual(nodes["hidden"]["body"], {"True.intro": 1})
        self.assertFalse(nodes["hidden"]["primitive"])
        self.assertEqual(nodes["True.intro"]["owner_module"], "Init.Prelude")
        self.assertTrue(nodes["True.intro"]["primitive"])
        self.assertEqual(nodes["True.intro"]["histogram_status"], "atomic_boundary_not_expanded")
        self.assertFalse(nodes["True.intro"]["body_present"])

    def test_sorry_direct_indirect_and_custom_axiom(self):
        for proof, context in [("by sorry", ""), ("by have ignored : True := by sorry; exact False.elim (by sorry)", ""), ("bad", "theorem bad : False := by sorry"),
                               ("bad", "axiom bad : False")]:
            with self.subTest(proof=proof, context=context):
                result = self.evaluate(proof, context=context, binders="", proposition="False")
                self.assertEqual(result["validity_status"], "disallowed_axiom", result)

    def test_modified_target_and_multiple_proofs_are_rejected(self):
        for proof in ["theorem other : True := True.intro", "by exact ⟨h,h⟩\ntheorem other : True := True.intro",
                      "by exact ⟨h,h⟩\nby exact ⟨h,h⟩", "```lean\nby exact ⟨h,h⟩\n```"]:
            with self.subTest(proof=proof):
                result = self.evaluate(proof)
                self.assertEqual(result["validity_status"], "parse_failure", result)

    def test_wrong_proposition_is_elaboration_failure(self):
        result = self.evaluate("True.intro", binders="", proposition="False")
        self.assertEqual(result["validity_status"], "elaboration_failure", result)

    def test_forbidden_declaration_absent_before_candidate(self):
        result = self.evaluate("True.intro", binders="", proposition="True",
                               forbidden_declarations=["True.intro"])
        self.assertEqual(result["validity_status"], "forbidden_environment", result)

    def test_custom_metaprogramming_rejected_before_elaboration(self):
        result = self.evaluate("by run_tac pure ()")
        self.assertEqual(result["validity_status"], "output_format_failure", result)

    def test_metric_error_preserves_validity(self):
        result = self.evaluate(max_graph_nodes=1)
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["metric_status"], "unavailable")
        self.assertIn("max_graph_nodes", result["metric_error"])
        self.assertEqual(self.evaluate(mode="validate")["metric_status"], "not_requested")

    def test_namespace_context_and_fully_elaborated_signature(self):
        result = self.evaluate("by exact ⟨h,h⟩", context="namespace Example\nvariable (unused : Nat)")
        self.assertEqual(result["validity_status"], "success", result)
        self.assertEqual(result["root"], "Example.evaluation_target")
        self.assertIn("∀", result["trusted_type_source"])

    def test_lexical_qualified_identifier_numeral_and_string(self):
        result = self.evaluate("Eq.refl (\"unicode λ\", 123)", binders="", proposition='("unicode λ", 123) = ("unicode λ", 123)')
        self.assertEqual(result["validity_status"], "success", result)
        source = result["source_tokens"]["source"].encode()
        tokens = [source[x["start"]:x["end"]].decode() for x in result["source_tokens"]["body_spans"]]
        self.assertEqual(tokens, ["Eq.refl", "(", '"unicode λ"', ",", "123", ")"])


if __name__ == "__main__":
    unittest.main()
