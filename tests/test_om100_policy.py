"""Explicit project-module expansion for the selected exploratory question set."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from lean_compression.dataset import DOMAINS, SUPPORTED_DOMAINS, readiness_errors, validate_manifest
from lean_compression.integrity import boundary_expansion_modules, require_completed_pilot, require_implemented_policy
from lean_compression.io import digest, measurement_hash, read_json
from lean_compression.runner import evaluate_proof


PROJECT = Path(__file__).resolve().parents[1]
ENGINE = PROJECT / "lean/Evaluator.lean"
LEGACY = "expand_mathlib_and_evaluator_v1"
DESCRIPTOR = {"name": "expand_mathlib_and_evaluator_plus_modules_v2",
              "extra_modules": ["OAI.Combinatorics.Sensitivity.Composition", "OAI.Probability.Basic"]}


def task(domain="combinatorics", index=0):
    return {"task_id": f"om100_{index}", "family_id": f"family_{index}", "domain": domain,
            "split": "main", "origin": "library", "stratum": "candidate",
            "imports": ["Mathlib"], "context": "", "binders": f"(P_{index} : Prop) (h : P_{index})",
            "proposition": f"P_{index}", "target_name": f"evaluation_target_{index}",
            "premise_context": "", "forbidden_declarations": []}


class ExpandedBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.config = read_json(PROJECT / "configs/development.json")
        self.config["environment"]["boundary_policy"] = copy.deepcopy(DESCRIPTOR)

    def test_legacy_and_explicit_module_policy_are_accepted(self):
        require_implemented_policy(self.config)
        modules = boundary_expansion_modules(self.config["environment"]["boundary_policy"])
        self.assertEqual(modules, DESCRIPTOR["extra_modules"])
        modules.append("OAI.Unrelated")
        self.assertEqual(self.config["environment"]["boundary_policy"], DESCRIPTOR)
        self.config["environment"]["boundary_policy"] = LEGACY
        require_implemented_policy(self.config)
        self.assertIsNone(boundary_expansion_modules(LEGACY))

    def test_malformed_or_ambiguous_boundaries_are_rejected(self):
        invalid = [None, [], {}, "expand_all_packages_v9", DESCRIPTOR["name"],
                   {"name": DESCRIPTOR["name"]}, {"extra_modules": DESCRIPTOR["extra_modules"]},
                   {**DESCRIPTOR, "name": LEGACY}, {**DESCRIPTOR, "engine_change": True}]
        invalid += [{**DESCRIPTOR, "extra_modules": value} for value in (
            None, [], (), "OAI.Probability.Basic", [None], [1], [True], [["OAI.Basic"]],
            ["OAI.B", "OAI.A"], ["OAI.A", "OAI.A"], [""], ["OAI.*"], ["OAI..Basic"],
            [".OAI.Basic"], ["OAI.Basic."], ["OAI.1Basic"], ["OAI Basic"], ["OAI/Basic"],
            ["OAI.Basic\n"], ["OAI.Basic; import Secret"]) ]
        for value in invalid:
            with self.subTest(policy=value):
                self.config["environment"]["boundary_policy"] = value
                with self.assertRaisesRegex(ValueError, "environment.boundary_policy"):
                    require_implemented_policy(self.config)

    def test_other_policy_requirements_are_unchanged(self):
        for section, key in (("environment", "source_token_policy"),
                             ("extraction", "unknown_dependency"), ("extraction", "dependency_cycle")):
            changed = copy.deepcopy(self.config)
            changed[section][key] = "unsupported"
            with self.subTest(field=key), self.assertRaisesRegex(ValueError, f"{section}.{key}"):
                require_implemented_policy(changed)

    def test_frozen_module_list_reaches_validation_and_extraction(self):
        for policy in (LEGACY, DESCRIPTOR):
            with self.subTest(policy=policy):
                self.config["environment"]["boundary_policy"] = copy.deepcopy(policy)
                target = task()
                # Public task metadata cannot override the frozen experiment boundary.
                target["expand_modules"] = ["Unrequested.Module"]
                seen = []

                def engine(request, environment, source, validation, *, mode, wall_seconds):
                    seen.append((mode, copy.deepcopy(request)))
                    if mode == "validate":
                        return {"validity_status": "success", "metric_status": "not_requested"}
                    return {"validity_status": "success", "metric_status": "available",
                            "root": "root", "source_tokens": {"W_S": 4, "W_B": 2},
                            "nodes": [{"name": "root", "primitive": False,
                                       "signature": {"atom": 1}, "body": {"atom": 2}},
                                      {"name": "atom", "primitive": True}]}

                before = copy.deepcopy(self.config)
                with patch("lean_compression.runner.execute_engine", side_effect=engine):
                    result = evaluate_proof(target, "by exact h", {"environment_hash": "fixture"},
                                            ENGINE, self.config)
                self.assertEqual([mode for mode, _ in seen], ["validate", "extract"])
                self.assertEqual(seen[0][1], seen[1][1])
                for _, request in seen:
                    if policy == LEGACY:
                        self.assertNotIn("expand_modules", request)
                    else:
                        self.assertEqual(request["expand_modules"], DESCRIPTOR["extra_modules"])
                self.assertEqual(result["validity_status"], "success")
                self.assertEqual(result["metric_status"], "available")
                self.assertEqual(result["boundary_policy"], policy)
                self.assertEqual(result["boundary_policy_hash"], digest(policy))
                self.assertEqual(self.config, before)

    def test_runner_rejects_bad_boundary_before_running_the_engine(self):
        self.config["environment"]["boundary_policy"]["extra_modules"] = ["OAI.*"]
        with patch("lean_compression.runner.execute_engine") as engine:
            with self.assertRaisesRegex(ValueError, "environment.boundary_policy"):
                evaluate_proof(task(), "by exact h", {"environment_hash": "fixture"}, ENGINE, self.config)
        engine.assert_not_called()

    def test_measurement_identity_binds_the_whole_module_list(self):
        changed = copy.deepcopy(self.config)
        changed["environment"]["boundary_policy"]["extra_modules"].append("OAI.ZAdditional")
        require_implemented_policy(changed)
        self.assertNotEqual(measurement_hash(self.config), measurement_hash(changed))


class ExploratoryDomainTests(unittest.TestCase):
    def test_selected_domains_are_valid_without_changing_legacy_quota_domains(self):
        self.assertEqual(DOMAINS, ("algebra_groups", "number_theory", "analysis_inequalities", "sets_logic",
                                  "linear_algebra_combinatorics"))
        for domain in ("algebra", "analysis", "combinatorics", "cs_probability"):
            with self.subTest(domain=domain):
                self.assertIn(domain, SUPPORTED_DOMAINS)
                self.assertEqual(validate_manifest([task(domain)]), [])
        self.assertIn("om100_0: unknown domain", validate_manifest([task("not_a_domain")]))

    def test_hundred_question_manifest_does_not_satisfy_preregistered_quotas(self):
        domains = ("algebra", "analysis", "combinatorics", "cs_probability")
        tasks = [task(domains[index % len(domains)], index) for index in range(100)]
        self.assertEqual(validate_manifest(tasks), [])
        errors = validate_manifest(tasks, enforce_quotas=True)
        self.assertIn("complete manifest: expected 250 tasks, found 100", errors)
        for domain in DOMAINS:
            self.assertIn(f"pilot/{domain}: expected 10, found 0", errors)
            self.assertIn(f"main/{domain}: expected 40, found 0", errors)
        self.assertEqual(len(readiness_errors(tasks)), 500)

    def test_new_boundary_does_not_waive_completed_pilot_requirement(self):
        config = read_json(PROJECT / "configs/development.json")
        config["environment"]["boundary_policy"] = DESCRIPTOR
        with self.assertRaisesRegex(ValueError, "completed pilot"):
            require_completed_pilot(config, None, "fixture")


if __name__ == "__main__":
    unittest.main()
