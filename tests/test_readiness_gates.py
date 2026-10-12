import unittest

from lean_compression.io import task_input_hash
from lean_compression.readiness import AUDIT_FLAGS, build_readiness_report


def task(name):
    return {"task_id": name, "family_id": name, "domain": "sets_logic", "origin": "fresh",
            "split": "pilot", "stratum": "provisional_easy", "imports": [], "context": "",
            "binders": f"(P{name} : Prop)", "proposition": f"P{name} → P{name}",
            "target_name": "target_" + name, "premise_context": "", "forbidden_declarations": [],
            "audit": {flag: True for flag in AUDIT_FLAGS}}


def reference(t, wb=2, depth=3):
    return {"task_id": t["task_id"], "task_input_hash": task_input_hash(t), "validity_status": "success",
            "environment_hash": "env", "boundary_policy_hash": "policy", "engine_hash": "engine",
            "source_token_policy_hash": "tokens", "metric_status": "available", "proof": "fun h => h",
            "validation": {"validity_status": "success", "kernel_replay": True,
                           "axioms": ["propext", "Classical.choice", "Quot.sound"],
                           "trusted_type_expression": "trusted type"},
            "extraction": {"metric_status": "available", "nodes": {}, "source_tokens": {
                "source": "source", "body_spans": [], "W_B": wb}},
            "metrics": {"metric_status": "available", "W_S": 5, "W_B": wb, "D": depth,
                        "U_S_decimal": "3", "U_B_decimal": "11"}}


def report(tasks, refs, **settings):
    return build_readiness_report(tasks, refs, {"enforce_quotas": False, **settings})


class EvidenceGateTests(unittest.TestCase):
    def test_each_reference_evidence_requirement_is_enforced(self):
        t = task("one")
        complete = report([t], [reference(t)])["tasks"]["one"]
        self.assertEqual(complete["missing_evidence"], [])
        self.assertTrue(complete["eligible_for_strata"])
        cases = {"disallowed_reference_axioms": ("validation", "axioms", ["propext", "sorryAx"]),
                 "missing_trusted_target_evidence": ("validation", "trusted_type_expression", None),
                 "missing_dependency_evidence": ("extraction", "metric_status", "unavailable"),
                 "source_count_evidence_mismatch": ("extraction", "source_tokens",
                                                    {"source": "source", "body_spans": [], "W_B": 3})}
        for reason, (section, key, value) in cases.items():
            with self.subTest(reason):
                r = reference(t)
                r[section][key] = value
                row = report([t], [r])["tasks"]["one"]
                self.assertEqual(row["missing_evidence"], [reason])
                self.assertFalse(row["reference_evidence_complete"])
                self.assertFalse(row["eligible_for_strata"])
        r = reference(t)
        del r["extraction"]["nodes"]
        self.assertEqual(report([t], [r])["tasks"]["one"]["missing_evidence"], ["missing_dependency_evidence"])

    def test_fixed_cutpoints_put_boundary_values_in_the_lower_bin(self):
        tasks = [task(name) for name in ("at_first", "at_second", "above")]
        refs = [reference(t, wb, depth) for t, wb, depth in zip(tasks, (2, 20, 21), (3, 30, 31))]
        result = report(tasks, refs, source_length_cutpoints=[2, 20], dependency_depth_cutpoints=[3, 30])
        self.assertEqual({name: (row["reference_length_bin"], row["reference_depth_bin"])
                          for name, row in result["tasks"].items()},
                         {"at_first": ("low", "low"), "at_second": ("middle", "middle"), "above": ("high", "high")})

    def test_cutpoints_are_frozen_only_with_the_explicit_strata_flag(self):
        t = task("one")
        cuts = {"source_length_cutpoints": [2, 20], "dependency_depth_cutpoints": [3, 30]}
        frozen = lambda **settings: report([t], [reference(t)], **settings)["gates"]["length_depth_cutpoints_frozen"]
        self.assertFalse(frozen(**cuts))
        self.assertFalse(frozen(strata_frozen="true", **cuts))
        self.assertFalse(frozen(strata_frozen=True, source_length_cutpoints=[2, 20]))
        self.assertTrue(frozen(strata_frozen=True, **cuts))


if __name__ == "__main__":
    unittest.main()
