import copy
import json
from pathlib import Path
import tempfile
import unittest

from lean_compression.io import amend_reference_identity, digest, task_input_hash
from lean_compression.readiness import AUDIT_FLAGS, build_readiness_report, write_readiness_report


def task(name, split="pilot", family=None, domain="sets_logic"):
    return {"task_id": name, "family_id": family or name, "domain": domain, "origin": "fresh",
            "split": split, "stratum": "provisional_easy", "imports": [], "context": "",
            "binders": f"(P{name} : Prop)", "proposition": f"P{name} → P{name}",
            "target_name": "target_" + name, "premise_context": "", "forbidden_declarations": [],
            "audit": {flag: True for flag in AUDIT_FLAGS}}


def reference(t, wb=2, depth=3, proof="by rfl"):
    return {"task_id": t["task_id"], "task_input_hash": task_input_hash(t), "validity_status": "success",
            "environment_hash": "env", "boundary_policy_hash": "policy", "engine_hash": "engine",
            "source_token_policy_hash": "tokens", "metric_status": "available", "proof": proof,
            "validation": {"validity_status": "success", "kernel_replay": True, "axioms": [],
                           "trusted_type_expression": "trusted type"},
            "extraction": {"metric_status": "available", "nodes": {}, "source_tokens": {
                "source": "source", "body_spans": [], "W_B": wb}},
            "metrics": {"metric_status": "available", "W_S": 5, "W_B": wb, "D": depth,
                        "U_S_decimal": "3", "U_B_decimal": "11"}}


def report(tasks, refs, **settings):
    return build_readiness_report(tasks, refs, {"enforce_quotas": False, **settings})


class ReadinessTests(unittest.TestCase):
    def test_compilation_audit_flag_does_not_replace_independent_evidence(self):
        t = task("one")
        t["audit"]["reference_verification_method"] = "Lean compiler only"
        r = reference(t)
        r["validation"].pop("kernel_replay")
        result = report([t], [r])
        self.assertFalse(result["gates"]["all_reference_evidence_complete"])
        self.assertIn("independent_kernel_replay_not_verified", result["tasks"]["one"]["missing_evidence"])
        self.assertEqual(result["groups"]["all"]["measured_stratum_population"], 0)

    def test_identity_requires_input_hash_or_exact_full_legacy_hash(self):
        t = task("one")
        r = reference(t)
        r.pop("task_input_hash")
        r["task_hash"] = digest(t)
        result = report([t], [r])
        self.assertEqual(result["tasks"]["one"]["identity_method"], "exact_full_task_hash_legacy")
        changed = copy.deepcopy(t)
        changed["audit"]["family_reviewed"] = False
        result = report([changed], [r])
        self.assertIsNone(result["tasks"]["one"]["identity_method"])
        r["task_input_hash"] = task_input_hash(t)
        result = report([changed], [r])
        self.assertEqual(result["tasks"]["one"]["identity_method"], "task_input_hash")
        changed["proposition"] = "False"
        self.assertIsNone(report([changed], [r])["tasks"]["one"]["identity_method"])

    def test_measured_pilot_cutpoints_assign_main_without_calling_it_difficulty(self):
        tasks = [task("a"), task("b"), task("c"), task("d"), task("main", "main")]
        refs = [reference(t, wb, dep) for t, wb, dep in zip(tasks, (2, 4, 8, 16, 30), (1, 2, 4, 8, 20))]
        result = report(tasks, refs)
        strata = result["proposed_reference_strata"]
        self.assertEqual(strata["source_length_cutpoints"], ["7/2", "10"])
        self.assertEqual(strata["dependency_depth_cutpoints"], ["7/4", "5"])
        self.assertEqual(strata["fit_task_ids"], ["a", "b", "c", "d"])
        self.assertEqual(result["tasks"]["main"]["reference_length_bin"], "high")
        self.assertEqual(result["tasks"]["main"]["reference_depth_bin"], "high")
        self.assertFalse(result["gates"]["length_depth_cutpoints_frozen"])
        self.assertFalse(result["gates"]["difficulty_coverage_reviewed"])

    def test_duplicates_missing_refs_and_stale_refs_are_explicit(self):
        a, b, c = task("a"), task("b"), task("c")
        r1, r2 = reference(a), reference(a, wb=4)
        stale = reference(b)
        stale["task_input_hash"] = "not_current"
        result = report([a, b, c], [r1, r2, stale])
        self.assertIn("multiple_matching_reference_records", result["tasks"]["a"]["missing_evidence"])
        self.assertEqual(result["tasks"]["b"]["stale_or_unmatched_reference_count"], 1)
        self.assertIn("missing_reference_record", result["tasks"]["c"]["missing_evidence"])

    def test_mixed_policies_are_not_pooled_into_distributions(self):
        a, b = task("a"), task("b")
        ra, rb = reference(a), reference(b)
        rb["boundary_policy_hash"] = "other"
        result = report([a, b], [ra, rb])
        self.assertFalse(result["gates"]["reference_provenance_consistent"])
        self.assertEqual(result["groups"]["all"]["reference_W_B"]["count"], 0)
        self.assertEqual(len(result["reference_cohorts"]), 2)

    def test_runtime_identity_allows_only_prompt_presentation_changes(self):
        original = task("one")
        original["premise_context"] = "Original supplied context"
        r = amend_reference_identity(reference(original), original)
        presented = copy.deepcopy(original)
        presented["premise_context"] = "New rendering of the same runtime context"
        result = report([presented], [r])
        self.assertEqual(result["tasks"]["one"]["identity_method"], "verification_request_hash")
        self.assertTrue(result["tasks"]["one"]["reference_evidence_complete"])
        self.assertFalse(r["identity_amendment"]["new_proof_execution"])
        for field, value in (("context", "axiom different : False"), ("imports", ["Mathlib"]),
                             ("proposition", "False"), ("target_name", "different_target")):
            changed = copy.deepcopy(presented)
            changed[field] = value
            self.assertIsNone(report([changed], [r])["tasks"]["one"]["identity_method"], field)

    def test_expected_engine_selects_a_cohort_without_pooling_other_token_policy(self):
        a, b = task("a"), task("b")
        ra, rb = reference(a), reference(b)
        rb["engine_hash"], rb["source_token_policy_hash"] = "old-engine", "old-tokens"
        result = report([a, b], [ra, rb], engine_hash="engine")
        self.assertEqual(result["groups"]["all"]["reference_W_B"]["count"], 1)
        self.assertEqual(result["mixed_provenance"], {})
        self.assertIn("mismatched_engine_hash", result["tasks"]["b"]["missing_evidence"])

    def test_source_only_controls_are_descriptive_and_audits_stay_separate(self):
        a, b = task("a"), task("b", "main")
        a["audit"]["family_reviewed"] = False
        result = report([a, b], [reference(a, proof="rfl"), reference(b, proof="by omega")])
        self.assertEqual(result["source_only_controls"]["literal_reflexivity"], ["a"])
        self.assertEqual(result["source_only_controls"]["single_tactic_wrapper"], ["b"])
        self.assertFalse(result["gates"]["all_required_audits_complete"])
        self.assertEqual(result["groups"]["origin:fresh"]["tasks"], 2)

    def test_all_gates_required_and_readiness_artifacts_are_inspectable(self):
        t = task("ready")
        result = report([t], [reference(t)], source_length_cutpoints=[2, 20],
                        dependency_depth_cutpoints=[3, 30], strata_frozen=True, difficulty_review_complete=True)
        # This one-task fixture disables quotas, so that gate alone stays pending.
        self.assertEqual(result["status"], "not_ready_for_dataset_freeze")
        self.assertEqual([gate for gate, passed in result["gates"].items() if not passed], ["quotas_enforced"])
        with tempfile.TemporaryDirectory() as directory:
            paths = write_readiness_report(result, directory)
            self.assertEqual(json.loads(Path(paths["json"]).read_text()), result)
            self.assertIn("no model outcomes", Path(paths["html"]).read_text())
            write_readiness_report(result, directory)

    def test_cross_domain_families_are_visible(self):
        a, b = task("a", family="same"), task("b", family="same", domain="number_theory")
        result = report([a, b], [reference(a), reference(b)])
        self.assertEqual(result["family_structure"]["cross_domain_families"]["same"], ["number_theory", "sets_logic"])


if __name__ == "__main__":
    unittest.main()
