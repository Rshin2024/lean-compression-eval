import unittest

from lean_compression.dataset import DOMAINS, sha256_text, validate_manifest, validate_references


def task(name, domain=DOMAINS[0], split="pilot", origin="fresh", **changes):
    value = dict(task_id=name, family_id="family_" + name, domain=domain, split=split, origin=origin,
                 stratum="provisional", imports=["Mathlib"], context="", binders=f"(x_{name} : Nat)",
                 proposition=f"x_{name} + 0 = x_{name}", target_name="Eval." + name,
                 premise_context="", forbidden_declarations=[])
    if origin == "library":
        value.update(provenance={"module": "Mathlib.Source." + name, "declaration": "source_" + name},
                     forbidden_declarations=["source_" + name])
    value.update(changes)
    value["hashes"] = {field: sha256_text(value[field])
                       for field in ("context", "premise_context", "binders", "proposition")}
    return value


def design():
    """Per domain: 10 pilot goals and 40 main goals, 30 library and 10 fresh."""
    cells = [("pilot", "fresh")] * 10 + [("main", "library")] * 30 + [("main", "fresh")] * 10
    return [task(f"d{d}_{index}", domain, split, origin)
            for d, domain in enumerate(DOMAINS) for index, (split, origin) in enumerate(cells)]


def quota_errors(tasks):
    return validate_manifest(tasks, enforce_quotas=True)


class QuotaTests(unittest.TestCase):
    def test_complete_preregistered_design_satisfies_every_quota(self):
        tasks = design()
        self.assertEqual(len(tasks), 250)
        self.assertEqual(quota_errors(tasks), [])

    def test_one_task_out_of_place_breaks_exactly_its_cells(self):
        tasks = design()
        next(t for t in tasks if t["split"] == "pilot" and t["domain"] == DOMAINS[0])["domain"] = DOMAINS[1]
        self.assertCountEqual(quota_errors(tasks), [f"pilot/{DOMAINS[0]}: expected 10, found 9",
                                                    f"pilot/{DOMAINS[1]}: expected 10, found 11"])
        tasks = design()
        next(t for t in tasks if t["origin"] == "library" and t["domain"] == DOMAINS[2])["origin"] = "fresh"
        self.assertCountEqual(quota_errors(tasks), [f"main/{DOMAINS[2]}/library: expected 30, found 29",
                                                    f"main/{DOMAINS[2]}/fresh: expected 10, found 11"])
        tasks = design()
        tasks.remove(next(t for t in tasks if t["split"] == "main" and t["origin"] == "fresh"
                          and t["domain"] == DOMAINS[4]))
        self.assertCountEqual(quota_errors(tasks), [f"main/{DOMAINS[4]}: expected 40, found 39",
                                                    f"main/{DOMAINS[4]}/fresh: expected 10, found 9",
                                                    "complete manifest: expected 250 tasks, found 249"])

    def test_pilot_subset_is_structurally_valid_but_not_the_complete_design(self):
        pilot = [t for t in design() if t["split"] == "pilot"]
        self.assertEqual(validate_manifest(pilot), [])
        self.assertIn("complete manifest: expected 250 tasks, found 50", quota_errors(pilot))


class StructuralRuleTests(unittest.TestCase):
    def test_each_structural_rule_rejects_its_defect(self):
        first = task("a")
        self.assertEqual(validate_manifest([first, task("b")]), [])
        cases = {"b: duplicate target_name": task("b", target_name=first["target_name"]),
                 "b: exact source signature duplicate of a": task(
                     "b", binders=" " + first["binders"], proposition=first["proposition"] + " "),
                 "b: invalid module name": task("b", imports=["Mathlib Logic"]),
                 "b: split must be pilot or main": task("b", split="test"),
                 "b: private reference proof in public manifest": task("b", reference_proof="by simp")}
        for message, second in cases.items():
            with self.subTest(message):
                self.assertIn(message, validate_manifest([first, second]))
        self.assertIn("b: private reference proof in public manifest",
                      validate_manifest([first, task("b", private_reference="by simp")]))

    def test_each_task_has_exactly_one_private_reference(self):
        tasks = [task("a")]
        self.assertEqual(validate_references(tasks, [{"task_id": "a", "proof": "by simp"}]), [])
        self.assertEqual(validate_references(tasks, [{"task_id": "a", "proof": "by simp"},
                                                     {"task_id": "a", "proof": "by omega"}]),
                         ["a: expected one private reference, found 2"])


if __name__ == "__main__":
    unittest.main()
