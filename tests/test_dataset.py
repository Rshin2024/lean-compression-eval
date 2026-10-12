import copy
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.dataset import (
    DOMAINS, load_tasks, readiness_errors, sha256_text, validate_manifest,
    validate_references,
)


def task(i="one", **changes):
    value = dict(task_id=i, family_id="family_" + i,
                 domain=DOMAINS[0], split="pilot", origin="fresh", stratum="easy",
                 imports=["Mathlib"], context="", binders="(P : Prop)",
                 proposition="P → P", target_name="Eval." + i,
                 premise_context="", forbidden_declarations=[])
    value.update(changes)
    return value


class DatasetTests(unittest.TestCase):
    def test_loader_keeps_private_unicode_and_newlines_as_data(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "tasks.jsonl"
            p.write_text(json.dumps(task(), ensure_ascii=False) + "\n\n")
            self.assertEqual(load_tasks(p), [task()])
            q = Path(folder) / "tasks.json"
            q.write_text(json.dumps({"tasks": [task()]}))
            self.assertEqual(load_tasks(q), [task()])

    def test_family_split_leakage_and_duplicate_task_are_rejected(self):
        a = task()
        b = task("two", split="main", family_id=a["family_id"], proposition="True")
        self.assertTrue(any("crosses" in e for e in validate_manifest([a, b])))
        self.assertTrue(any("duplicate task_id" in e for e in validate_manifest([a, a])))

    def test_public_proof_and_imported_answer_are_rejected(self):
        a = task(proof="by intro h; exact h")
        self.assertTrue(any("private reference" in e for e in validate_manifest([a])))
        b = task(origin="library", imports=["Mathlib.Data.Set.Basic"],
                 provenance={"module": "Mathlib.Data.Set.Basic"})
        self.assertTrue(any("target module" in e for e in validate_manifest([b])))

    def test_hash_tampering_is_detected(self):
        a = task(hashes={"proposition": sha256_text("False")})
        self.assertTrue(any("stale proposition" in e for e in validate_manifest([a])))

    def test_quotas_check_domain_and_source_counts(self):
        self.assertFalse(validate_manifest([task()]))
        errors = validate_manifest([task()], enforce_quotas=True)
        self.assertTrue(any("expected 250" in e for e in errors))
        self.assertTrue(any("main/algebra_groups/library" in e for e in errors))

    def test_structural_success_cannot_claim_preregistered_readiness(self):
        a = task()
        self.assertEqual(len(readiness_errors([a])), 5)
        a["audit"] = dict.fromkeys(("reference_verified", "explicit_signature_frozen",
                                   "answer_access_audited", "family_reviewed",
                                   "metric_closure_verified"), True)
        self.assertFalse(readiness_errors([a]))

    def test_references_must_be_one_to_one(self):
        a = task()
        self.assertFalse(validate_references([a], [{"task_id":"one", "proof":"by tauto"}]))
        errors = validate_references([a], [{"task_id":"other", "proof":""}])
        self.assertEqual(len(errors), 3)

    def test_unsafe_target_name_is_rejected(self):
        a = task(target_name="T : False := by sorry\ntheorem injected")
        self.assertTrue(any("unsafe target_name" in e for e in validate_manifest([a])))

    def test_malformed_records_return_errors_without_crashing(self):
        a = task(domain=[], provenance=[], hashes=[])
        errors = validate_manifest([a, []], enforce_quotas=True)
        self.assertTrue(any("domain must be a string" in e for e in errors))
        self.assertTrue(any("provenance must be an object" in e for e in errors))
        self.assertTrue(any("hashes must be an object" in e for e in errors))
        self.assertTrue(any("expected a task object" in e for e in errors))

    def test_import_descriptors_preserve_order_and_boolean_flags(self):
        a = task(import_specs=[{"module":"Mathlib", "is_meta":True,
                                "is_exported":False, "import_all":False}])
        self.assertFalse(validate_manifest([a]))
        a["import_specs"][0]["is_meta"] = "true"
        self.assertTrue(any("must be a boolean" in e for e in validate_manifest([a])))
        a["import_specs"][0]["module"] = "Lean"
        self.assertTrue(any("module order" in e for e in validate_manifest([a])))


if __name__ == "__main__":
    unittest.main()
